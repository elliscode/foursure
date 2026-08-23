import time
import uuid

from .utils import (
    TABLE_NAME,
    authenticate,
    decimal_to_number,
    dynamo,
    dynamo_obj_to_python_obj,
    format_response,
    python_obj_to_dynamo_obj,
)

MAX_WORD_LENGTH = 40
MAX_CATEGORY_LENGTH = 200

STATUS_FILTERS = {
    "pending": ("attribute_not_exists(#approved)", {"#approved": "approved"}, {}),
    "approved": ("#approved = :true", {"#approved": "approved"}, {":true": True}),
    "rejected": ("#approved = :false", {"#approved": "approved"}, {":false": False}),
    "all": (None, {}, {}),
    # Approved and not yet locked into a constructed puzzle — what
    # admin.html's "Build Puzzle" tab offers as addable. Same filter
    # fourplay/puzzle.py's old group-sampling generate_puzzle() used.
    "approved-unused": (
        "#approved = :true AND (attribute_not_exists(#used) OR #used = :false)",
        {"#approved": "approved", "#used": "used_in_puzzle"},
        {":true": True, ":false": False},
    ),
}


def _query_groups(filter_expression, expr_names=None, expr_values=None):
    names = {"#key1": "key1"}
    names.update(expr_names or {})
    values = {":key1": "group"}
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


def _get_group(group_id):
    result = dynamo.get_item(
        Key=python_obj_to_dynamo_obj({"key1": "group", "key2": group_id}),
        TableName=TABLE_NAME,
    )
    if "Item" not in result:
        return None
    return decimal_to_number(dynamo_obj_to_python_obj(result["Item"]))


@authenticate
def list_groups_route(event, admin_phone, body):
    status = body.get("status") or "pending"
    if status not in STATUS_FILTERS:
        return format_response(
            event=event, http_code=400, body="status must be one of pending/approved/rejected/all/approved-unused"
        )

    filter_expression, expr_names, expr_values = STATUS_FILTERS[status]
    groups = _query_groups(filter_expression, expr_names, expr_values)
    return format_response(event=event, http_code=200, body={"groups": groups}, log_this=False)


# Approve or reject an already-submitted group. Never deletes the record —
# "approved" just flips to true/false and stays that way, same non-destructive
# moderation shape as kaios-calorie-counter's review_route.
@authenticate
def decide_group_route(event, admin_phone, body):
    group_id = str(body.get("id") or "").strip()
    approved = body.get("approved")

    if not group_id:
        return format_response(event=event, http_code=400, body="A valid id is required")
    if not isinstance(approved, bool):
        return format_response(event=event, http_code=400, body="approved (true/false) is required")
    if _get_group(group_id) is None:
        return format_response(event=event, http_code=404, body="No group found with that id")

    dynamo.update_item(
        TableName=TABLE_NAME,
        Key=python_obj_to_dynamo_obj({"key1": "group", "key2": group_id}),
        UpdateExpression="SET #approved = :approved, #reviewedAt = :reviewedAt",
        ExpressionAttributeNames={"#approved": "approved", "#reviewedAt": "reviewedAt"},
        ExpressionAttributeValues=python_obj_to_dynamo_obj({":approved": approved, ":reviewedAt": int(time.time())}),
    )

    return format_response(event=event, http_code=200, body={"id": group_id, "approved": approved})


# Editing a group is modeled as reject-the-original + submit-a-new-already-
# approved-replacement, rather than an in-place update — so the original
# submission stays in the table exactly as it was submitted (rejected, not
# deleted), and the edited version is a brand new record from the start,
# linked back via sourceGroupId purely for admin-page display.
@authenticate
def edit_group_route(event, admin_phone, body):
    group_id = str(body.get("id") or "").strip()
    words = body.get("words")
    category = str(body.get("category") or "").strip()

    if not group_id:
        return format_response(event=event, http_code=400, body="A valid id is required")
    if _get_group(group_id) is None:
        return format_response(event=event, http_code=404, body="No group found with that id")
    if not isinstance(words, list) or len(words) != 4:
        return format_response(event=event, http_code=400, body="Exactly 4 words are required")

    cleaned_words = [str(word).strip() for word in words]
    if any(not word or len(word) > MAX_WORD_LENGTH for word in cleaned_words):
        return format_response(
            event=event,
            http_code=400,
            body=f"Each word must be non-empty and {MAX_WORD_LENGTH} characters or fewer",
        )
    if not category or len(category) > MAX_CATEGORY_LENGTH:
        return format_response(
            event=event,
            http_code=400,
            body=f"A category connecting the four words is required ({MAX_CATEGORY_LENGTH} characters or fewer)",
        )

    now = int(time.time())

    dynamo.update_item(
        TableName=TABLE_NAME,
        Key=python_obj_to_dynamo_obj({"key1": "group", "key2": group_id}),
        UpdateExpression="SET #approved = :false, #reviewedAt = :reviewedAt",
        ExpressionAttributeNames={"#approved": "approved", "#reviewedAt": "reviewedAt"},
        ExpressionAttributeValues=python_obj_to_dynamo_obj({":false": False, ":reviewedAt": now}),
    )

    new_group_id = str(uuid.uuid4())
    dynamo.put_item(
        TableName=TABLE_NAME,
        Item=python_obj_to_dynamo_obj(
            {
                "key1": "group",
                "key2": new_group_id,
                "words": cleaned_words,
                "category": category,
                "submittedAt": now,
                "approved": True,
                "reviewedAt": now,
                "sourceGroupId": group_id,
            }
        ),
    )

    return format_response(event=event, http_code=200, body={"id": new_group_id, "sourceGroupId": group_id})
