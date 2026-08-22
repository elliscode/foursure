import json
import random
from datetime import datetime, timedelta, timezone

from .logger import log
from .utils import (
    PUZZLE_BUCKET_NAME,
    TABLE_NAME,
    decimal_to_number,
    dynamo,
    dynamo_obj_to_python_obj,
    python_obj_to_dynamo_obj,
    s3,
)

DIFFICULTIES = [1, 2, 3, 4]
PUZZLE_KEY_PREFIX = "puzzles/"
MANIFEST_KEY = "puzzles/manifest.json"


def _query_available_groups():
    result = dynamo.query(
        TableName=TABLE_NAME,
        KeyConditionExpression="#key1 = :key1",
        FilterExpression="#approved = :true AND (attribute_not_exists(#used) OR #used = :false)",
        ExpressionAttributeNames={"#key1": "key1", "#approved": "approved", "#used": "used_in_puzzle"},
        ExpressionAttributeValues=python_obj_to_dynamo_obj(
            {":key1": "group", ":true": True, ":false": False}
        ),
    )
    return [decimal_to_number(dynamo_obj_to_python_obj(item)) for item in result.get("Items", [])]


def _mark_used(group_id, target_date, difficulty):
    dynamo.update_item(
        TableName=TABLE_NAME,
        Key=python_obj_to_dynamo_obj({"key1": "group", "key2": group_id}),
        UpdateExpression="SET #used = :true, #puzzleDate = :puzzleDate, #difficulty = :difficulty",
        ExpressionAttributeNames={
            "#used": "used_in_puzzle",
            "#puzzleDate": "puzzleDate",
            "#difficulty": "difficulty",
        },
        ExpressionAttributeValues=python_obj_to_dynamo_obj(
            {":true": True, ":puzzleDate": target_date, ":difficulty": difficulty}
        ),
    )


def _update_manifest(target_date):
    try:
        result = s3.get_object(Bucket=PUZZLE_BUCKET_NAME, Key=MANIFEST_KEY)
        manifest = json.loads(result["Body"].read())
    except s3.exceptions.NoSuchKey:
        manifest = []
    if target_date not in manifest:
        manifest.append(target_date)
    manifest.sort()
    s3.put_object(
        Bucket=PUZZLE_BUCKET_NAME,
        Key=MANIFEST_KEY,
        Body=json.dumps(manifest),
        ContentType="application/json",
    )


# Invoked directly from lambda_function.py's lambda_handler when the event
# came from the EventBridge rule the developer creates (see backend/README.md)
# rather than through the normal API-Gateway path dispatch below. Picks 4
# random approved-and-not-yet-used groups, assigns each a random unique
# difficulty (1-4, colored client-side per README's difficulty palette),
# marks them used so a future run never reuses them, and writes both the
# puzzle file and the updated manifest straight to S3 — the frontend's
# calendar picker reads the manifest to know which dates are playable.
def generate_puzzle():
    pool = _query_available_groups()
    if len(pool) < 4:
        log(f"Not enough approved, unused groups to generate a puzzle (found {len(pool)}, need 4)")
        return {"generated": False, "reason": "not enough approved unused groups"}

    chosen = random.sample(pool, 4)
    difficulties = list(DIFFICULTIES)
    random.shuffle(difficulties)
    target_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

    groups_payload = []
    for group, difficulty in zip(chosen, difficulties):
        _mark_used(group["key2"], target_date, difficulty)
        groups_payload.append({"category": group["category"], "words": group["words"], "difficulty": difficulty})
    groups_payload.sort(key=lambda g: g["difficulty"])

    puzzle = {"date": target_date, "groups": groups_payload}
    s3.put_object(
        Bucket=PUZZLE_BUCKET_NAME,
        Key=f"{PUZZLE_KEY_PREFIX}{target_date}.json",
        Body=json.dumps(puzzle),
        ContentType="application/json",
    )
    _update_manifest(target_date)

    log(f"Generated puzzle for {target_date}")
    return {"generated": True, "date": target_date}
