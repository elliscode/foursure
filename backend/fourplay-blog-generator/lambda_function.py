import html
import json
import os
import re
import traceback
from datetime import datetime
from string import Template

import boto3
from google import genai
from google.genai import types

PUZZLE_BUCKET_NAME = os.environ.get("PUZZLE_BUCKET_NAME")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL_NAME = "gemini-3.7-flash"

SITE_URL = "https://fourplay.elliscode.com/"

PUZZLE_KEY_PREFIX = "puzzles/"
DATED_PUZZLE_KEY_PATTERN = re.compile(r"^puzzles/\d{4}-\d{2}-\d{2}\.json$")
BLOG_KEY_PREFIX = "blog/"
# "template.html" doesn't match \d{4}-\d{2}-\d{2}, so it's already excluded
# from both the "existing posts" set and the "missing dates" diff below —
# no special-casing needed to keep it out of either.
DATED_BLOG_KEY_PATTERN = re.compile(r"^blog/\d{4}-\d{2}-\d{2}\.html$")
TEMPLATE_KEY = "blog/template.html"
SITEMAP_KEY = "sitemap.xml"

s3 = boto3.client("s3")
client = genai.Client(api_key=GEMINI_API_KEY)

GROUP_SYSTEM_INSTRUCTION = (
    "You are writing short explanatory blurbs for a daily connections-style word "
    "puzzle called Fourplay. Each puzzle has four groups of four words; each group "
    "shares a hidden category and is ranked by difficulty from 1 (easiest) to 4 "
    "(hardest -- often wordplay, homophones, or misdirection-based). "
    "You are being shown exactly one group in isolation: its category, its four "
    "words, and where it ranks (1-4) in this puzzle's difficulty order. You do not "
    "know what the other three groups are, so do not speculate about their "
    "specific content -- but you may describe this group's own relative position "
    "(e.g. 'the easiest group today,' 'the hardest group of the day') using the "
    "difficulty rank you were given.\n\n"
    "Write 2 to 4 sentences explaining why these four words fit the category, in "
    "a confident, dry, slightly wry tone -- like a puzzle constructor explaining "
    "their own trick after the fact, not a cheerleader. Plain prose only: no "
    "markdown formatting of any kind -- no **bold**, no _italics_, no headers, no "
    "bullet points, no quotation marks around the words. Do not use asterisks or "
    "underscores for emphasis at all, even sparingly -- this text is inserted "
    "directly into an HTML <p> tag as plain text. Don't open with a "
    "restatement of the category as a label (e.g. don't start with 'This category "
    "is about...'); work it into the sentence naturally. It's fine to address the "
    "reader directly ('you') if it fits naturally."
)

WHY_SYSTEM_INSTRUCTION = (
    "You are writing the closing paragraph of a daily blog post that breaks down "
    "a four-group Fourplay word puzzle (a connections-style game). You will be "
    "given all four groups -- each with its category, its four words, and its "
    "difficulty rank (1 easiest to 4 hardest).\n\n"
    "Write a single paragraph, about 80 to 100 words, explaining why these four "
    "groups work together as a puzzle. Specifically call out, by name, any "
    "specific words from different groups that could plausibly be mistaken for "
    "belonging to another group (red herrings / overlaps) -- reference the actual "
    "words, not just the categories in the abstract. Discuss how the difficulty "
    "curve is constructed: why the easy group stays easy and the hard group "
    "stays hard despite that overlap. Tone: confident, analytical, a little wry "
    "-- like a puzzle constructor discussing their own design after the fact. "
    "Plain prose only: one paragraph, no markdown formatting of any kind -- no "
    "**bold**, no _italics_, no headers, no bullet points. Do not use asterisks "
    "or underscores for emphasis at all, even sparingly -- this text is inserted "
    "directly into an HTML <p> tag as plain text."
)


def _list_dated_dates(prefix, pattern, suffix):
    # Plain list_objects_v2, no paginator -- same convention as
    # backend/lambda/fourplay/puzzle.py's _next_puzzle_id(). Silently caps at
    # 1000 keys per prefix, which at one file/day is ~2.7 years of history;
    # a known, shared limitation, not unique to this Lambda.
    result = s3.list_objects_v2(Bucket=PUZZLE_BUCKET_NAME, Prefix=prefix)
    keys = [obj["Key"] for obj in result.get("Contents", []) if pattern.match(obj["Key"])]
    return {key[len(prefix) : -len(suffix)] for key in keys}


# Deliberately the MOST RECENT missing date, not the oldest -- in normal
# nightly operation this always converges to last night's newly-published
# puzzle anyway, so it only matters if there's ever a multi-day backlog, in
# which case only the newest gap gets backfilled per run (see
# DEPLOYMENT_STEPS.md for how to manually re-invoke for an older gap).
def _most_recent_missing_date():
    puzzle_dates = _list_dated_dates(PUZZLE_KEY_PREFIX, DATED_PUZZLE_KEY_PATTERN, ".json")
    blog_dates = _list_dated_dates(BLOG_KEY_PREFIX, DATED_BLOG_KEY_PATTERN, ".html")
    missing = puzzle_dates - blog_dates
    if not missing:
        return None
    return max(missing)  # lexicographic sort == chronological for YYYY-MM-DD


# Same DATED_BLOG_KEY_PATTERN _most_recent_missing_date() uses above, but
# keeping the full (date, LastModified) pairs this time instead of just the
# bare date strings -- list_objects_v2 already returns LastModified per
# object, so this costs nothing extra to also feed the sitemap's <lastmod>.
def _list_blog_posts():
    result = s3.list_objects_v2(Bucket=PUZZLE_BUCKET_NAME, Prefix=BLOG_KEY_PREFIX)
    posts = []
    for obj in result.get("Contents", []):
        if DATED_BLOG_KEY_PATTERN.match(obj["Key"]):
            date_str = obj["Key"][len(BLOG_KEY_PREFIX) : -len(".html")]
            posts.append((date_str, obj["LastModified"]))
    return sorted(posts)


# Home page + submit-group.html are the two other real, indexable pages on
# the site -- everything else (puzzles/*.json, admin.html, the KaiOS app's
# own origin) is either raw data or not meant to be crawled at all. Plain
# string-built XML -- sitemaps.org's format is simple and fixed-shape here,
# every URL is either a static path or digits/hyphens, so nothing needs
# real XML escaping.
def _build_sitemap_xml():
    urls = [(SITE_URL, None), (f"{SITE_URL}submit-group.html", None)]
    for date_str, last_modified in _list_blog_posts():
        urls.append((f"{SITE_URL}blog/{date_str}.html", last_modified.strftime("%Y-%m-%d")))

    entries = []
    for loc, lastmod in urls:
        if lastmod:
            entries.append(f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{lastmod}</lastmod>\n  </url>")
        else:
            entries.append(f"  <url>\n    <loc>{loc}</loc>\n  </url>")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )


# Regenerated on every run, not just when a new post actually gets
# published -- cheap (one list_objects_v2 + one put_object), and keeps it
# self-healing if blog/* ever changes some other way.
def _update_sitemap():
    s3.put_object(
        Bucket=PUZZLE_BUCKET_NAME,
        Key=SITEMAP_KEY,
        Body=_build_sitemap_xml().encode("utf-8"),
        ContentType="application/xml",
    )


def _fetch_puzzle_groups(date_str):
    obj = s3.get_object(Bucket=PUZZLE_BUCKET_NAME, Key=f"{PUZZLE_KEY_PREFIX}{date_str}.json")
    puzzle = json.loads(obj["Body"].read())
    return puzzle["groups"]  # already sorted ascending by difficulty per the schema


def _build_group_prompt(category, words, difficulty):
    return (
        f"Category: {category}\n"
        f"Words: {', '.join(words)}\n"
        f"Difficulty rank: {difficulty} of 4 (1 = easiest, 4 = hardest)\n\n"
        "Write the explanation now."
    )


def _build_why_prompt(groups):
    lines = [
        f"Group (difficulty {g['difficulty']} of 4) -- {g['category']}: {', '.join(g['words'])}" for g in groups
    ]
    return "\n".join(lines) + "\n\nWrite the closing paragraph now."


# Gemini 3.x dropped temperature/top_p/top_k/candidate_count entirely (a
# request setting any of them gets rejected) in favor of thinking_level,
# which controls reasoning depth rather than sampling. "low" fits both
# calls below: short creative prose, not multi-step reasoning, tool use, or
# code -- exactly the high-throughput/low-latency case Google's own docs
# point "low" at. Can't set thinking_level alongside the legacy
# thinking_budget -- Gemini 3.x rejects that combination outright.
_THINKING_CONFIG = types.ThinkingConfig(thinking_level="low")


def _generate_group_explanation(group):
    config = types.GenerateContentConfig(system_instruction=GROUP_SYSTEM_INSTRUCTION, thinking_config=_THINKING_CONFIG)
    prompt = _build_group_prompt(group["category"], group["words"], group["difficulty"])
    response = client.models.generate_content(model=MODEL_NAME, contents=[prompt], config=config)
    return response.text.strip()


def _generate_why_paragraph(groups):
    config = types.GenerateContentConfig(system_instruction=WHY_SYSTEM_INSTRUCTION, thinking_config=_THINKING_CONFIG)
    response = client.models.generate_content(model=MODEL_NAME, contents=[_build_why_prompt(groups)], config=config)
    return response.text.strip()


# "August 21, 2026" -- avoids %-d/%#d (platform-specific strftime flags for
# a no-leading-zero day) by just formatting the day as a plain int instead.
def _display_date(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt:%B} {dt.day}, {dt.year}"


# The system prompts tell Gemini not to use markdown, but it doesn't always
# listen -- **bold**/_italic_ has shown up in real output despite that. Since
# this text goes straight into an HTML <p>, that leaks literal asterisks
# instead of rendering anything, so: escape first (in case the model ever
# emits a stray "<"/"&"/">", e.g. from an "AT&T"-style word), then convert
# just the two inline markdown forms actually seen in practice into real
# tags. Deliberately not a full markdown parser -- the prompts already ask
# for a single plain paragraph with no headers/lists/links, so there's
# nothing else worth handling, and block-level markdown would risk invalid
# HTML nested inside the <p> this gets substituted into anyway.
def _gemini_text_to_html(text):
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<em>\1</em>", escaped)
    return escaped


def _render_post(date_str, groups, explanations, why_paragraph):
    template_obj = s3.get_object(Bucket=PUZZLE_BUCKET_NAME, Key=TEMPLATE_KEY)
    template = Template(template_obj["Body"].read().decode("utf-8"))

    mapping = {
        "display_date": html.escape(_display_date(date_str)),
        "why_paragraph": _gemini_text_to_html(why_paragraph),
    }
    for i, (group, explanation) in enumerate(zip(groups, explanations), start=1):
        mapping[f"group{i}_category"] = html.escape(group["category"])
        mapping[f"group{i}_words"] = html.escape(", ".join(group["words"]))
        mapping[f"group{i}_explanation"] = _gemini_text_to_html(explanation)

    # Strict substitute(), not safe_substitute() -- a placeholder/mapping
    # mismatch (e.g. template.html hand-edited with a typo'd token) should
    # fail loudly here rather than silently publish a post with a literal
    # "$group3_explanation" baked into it.
    return template.substitute(mapping)


def generate_blog_post():
    date_str = _most_recent_missing_date()
    if date_str is None:
        print("No missing blog post to generate")
        result = {"generated": False, "reason": "no missing blog dates"}
    else:
        groups = _fetch_puzzle_groups(date_str)
        explanations = [_generate_group_explanation(group) for group in groups]
        why_paragraph = _generate_why_paragraph(groups)

        post_html = _render_post(date_str, groups, explanations, why_paragraph)
        s3.put_object(
            Bucket=PUZZLE_BUCKET_NAME,
            Key=f"{BLOG_KEY_PREFIX}{date_str}.html",
            Body=post_html.encode("utf-8"),
            ContentType="text/html; charset=utf-8",
        )
        print(f"Generated {BLOG_KEY_PREFIX}{date_str}.html")
        result = {"generated": True, "date": date_str}

    # Runs whichever branch above ran -- see _update_sitemap()'s own
    # comment for why this isn't gated on a post actually being generated.
    _update_sitemap()
    return result


# This Lambda has exactly one trigger -- its own EventBridge scheduled rule,
# no API Gateway at all -- so unlike backend/lambda/lambda_function.py there's
# no event-shape branching here. Exceptions propagate rather than getting
# caught into an always-200 response: EventBridge doesn't read this
# function's return value either way, so a real CloudWatch Errors metric on
# a failed invocation is the only signal worth producing.
def lambda_handler(event, context):
    print(json.dumps(event))
    try:
        return generate_blog_post()
    except Exception:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    print(json.dumps(lambda_handler({}, {})))
