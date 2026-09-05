# Foursure Blog Generator

A second, independent Lambda from `backend/lambda/` (`fourplay-api`) — this one has exactly one job and one trigger (its own EventBridge scheduled rule, no API Gateway at all). Once a night: find the most recent puzzle date in the bucket with no corresponding blog post, ask Gemini to write the explanatory copy for it, publish `blog/{date}.html`, and regenerate both `sitemap.xml` and `blog/index.html` at the bucket root / under `blog/`.

It's the first Lambda in this repo with a third-party dependency (`google-genai`), so unlike `fourplay-api`'s plain-zip stdlib-only deploy, this one's packaged via Docker (same pattern as `dnd-session-notes/lambda/dnd-rag-completion-gemini`) — see "Releasing" below.

## What it does, each run

1. Lists `puzzles/` and `blog/` in the bucket, matching `YYYY-MM-DD.json`/`YYYY-MM-DD.html` respectively (`blog/template.html` doesn't match the dated pattern, so it's never mistaken for an existing post).
2. Diffs the two date sets. If nothing's missing, logs it and returns — no-op.
3. Takes either the **most recent** or **oldest** missing date, controlled by the `BACKFILL_ORDER` env var (see below) — defaults to most recent if unset. See the comment on `_next_missing_date()` in `lambda_function.py` for what each setting means for a multi-day backlog.
4. Fetches that date's `puzzles/{date}.json`, already denormalized with `category`/`words`/`difficulty` per group, sorted ascending by difficulty.
5. Makes 5 separate Gemini calls: one per group (given only that group's own category/words/difficulty, no cross-group context) asking for a 2-4 sentence explanation, plus one given all 4 groups together asking for a closing paragraph on how they work as a puzzle (overlaps, red herrings, the difficulty curve).
6. Fetches `blog/template.html`, substitutes the 14 `${...}` placeholders (`${display_date}`, `${group1_category}`/`${group1_words}`/`${group1_explanation}` through `group4`, `${why_paragraph}`) via Python's stdlib `string.Template`, and writes `blog/{date}.html`.
7. Re-lists `blog/*` **once** and reuses that single listing for two independent rebuilds — no second listing call for the second artifact:
   - `sitemap.xml` (home page, `submit-group.html`, `blog/index.html`, the static `blog/how-we-score.html` explainer, and one `<url>` per dated post with its S3 `LastModified` as `<lastmod>`), written to the bucket root.
   - `blog/index.html` — every post, newest first, linking to `{date}.html`. Built from `blog/index-template.html` (same one-placeholder, `string.Template`-substituted pattern as the post template). Every entry's data (just the date, formatted) comes straight from the S3 key names already listed — no past post's content is ever re-read or re-parsed just because a new one was added.

   Both steps always run, even on the no-op path in step 2, so they self-heal regardless of whether a new post was generated this run.

No retries across the 5 Gemini calls, and no partial writes for the post itself — either all 5 calls + the render succeed and `blog/{date}.html` gets written once, or an exception propagates before the sitemap step runs, leaving that date "missing" for the next scheduled run to retry from scratch.

## Environment variables

| Variable | Example | Description |
|----------|---------|--------------|
| `PUZZLE_BUCKET_NAME` | `foursure-elliscode-com` | Same bucket `fourplay-api` uses — this Lambda reads `puzzles/*`, reads/writes `blog/*`, and writes `sitemap.xml` at the root. |
| `GEMINI_API_KEY` | — | Plain env var, same convention as `fourplay-api`'s `SMS_SQS_QUEUE_URL` — no Secrets Manager anywhere in this codebase. |
| `BACKFILL_ORDER` | `oldest` | Optional. Which missing date to backfill next when there's a multi-day gap — `newest` (the default if this is unset, blank, or anything other than exactly `oldest`) or `oldest`. Toggle it directly in the Lambda console's environment variables whenever you want to switch, no code change needed. |

## One-time AWS setup

See `DEPLOYMENT_STEPS.md` in the repo root for the full step-by-step (Lambda creation, IAM role, EventBridge rule, architecture/timeout settings) alongside the rest of the project's setup.

IAM role needed — notably narrower than `fourplay-api`'s (no DynamoDB, no SQS at all):
- Standard Lambda basic execution (CloudWatch Logs)
- `s3:ListBucket` on the bucket
- `s3:GetObject` on `puzzles/*` and `blog/*`
- `s3:PutObject` on `blog/*` and, separately, the exact `sitemap.xml` key (not a wildcard on the bucket root)

## Releasing

```
sh release.sh
```

Unlike `backend/release.sh`'s plain `zip` + `update-function-code`, this builds a Docker image (`public.ecr.aws/lambda/python:3.14`, deps installed to `/opt/python`), extracts the installed dependencies out of a throwaway container, zips them alongside `lambda_function.py`, and pushes that zip via `aws lambda update-function-code`. `--platform linux/arm64` is pinned explicitly in `release.sh` — make sure the Lambda function itself is configured for **arm64** architecture in AWS, or the deploy will fail at invoke time with an exec-format error.
