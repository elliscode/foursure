import time
import uuid

from .utils import (
    TABLE_NAME,
    dynamo,
    format_response,
    parse_body,
    python_obj_to_dynamo_obj,
)

MAX_WORD_LENGTH = 40
MAX_CATEGORY_LENGTH = 200


# Public, no login — anyone can propose a group of 4 words + the category
# connecting them. Lands as a plain pending record (no "approved" attribute
# at all yet) for an admin to review via admin.py's routes below.
def submit_group_route(event):
    body = parse_body(event.get("body"))
    words = body.get("words")
    category = str(body.get("category") or "").strip()

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

    group_id = str(uuid.uuid4())
    dynamo.put_item(
        TableName=TABLE_NAME,
        Item=python_obj_to_dynamo_obj(
            {
                "key1": "group",
                "key2": group_id,
                "words": cleaned_words,
                "category": category,
                "submittedAt": int(time.time()),
            }
        ),
    )

    return format_response(event=event, http_code=200, body={"id": group_id})
