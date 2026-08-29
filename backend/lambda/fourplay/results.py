import re

from .logger import log
from .utils import (
    TABLE_NAME,
    decimal_to_number,
    dynamo,
    dynamo_obj_to_python_obj,
    format_response,
    parse_body,
    python_obj_to_dynamo_obj,
)

DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# Score per attempt: all 4 slots the same difficulty (a correct guess) earns
# that difficulty's own value (1-4); any other combination (a wrong guess)
# costs 1. A perfect no-mistake win is 1+2+3+4 = 10; a straight 4-miss loss
# is -4. "Success" just means every difficulty 1-4 shows up as a correct
# guess somewhere in the sequence — the game only ever ends in "solved all
# 4" or "ran out of chances", so this is exactly "the puzzle was won".
def _score_attempts(attempts):
    score = 0
    correct_difficulties = set()
    for attempt in attempts:
        if len(set(attempt)) == 1:
            score += attempt[0]
            correct_difficulties.add(attempt[0])
        else:
            score -= 1
    success = correct_difficulties == {1, 2, 3, 4}
    return score, success


# One row per (date, score) pair rather than one row per date holding a
# results-map attribute -- ADD atomically creates the item *and*
# initializes the counter in a single call, with no existence-check needed
# for the very first completion of a new date (a nested-map-per-day design
# would need one, since incrementing a path inside a map requires the map
# to already exist). key1 is flat ("completions"), not composite per date
# -- same convention every other record type in this table already uses
# (puzzle/group/otp/token all share one key1, differentiated by key2), and
# it means a future Query(key1="completions") with no key2 condition
# returns every completion ever recorded in one call, already sorted by
# date. A future per-date reader would use begins_with(key2, f"{date}#").
def _record_completion(date, score):
    dynamo.update_item(
        TableName=TABLE_NAME,
        Key=python_obj_to_dynamo_obj({"key1": "completions", "key2": f"{date}#{score}"}),
        UpdateExpression="ADD #count :one",
        ExpressionAttributeNames={"#count": "count"},
        ExpressionAttributeValues=python_obj_to_dynamo_obj({":one": 1}),
    )


# Public, no auth — same trust tier as /submit-group. The client only ever
# sends the raw guess sequence (as plain 1-4 difficulty integers, not
# words/categories/identity) and the puzzle's date — score and success are
# computed here and never sent back in the response. Anonymous even in
# storage: _record_completion() only ever increments a per-date-and-score
# counter, nothing identifying the submitter is written anywhere.
def submit_result_route(event):
    body = parse_body(event.get("body"))
    date = str(body.get("date") or "").strip()
    attempts = body.get("attempts")

    if not DATE_REGEX.match(date):
        return format_response(event=event, http_code=400, body="A valid date (YYYY-MM-DD) is required")
    if not isinstance(attempts, list) or not attempts:
        return format_response(event=event, http_code=400, body="attempts must be a non-empty list")
    for attempt in attempts:
        if (
            not isinstance(attempt, list)
            or len(attempt) != 4
            or any(not isinstance(d, int) or isinstance(d, bool) or d not in (1, 2, 3, 4) for d in attempt)
        ):
            return format_response(
                event=event, http_code=400, body="Each attempt must be exactly 4 integers between 1 and 4"
            )

    score, success = _score_attempts(attempts)
    _record_completion(date, score)
    log({"date": date, "attempts": attempts, "score": score, "success": success})

    return format_response(event=event, http_code=200, body={"status": "ok"}, log_this=False)


# Public, no auth — same trust tier as /submit-group and /submit-result
# above, since this only ever returns aggregate counts, nothing identifying
# any submitter. Response is deliberately sparse (only scores that actually
# have a count) -- the fixed -4..10 axis and zero-filling any missing score
# is the blog page's job at render time, not this route's, since "always
# the same axis on every post" is a rendering concern, not a data one.
def get_completions_route(event):
    body = parse_body(event.get("body"))
    date = str(body.get("date") or "").strip()
    if not DATE_REGEX.match(date):
        return format_response(event=event, http_code=400, body="A valid date (YYYY-MM-DD) is required")

    result = dynamo.query(
        TableName=TABLE_NAME,
        KeyConditionExpression="#key1 = :key1 AND begins_with(#key2, :prefix)",
        ExpressionAttributeNames={"#key1": "key1", "#key2": "key2"},
        ExpressionAttributeValues=python_obj_to_dynamo_obj({":key1": "completions", ":prefix": f"{date}#"}),
    )
    counts = {}
    for item in result.get("Items", []):
        parsed = decimal_to_number(dynamo_obj_to_python_obj(item))
        score = parsed["key2"].split("#", 1)[1]  # "{date}#{score}" -> "{score}"
        counts[score] = parsed["count"]

    return format_response(event=event, http_code=200, body={"date": date, "counts": counts}, log_this=False)
