import re

from .logger import log
from .utils import format_response, parse_body

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


# Public, no auth — same trust tier as /submit-group. Anonymous analytics
# ping only: nothing is written to DynamoDB, the only thing that happens is
# a log line with the computed score/success for later review. The client
# only ever sends the raw guess sequence (as plain 1-4 difficulty integers,
# not words/categories/identity) and the puzzle's date — score and success
# are computed here and never sent back in the response.
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
    log({"date": date, "attempts": attempts, "score": score, "success": success})

    return format_response(event=event, http_code=200, body={"status": "ok"}, log_this=False)
