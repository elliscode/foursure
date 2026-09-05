# Foursure Backend

`lambda/` is a single Lambda function (no framework, plain Python — see `fourplay/`), structured the same way as `kaios-calorie-counter/backend`. Every route is `POST` only (to avoid CORS preflight — see the comment in `lambda_function.py`), except the puzzle-generation path, which isn't reached through API Gateway at all.

## Routes

| Route | Auth | Purpose |
|-------|------|---------|
| `/test` | none | Health check — returns `{"status": "up"}` |
| `/submit-group` | none | Body `{words: [4 strings], category}` — anyone can propose a group. Lands as a pending record (no `approved` attribute set) for an admin to review. |
| `/submit-result` | none | Body `{date: "YYYY-MM-DD", attempts: [[1-4, 1-4, 1-4, 1-4], ...]}` — anonymous ping sent once a puzzle finishes (see `fourplay/results.py`). Each attempt is the difficulty of the 4 tiles guessed together (all 4 equal = a correct guess of that difficulty; otherwise a wrong guess). Computes a score (+difficulty per correct guess, -1 per wrong guess) and a success/failure verdict server-side, logs them, and atomically increments a `key1="completions"`, `key2="{date}#{score}"` counter (`ADD`, so the very first completion of a new date/score pair creates the row same as any later increment) — nothing identifying who submitted it is ever stored, and neither value is returned to the client. |
| `/completions` | none | Body `{date: "YYYY-MM-DD"}` — returns `{date, counts: {"{score}": count, ...}}`, a `Query` on `key1="completions" AND begins_with(key2, "{date}#")` reassembled into a sparse score-to-count dict. Only ever returns aggregate counts, nothing identifying any submitter. Feeds the bar chart on each blog post (`s3/blog/template.html`) — the fixed -4..10 axis and zero-filling any missing score is that page's job at render time, not this route's. |
| `/admin/otp` | phone must be in `ADMIN_PHONES` | Admin login step 1 — texts a one-time code via the shared SQS-triggered Twilio Lambda |
| `/admin/login` | — | Admin login step 2 — verifies the code, sets the session cookie + returns a CSRF token (`x-csrf-token` header) |
| `/admin/logged-in-check` | session | Confirms the current session/cookie is still valid |
| `/admin/list-groups` | session | Body `{csrf, status?: "pending"\|"approved"\|"rejected"\|"all"\|"approved-unused"}`, default `pending` — lists groups in that state. `approved-unused` (approved, not yet locked into a constructed puzzle) is what the "Build Puzzle" tab offers as addable. |
| `/admin/decide-group` | session | Body `{csrf, id, approved: true/false}` — approves or rejects a group. **Never deletes the record** — `approved` just gets set. |
| `/admin/edit-group` | session | Body `{csrf, id, words, category}` — rejects the original record (`approved: false`) and inserts a brand-new, already-approved record with the edited words/category, linked back via `sourceGroupId` |
| `/admin/create-puzzle` | session | Body `{csrf, groups: [{groupId, difficulty}, ...]}` (exactly 4, difficulties a 1–4 permutation) — builds a whole puzzle from 4 admin-picked approved groups. This is what actually locks each group (`used_in_puzzle: true`) — not `generate_puzzle()` below, which just publishes an already-built puzzle later. |
| `/admin/list-puzzles` | session | Body `{csrf, status?: "unused"\|"used"\|"all"}`, default `unused` — lists constructed puzzles, oldest first |
| `/admin/delete-puzzle` | session | Body `{csrf, id}` — deletes a constructed-but-not-yet-published puzzle for real (unlike groups, a draft puzzle isn't a historical record worth keeping) and frees its 4 groups back up (`used_in_puzzle` removed) so they can go into a different puzzle. Rejects with 400 if the puzzle's already been published. |

## Puzzle construction &amp; publishing

Puzzles are built by an admin ahead of time (`/admin/create-puzzle`, above — the "Build Puzzle" tab in `admin.html`: pick 4 approved groups, assign each a difficulty 1–4, submit), stored as their own `key1="puzzle"` record (`groups`: 4 `{groupId, category, words, difficulty}` maps, denormalized from each group at construction time since groups are otherwise immutable once approved; `createdAt`; `used`, absent until published). `generate_puzzle()` doesn't build anything — it's a nightly picker.

**`generate_puzzle()` (EventBridge, not API Gateway)** — not a separate Lambda function; `lambda_handler` in `lambda_function.py` checks for an EventBridge-shaped event (`event.get("source") == "aws.events"`) before doing any API Gateway routing, and calls `fourplay.puzzle.generate_puzzle()` directly. The developer attaches an EventBridge scheduled rule to this same function as a second trigger (see setup step 4 below) — no new AWS resource beyond the rule itself.

Each run:
1. Computes tomorrow's date and checks whether `puzzles/{that date}.json` already exists. If it does, logs that and stops immediately — nothing else runs. This guards against the rule (or a manual invoke) firing more than once on the same day: without it, a second run would just pick a *different* unused puzzle out of the queue and silently overwrite the first run's file with it, quietly burning a queued puzzle that never actually got served.
2. Otherwise, queries constructed puzzles where `used` is unset/false. If none are available, logs it and does nothing else — admins need to keep the queue stocked.
3. Picks the **oldest** one (FIFO by `createdAt`) — first constructed, first published, so nothing sits in the queue indefinitely while newer ones keep getting picked instead.
4. Assigns the puzzle's `id`: lists the `puzzles/` prefix in the bucket (the same listing step 1's existence check already did — reused, not repeated), finds the most recently published dated file (`puzzles/YYYY-MM-DD.json` — the `default00N.json` fallback files, see below, don't match that pattern and are ignored automatically), reads its `id`, and adds 1. If no dated file exists yet, starts at `1` (the `default00N.json` files all carry `id: 0`).
5. Writes `puzzles/{YYYY-MM-DD}.json` (tomorrow's date) to the frontend's S3 bucket: `{"id": ..., "date": "...", "groups": [...]}`, its 4 groups sorted by difficulty ascending.
6. Marks the chosen puzzle `used: true` + `puzzleDate`, and stamps that same `puzzleDate` onto its 4 group records too (their `used_in_puzzle` is already `true` from construction time — this just records *when* they actually ran, for the admin's own reference).

The frontend just requests `puzzles/{date}.json` directly and falls back to one of 8 hand-authored `puzzles/default00N.json` files if that 404s, picked deterministically by day-of-year mod 8 (see `defaultPuzzleKeyFor()` in `frontend/js/puzzle.js`) — `generate_puzzle()` has nothing to do with that fallback and doesn't need to keep anything in sync for it.

There *is* a `puzzles/manifest.json`, but it's unrelated to publishing — a single hand-edited field, `firstPuzzleDate`, that only bounds how far back the frontend's calendar picker will go. Nothing in this backend reads or writes it.

Puzzle data (`puzzles/*.json`, including `manifest.json`) is fetched by the frontend as an absolute `https://{brand}.elliscode.com/...` URL rather than a relative path — Foursure (and, during the gradual rebrand, the legacy `fourplay.elliscode.com`) ships as both this website and a packaged KaiOS app whose own code runs from `http://fourplay.localhost`, which never has its own `puzzles/` directory. That makes those fetches cross-origin from the KaiOS app's perspective, so the S3 bucket needs a CORS policy allowing `GET` from `https://foursure.elliscode.com`, `https://fourplay.elliscode.com`, and `http://fourplay.localhost` (see `DEPLOYMENT_STEPS.md`) — separate from, and in addition to, this Lambda's own `DOMAIN_NAMES` origin check below, which only governs the API routes in the table above, not the static bucket.

## Environment variables

| Variable | Example | Description |
|----------|---------|--------------|
| `DOMAIN_NAMES` | `https://foursure.elliscode.com,https://fourplay.elliscode.com,http://fourplay.localhost` | Comma-separated allowlist of `Origin` headers. Any request from an origin not in this list gets a 403. Includes both brand domains during the gradual rebrand, plus the packaged KaiOS app's origin (`http://fourplay.localhost`). |
| `DYNAMODB_TABLE_NAME` | `foursure` | The DynamoDB table every route reads/writes. |
| `ADMIN_PHONES` | `5551234567,5559876543` | Comma-separated 10-digit US phone numbers permitted to log into the admin panel. `/admin/otp`/`/admin/login` reject anyone else. |
| `SMS_SQS_QUEUE_URL` | — | The existing, project-agnostic SQS queue that an already-deployed Twilio Lambda consumes to send the OTP text — same queue `kaios-calorie-counter`/`kaios-t9-wizard` use, no new queue needed. |
| `ADMIN_COOKIE_DOMAIN` | `.foursure.elliscode.com` | Leading-dot wildcard domain for the admin session cookie, so it's sent back correctly if `admin.html` and the API end up on different subdomains. |
| `PUZZLE_BUCKET_NAME` | `foursure-elliscode-com` | The S3 bucket the frontend is hosted from — `generate_puzzle()` writes `puzzles/{date}.json` here directly, and lists the `puzzles/` prefix to assign it an `id`. |

Set these on the Lambda function itself (Configuration → Environment variables in the console, or `--environment` on `aws lambda create-function`/`update-function-configuration`). No `.env` file is checked in.

## One-time AWS setup

1. Create a DynamoDB table named `foursure` — partition key `key1` (String), sort key `key2` (String). Enable **TTL** on it with `expiration` as the attribute name (used by `otp`/`token` records only — group records never expire).
2. Create a Lambda function (e.g. `fourplay-api`), Python 3.14 runtime, handler `lambda_function.lambda_handler`, with the environment variables listed above. Grant its own IAM role:
   - `dynamodb:PutItem`/`GetItem`/`UpdateItem`/`Query`/`DeleteItem` on the table — `DeleteItem` is needed now for `/admin/delete-puzzle` (a constructed-but-unpublished puzzle is disposable draft data); **groups are still never deleted**, only `key1="puzzle"` records ever go through `DeleteItem`
   - `sqs:SendMessage` on the SMS queue
   - `s3:GetObject`/`PutObject` scoped to `arn:aws:s3:::<PUZZLE_BUCKET_NAME>/puzzles/*`
   - `s3:ListBucket` on `arn:aws:s3:::<PUZZLE_BUCKET_NAME>` (a bucket-level permission, distinct from the object-level `GetObject`/`PutObject` above — needed for `_next_puzzle_id()`'s prefix listing; scope it tighter with an `s3:prefix` condition of `puzzles/*` if you want)
3. Set up an API Gateway with an ANY method + proxy integration targeting this Lambda.
4. Create an EventBridge scheduled rule targeting the *same* Lambda function as a second trigger — this is what fires `generate_puzzle()` to publish tomorrow's puzzle. Schedule expression `cron(0 23 * * ? *)` (23:00 UTC nightly, so the file is ready an hour ahead of the 00:00 UTC date it's for). No input transformer needed; `lambda_handler` detects it purely from the event shape.
5. Run `sh release.sh` to deploy.
6. Build up a backlog of constructed puzzles via `admin.html`'s "Build Puzzle" tab before relying on the schedule — `generate_puzzle()` does nothing if the queue is empty (see Part 2 of `DEPLOYMENT_STEPS.md`).

## Releasing

```
sh release.sh
```
