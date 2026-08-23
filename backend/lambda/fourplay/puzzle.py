import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from .logger import log
from .utils import (
    PUZZLE_BUCKET_NAME,
    TABLE_NAME,
    authenticate,
    decimal_to_number,
    dynamo,
    dynamo_obj_to_python_obj,
    format_response,
    python_obj_to_dynamo_obj,
    s3,
)

DIFFICULTIES = [1, 2, 3, 4]
PUZZLE_KEY_PREFIX = "puzzles/"
# Deliberately excludes the frontend's default00N.json fallback puzzles
# (and anything else non-date-shaped) — see _next_puzzle_id() for why that
# matters.
DATED_PUZZLE_KEY_PATTERN = re.compile(r"^puzzles/\d{4}-\d{2}-\d{2}\.json$")

PUZZLE_STATUS_FILTERS = {
    "unused": ("attribute_not_exists(#used) OR #used = :false", {"#used": "used"}, {":false": False}),
    "used": ("#used = :true", {"#used": "used"}, {":true": True}),
    "all": (None, {}, {}),
}


def _get_group(group_id):
    result = dynamo.get_item(
        Key=python_obj_to_dynamo_obj({"key1": "group", "key2": group_id}),
        TableName=TABLE_NAME,
    )
    if "Item" not in result:
        return None
    return decimal_to_number(dynamo_obj_to_python_obj(result["Item"]))


def _query_puzzles(filter_expression, expr_names=None, expr_values=None):
    names = {"#key1": "key1"}
    names.update(expr_names or {})
    values = {":key1": "puzzle"}
    values.update(expr_values or {})
    kwargs = {
        "TableName": TABLE_NAME,
        "KeyConditionExpression": "#key1 = :key1",
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": python_obj_to_dynamo_obj(values),
    }
    if filter_expression:
        kwargs["FilterExpression"] = filter_expression
    result = dynamo.query(**kwargs)
    return [decimal_to_number(dynamo_obj_to_python_obj(item)) for item in result.get("Items", [])]


# Builds a whole puzzle from 4 admin-picked approved groups, each given a
# difficulty — this is what actually "uses up" a group (used_in_puzzle is
# set here, at construction time, not later when generate_puzzle() publishes
# it). category/words are denormalized from each group onto the puzzle
# record itself: groups are otherwise immutable once approved, so this is
# safe, and it means generate_puzzle() never needs a group lookup at publish
# time — it just writes this list straight to S3.
@authenticate
def create_puzzle_route(event, admin_phone, body):
    entries = body.get("groups")
    if not isinstance(entries, list) or len(entries) != 4:
        return format_response(event=event, http_code=400, body="Exactly 4 groups are required")

    group_ids = [str(e.get("groupId") or "").strip() for e in entries if isinstance(e, dict)]
    difficulties = [e.get("difficulty") for e in entries if isinstance(e, dict)]
    if len(group_ids) != 4 or any(not g for g in group_ids):
        return format_response(event=event, http_code=400, body="Each entry needs a groupId")
    if len(set(group_ids)) != 4:
        return format_response(event=event, http_code=400, body="Groups must be 4 distinct groups")
    # A real puzzle needs exactly one group per difficulty level — the
    # frontend's difficulty-to-color mapping (DIFFICULTY_CLASSES in
    # puzzle.js) depends on this holding.
    if sorted(difficulties) != DIFFICULTIES:
        return format_response(
            event=event, http_code=400, body="Difficulties must be exactly 1, 2, 3, and 4, one each"
        )

    groups = []
    for group_id, difficulty in zip(group_ids, difficulties):
        group = _get_group(group_id)
        if group is None:
            return format_response(event=event, http_code=404, body=f"No group found with id {group_id}")
        if not group.get("approved"):
            return format_response(event=event, http_code=400, body=f"Group {group_id} is not approved")
        if group.get("used_in_puzzle"):
            return format_response(
                event=event, http_code=400, body=f"Group {group_id} is already used in another puzzle"
            )
        groups.append(
            {"groupId": group_id, "category": group["category"], "words": group["words"], "difficulty": difficulty}
        )

    puzzle_id = str(uuid.uuid4())
    dynamo.put_item(
        TableName=TABLE_NAME,
        Item=python_obj_to_dynamo_obj(
            {"key1": "puzzle", "key2": puzzle_id, "groups": groups, "createdAt": int(time.time())}
        ),
    )
    for group_id in group_ids:
        dynamo.update_item(
            TableName=TABLE_NAME,
            Key=python_obj_to_dynamo_obj({"key1": "group", "key2": group_id}),
            UpdateExpression="SET #used = :true",
            ExpressionAttributeNames={"#used": "used_in_puzzle"},
            ExpressionAttributeValues=python_obj_to_dynamo_obj({":true": True}),
        )

    return format_response(event=event, http_code=200, body={"id": puzzle_id})


@authenticate
def list_puzzles_route(event, admin_phone, body):
    status = body.get("status") or "unused"
    if status not in PUZZLE_STATUS_FILTERS:
        return format_response(event=event, http_code=400, body="status must be one of unused/used/all")

    filter_expression, expr_names, expr_values = PUZZLE_STATUS_FILTERS[status]
    puzzles = _query_puzzles(filter_expression, expr_names, expr_values)
    # DynamoDB returns Query results ordered by key2 (a UUID), not
    # createdAt — sort client-side so the "Build Puzzle" tab's queue view
    # (and generate_puzzle()'s FIFO pick below) both see oldest-first.
    puzzles.sort(key=lambda p: p.get("createdAt", 0))
    return format_response(event=event, http_code=200, body={"puzzles": puzzles}, log_this=False)


# Only ever deletes a puzzle that hasn't been published yet — a constructed-
# but-unused puzzle is disposable draft data, unlike groups (never deleted,
# just flagged) or a published puzzle (kept forever as a history record of
# what ran on which date).
@authenticate
def delete_puzzle_route(event, admin_phone, body):
    puzzle_id = str(body.get("id") or "").strip()
    if not puzzle_id:
        return format_response(event=event, http_code=400, body="A valid id is required")

    result = dynamo.get_item(
        Key=python_obj_to_dynamo_obj({"key1": "puzzle", "key2": puzzle_id}),
        TableName=TABLE_NAME,
    )
    if "Item" not in result:
        return format_response(event=event, http_code=404, body="No puzzle found with that id")
    puzzle = decimal_to_number(dynamo_obj_to_python_obj(result["Item"]))
    if puzzle.get("used"):
        return format_response(event=event, http_code=400, body="Cannot delete an already-published puzzle")

    # Frees every group this puzzle held back up for construction again —
    # REMOVE rather than SET ... false, so "available" goes back to meaning
    # exactly what it means for a group that was never used at all
    # (attribute absent), the same convention used everywhere else.
    for group in puzzle["groups"]:
        dynamo.update_item(
            TableName=TABLE_NAME,
            Key=python_obj_to_dynamo_obj({"key1": "group", "key2": group["groupId"]}),
            UpdateExpression="REMOVE #used",
            ExpressionAttributeNames={"#used": "used_in_puzzle"},
        )

    dynamo.delete_item(
        Key=python_obj_to_dynamo_obj({"key1": "puzzle", "key2": puzzle_id}),
        TableName=TABLE_NAME,
    )

    return format_response(event=event, http_code=200, body={"id": puzzle_id})


# Every puzzle file (the frontend's default00N.json fallbacks included)
# carries its own "id" — real ones get theirs assigned here, once, at
# publish time: read whatever the most recently published puzzle's id was
# and add 1. No DynamoDB counter or similar needed since S3 already gives us
# an ordered listing to read back.
def _next_puzzle_id():
    result = s3.list_objects_v2(Bucket=PUZZLE_BUCKET_NAME, Prefix=PUZZLE_KEY_PREFIX)
    # list_objects_v2 already returns keys in ascending lexicographic order,
    # which sorts YYYY-MM-DD.json filenames chronologically too — but
    # default00N.json sorts *after* every dated key ("d" > any digit), so
    # those have to be filtered out explicitly first, or the "last" key
    # would always be one of them and numbering would freeze at 1 forever.
    dated_keys = sorted(
        obj["Key"] for obj in result.get("Contents", []) if DATED_PUZZLE_KEY_PATTERN.match(obj["Key"])
    )
    if not dated_keys:
        return 1  # first-ever real publish — the default00N.json files all carry id 0
    last = s3.get_object(Bucket=PUZZLE_BUCKET_NAME, Key=dated_keys[-1])
    last_puzzle = json.loads(last["Body"].read())
    return int(last_puzzle.get("id", 0)) + 1


# Invoked directly from lambda_function.py's lambda_handler when the event
# came from the EventBridge rule the developer creates (see
# backend/README.md) rather than through the normal API-Gateway path
# dispatch below. Doesn't build a puzzle itself anymore — admins build whole
# puzzles ahead of time via create_puzzle_route above (picking 4 approved
# groups and assigning each a difficulty in admin.html's "Build Puzzle"
# tab); this just takes the oldest not-yet-published one out of that queue
# (FIFO — the oldest-constructed puzzle publishes first, so nothing sits in
# the queue indefinitely while newer ones keep getting picked instead) and
# publishes it.
def generate_puzzle():
    filter_expression, expr_names, expr_values = PUZZLE_STATUS_FILTERS["unused"]
    pool = _query_puzzles(filter_expression, expr_names, expr_values)
    if not pool:
        log("No unused constructed puzzles available to publish")
        return {"generated": False, "reason": "no constructed puzzles available"}

    pool.sort(key=lambda p: p.get("createdAt", 0))
    chosen = pool[0]
    target_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    puzzle_number = _next_puzzle_id()

    groups_payload = sorted(
        [{"category": g["category"], "words": g["words"], "difficulty": g["difficulty"]} for g in chosen["groups"]],
        key=lambda g: g["difficulty"],
    )
    puzzle = {"id": puzzle_number, "date": target_date, "groups": groups_payload}
    s3.put_object(
        Bucket=PUZZLE_BUCKET_NAME,
        Key=f"{PUZZLE_KEY_PREFIX}{target_date}.json",
        Body=json.dumps(puzzle),
        ContentType="application/json",
    )

    dynamo.update_item(
        TableName=TABLE_NAME,
        Key=python_obj_to_dynamo_obj({"key1": "puzzle", "key2": chosen["key2"]}),
        UpdateExpression="SET #used = :true, #puzzleDate = :puzzleDate, #usedAt = :usedAt",
        ExpressionAttributeNames={"#used": "used", "#puzzleDate": "puzzleDate", "#usedAt": "usedAt"},
        ExpressionAttributeValues=python_obj_to_dynamo_obj(
            {":true": True, ":puzzleDate": target_date, ":usedAt": int(time.time())}
        ),
    )
    # used_in_puzzle is already true from construction time — this just
    # stamps *when* each group actually ran, same audit-trail purpose the
    # old group-level _mark_used() served.
    for group in chosen["groups"]:
        dynamo.update_item(
            TableName=TABLE_NAME,
            Key=python_obj_to_dynamo_obj({"key1": "group", "key2": group["groupId"]}),
            UpdateExpression="SET #puzzleDate = :puzzleDate",
            ExpressionAttributeNames={"#puzzleDate": "puzzleDate"},
            ExpressionAttributeValues=python_obj_to_dynamo_obj({":puzzleDate": target_date}),
        )

    log(f"Published puzzle {chosen['key2']} (id {puzzle_number}) for {target_date}")
    return {"generated": True, "date": target_date, "id": puzzle_number, "puzzleId": chosen["key2"]}
