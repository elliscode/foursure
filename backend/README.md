# Fourplay Backend

`lambda/` is a single Lambda function (no framework, plain Python — see `fourplay/`), structured the same way as `kaios-calorie-counter/backend`. Every route is `POST` only (to avoid CORS preflight — see the comment in `lambda_function.py`), except the puzzle-generation path, which isn't reached through API Gateway at all.

## Routes

| Route | Auth | Purpose |
|-------|------|---------|
| `/test` | none | Health check — returns `{"status": "up"}` |
| `/submit-group` | none | Body `{words: [4 strings], category}` — anyone can propose a group. Lands as a pending record (no `approved` attribute set) for an admin to review. |
| `/admin/otp` | phone must be in `ADMIN_PHONES` | Admin login step 1 — texts a one-time code via the shared SQS-triggered Twilio Lambda |
| `/admin/login` | — | Admin login step 2 — verifies the code, sets the session cookie + returns a CSRF token (`x-csrf-token` header) |
| `/admin/logged-in-check` | session | Confirms the current session/cookie is still valid |
| `/admin/list-groups` | session | Body `{csrf, status?: "pending"\|"approved"\|"rejected"\|"all"}`, default `pending` — lists groups in that state |
| `/admin/decide-group` | session | Body `{csrf, id, approved: true/false}` — approves or rejects a group. **Never deletes the record** — `approved` just gets set. |
| `/admin/edit-group` | session | Body `{csrf, id, words, category}` — rejects the original record (`approved: false`) and inserts a brand-new, already-approved record with the edited words/category, linked back via `sourceGroupId` |

## Puzzle generation (EventBridge, not API Gateway)

Not a separate Lambda function — `lambda_handler` in `lambda_function.py` checks for an EventBridge-shaped event (`event.get("source") == "aws.events"`) before doing any API Gateway routing, and calls `fourplay.puzzle.generate_puzzle()` directly. The developer attaches an EventBridge scheduled rule to this same function as a second trigger (see setup step 7 below) — no new AWS resource beyond the rule itself.

Each run:
1. Queries groups where `approved = true` and `used_in_puzzle` is unset/false.
2. Picks 4 at random. If fewer than 4 are available, logs it and does nothing else.
3. Randomly assigns each a unique difficulty 1–4 (see the frontend's difficulty color palette in `frontend/css/stylesheet.css` — 1 is easiest/`#0474BA`, 4 is hardest/`#F17720`).
4. Marks all 4 chosen groups `used_in_puzzle: true` (with `puzzleDate`/`difficulty` set) so they're never picked again.
5. Writes `puzzles/{YYYY-MM-DD}.json` (tomorrow's date) to the frontend's S3 bucket: `{"date": "...", "groups": [{category, words, difficulty}, ...]}`, sorted by difficulty ascending.
6. Updates `puzzles/manifest.json` in the same bucket (a sorted JSON array of every date that has a puzzle) — this is what the frontend's calendar picker reads to know which dates are playable.

## Environment variables

| Variable | Example | Description |
|----------|---------|--------------|
| `DOMAIN_NAMES` | `https://fourplay.elliscode.com` | Comma-separated allowlist of `Origin` headers. Any request from an origin not in this list gets a 403. |
| `DYNAMODB_TABLE_NAME` | `fourplay` | The DynamoDB table every route reads/writes. |
| `ADMIN_PHONES` | `5551234567,5559876543` | Comma-separated 10-digit US phone numbers permitted to log into the admin panel. `/admin/otp`/`/admin/login` reject anyone else. |
| `SMS_SQS_QUEUE_URL` | — | The existing, project-agnostic SQS queue that an already-deployed Twilio Lambda consumes to send the OTP text — same queue `kaios-calorie-counter`/`kaios-t9-wizard` use, no new queue needed. |
| `ADMIN_COOKIE_DOMAIN` | `.fourplay.elliscode.com` | Leading-dot wildcard domain for the admin session cookie, so it's sent back correctly if `admin.html` and the API end up on different subdomains. |
| `PUZZLE_BUCKET_NAME` | `fourplay-elliscode-com` | The S3 bucket the frontend is hosted from — `generate_puzzle()` writes `puzzles/{date}.json` and `puzzles/manifest.json` here directly. |

Set these on the Lambda function itself (Configuration → Environment variables in the console, or `--environment` on `aws lambda create-function`/`update-function-configuration`). No `.env` file is checked in.

## One-time AWS setup

1. Create a DynamoDB table named `fourplay` — partition key `key1` (String), sort key `key2` (String). Enable **TTL** on it with `expiration` as the attribute name (used by `otp`/`token` records only — group records never expire).
2. Create a Lambda function (e.g. `fourplay-api`), Python 3.14 runtime, handler `lambda_function.lambda_handler`, with the environment variables listed above. Grant its own IAM role:
   - `dynamodb:PutItem`/`GetItem`/`UpdateItem`/`Query` on the table (no `DeleteItem` — nothing is ever deleted)
   - `sqs:SendMessage` on the SMS queue
   - `s3:GetObject`/`PutObject` scoped to `arn:aws:s3:::<PUZZLE_BUCKET_NAME>/puzzles/*`
3. Set up an API Gateway with an ANY method + proxy integration targeting this Lambda.
4. Create an EventBridge scheduled rule (e.g. a daily cron) targeting the *same* Lambda function as a second trigger — this is what fires `generate_puzzle()` for "tomorrow's" puzzle. No input transformer needed; `lambda_handler` detects it purely from the event shape.
5. Run `sh release.sh` to deploy.

## Releasing

```
sh release.sh
```
