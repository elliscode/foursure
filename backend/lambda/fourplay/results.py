import json
import re
from datetime import datetime

from .logger import log
from .utils import (
    PUZZLE_BUCKET_NAME,
    TABLE_NAME,
    decimal_to_number,
    dynamo,
    dynamo_obj_to_python_obj,
    format_response,
    parse_body,
    python_obj_to_dynamo_obj,
    s3,
)

DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The game ends on the 4th correct or the 4th wrong guess, so the longest
# possible game is 7 guesses (3 wrong + 4 correct, or 4 wrong + 3 correct).
MAX_GUESSES = 7

# date -> (puzzle, key). Warm-invocation cache only; a date's canonical
# puzzle never changes once its dated file exists, and dated files go up
# ~23h before anyone can reach that date (see DEPLOYMENT_STEPS.md step 7).
_canonical_cache = {}


# Python port of defaultPuzzleKeyFor() in frontend/js/puzzle.js -- must stay
# identical, since it decides which fallback puzzle the client showed for a
# date with no dated file. tm_yday is 1-indexed, same as the JS dayOfYear.
def _default_puzzle_key_for(date):
    day_of_year = datetime.strptime(date, "%Y-%m-%d").timetuple().tm_yday
    return f"puzzles/default{day_of_year % 8 + 1:03d}.json"


# Same rule as the client's getThePuzzle(): the dated file if one exists,
# otherwise that date's deterministic default00N.json fallback.
def _get_canonical_puzzle(date):
    if date in _canonical_cache:
        return _canonical_cache[date]
    key = f"puzzles/{date}.json"
    try:
        obj = s3.get_object(Bucket=PUZZLE_BUCKET_NAME, Key=key)
    except s3.exceptions.NoSuchKey:
        key = _default_puzzle_key_for(date)
        obj = s3.get_object(Bucket=PUZZLE_BUCKET_NAME, Key=key)
    result = (json.loads(obj["Body"].read()), key)
    _canonical_cache[date] = result
    return result


def _normalize_word(word):
    return word.strip().upper()


# Maps each guessed word to its group's difficulty, mirroring the client's
# checkGuess(): groups are stored sorted by difficulty and the client uses
# the group's index (DIFFICULTY_CLASSES[i]), so index + 1 here. Returns None
# if any word isn't one of this puzzle's 16 -- i.e. the client played a
# different puzzle than the canonical one.
def _guesses_to_attempts(guesses, puzzle):
    difficulty_by_word = {}
    for i, group in enumerate(puzzle["groups"]):
        for word in group["words"]:
            difficulty_by_word[_normalize_word(word)] = i + 1
    attempts = []
    for guess in guesses:
        attempt = [difficulty_by_word.get(_normalize_word(word)) for word in guess]
        if None in attempt:
            return None
        attempts.append(attempt)
    return attempts


def _valid_guesses(guesses):
    if not isinstance(guesses, list) or not guesses or len(guesses) > MAX_GUESSES:
        return False
    for guess in guesses:
        if not isinstance(guess, list) or len(guess) != 4:
            return False
        if any(not isinstance(word, str) or not word.strip() for word in guess):
            return False
        if len({_normalize_word(word) for word in guess}) != 4:
            return False
    return True


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


# Public, no auth — same trust tier as /submit-group. The client sends the
# puzzle's date plus its guess sequence, in one of two shapes:
#   - guesses: the actual words of each guess (current clients). Checked
#     against the canonical puzzle for that date (_get_canonical_puzzle());
#     a match is scored from the canonical groups and recorded, a mismatch
#     (the client was served some other puzzle, e.g. a bad CloudFront cache
#     handing out a default00N.json) is logged as PUZZLE_MISMATCH -- the
#     string the CloudWatch alarm's metric filter matches -- and not
#     recorded. Still a 200 either way; the client is fire-and-forget.
#   - attempts: plain 1-4 difficulty integers (KaiStore builds from before
#     guesses existed, which stay installed indefinitely). Nothing to verify
#     against, so scored and recorded as-is.
# Score and success are never sent back in the response. Anonymous even in
# storage: _record_completion() only ever increments a per-date-and-score
# counter, nothing identifying the submitter is written anywhere.
def submit_result_route(event):
    body = parse_body(event.get("body"))
    date = str(body.get("date") or "").strip()
    guesses = body.get("guesses")
    attempts = body.get("attempts")

    if not DATE_REGEX.match(date):
        return format_response(event=event, http_code=400, body="A valid date (YYYY-MM-DD) is required")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return format_response(event=event, http_code=400, body="A valid date (YYYY-MM-DD) is required")

    if guesses is not None:
        if not _valid_guesses(guesses):
            return format_response(
                event=event,
                http_code=400,
                body=f"guesses must be 1-{MAX_GUESSES} lists of 4 distinct non-empty words",
            )
        puzzle, canonical_key = _get_canonical_puzzle(date)
        attempts = _guesses_to_attempts(guesses, puzzle)
        if attempts is None:
            log("ERROR: PUZZLE_MISMATCH", {"date": date, "canonical_key": canonical_key, "guesses": guesses})
            return format_response(event=event, http_code=200, body={"status": "ok"}, log_this=False)
        score, success = _score_attempts(attempts)
        _record_completion(date, score)
        log(
            {
                "date": date,
                "canonical_key": canonical_key,
                "guesses": guesses,
                "attempts": attempts,
                "score": score,
                "success": success,
            }
        )
        return format_response(event=event, http_code=200, body={"status": "ok"}, log_this=False)

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
