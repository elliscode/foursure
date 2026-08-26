# Fourplay — Deployment Steps

Manual runbook for deploying Fourplay to AWS. Nothing here is scripted or automated — every AWS resource is created by hand in the console (or via the AWS CLI, your choice), matching how `kaios-calorie-counter` is set up. `backend/README.md` has the authoritative env-var/route reference; this file is the step-by-step sequence to actually stand the whole thing up and keep it running.

Split into three parts:
- **Part 1** — one-time AWS infrastructure setup (only do this once, ever, per environment)
- **Part 2** — bootstrapping the very first puzzle (only needed once, right after Part 1)
- **Part 3** — the ordinary "I changed some code, ship it" workflow you'll use every other time

---

## Part 1 — One-time AWS setup

### 1. DynamoDB table

- Table name: `fourplay` (or whatever you want — just make sure it matches `DYNAMODB_TABLE_NAME` in step 5)
- Partition key: `key1` (String)
- Sort key: `key2` (String)
- Enable **TTL**, attribute name `expiration` (used by `otp`/`token` records — group records never expire, so this is safe to turn on unconditionally)

### 2. S3 bucket for the frontend

- Create a bucket (e.g. `fourplay-elliscode-com`) — this is what `s3/release.sh`'s `BUCKET` variable and the Lambda's `PUZZLE_BUCKET_NAME` env var both point at, so keep the name consistent across both.
- Keep the bucket **private** (block all public access) — you'll front it with CloudFront next, not serve directly from the bucket.
- **CORS policy**: Fourplay ships as two things — the website at `https://fourplay.elliscode.com`, and a packaged KaiOS app that (per KaiOS's app-origin convention) serves its own code from `http://fourplay.localhost`. Both fetch puzzle data (`puzzles/*.json`) directly from this bucket via absolute URLs (see `frontend/js/puzzle.js`'s `SITE_URL`), so both origins need `GET` allowed in the bucket's CORS configuration:
  ```json
  [
    {
      "AllowedOrigins": ["https://fourplay.elliscode.com", "http://fourplay.localhost"],
      "AllowedMethods": ["GET"],
      "AllowedHeaders": ["*"]
    }
  ]
  ```

### 3. CloudFront distribution (serves the site over HTTPS)

- Request an ACM certificate for your domain (e.g. `fourplay.elliscode.com`) **in `us-east-1`** — CloudFront only accepts certs from that region regardless of where anything else lives. Validate it (DNS validation is easiest if your domain's already in Route53).
- Create a CloudFront distribution:
  - Origin: the S3 bucket from step 2, using **Origin Access Control (OAC)** (not a public bucket policy) — CloudFront will offer to update the bucket policy for you automatically when you set this up.
  - Default root object: `index.html`
  - Attach the ACM cert from above, add `fourplay.elliscode.com` as an alternate domain name (CNAME).
- In Route53 (or wherever your DNS lives), point `fourplay.elliscode.com` at the CloudFront distribution (an ALIAS record if using Route53, otherwise a CNAME).

### 4. Lambda function

- Create a function (e.g. `fourplay-api`), **Python 3.14** runtime, handler `lambda_function.lambda_handler`.
- Don't set the code yet — that comes from `backend/release.sh` in Part 3. For now just create the function shell so you have something to attach a role/env vars/triggers to.
- Attach an IAM role granting:
  - `dynamodb:PutItem` / `GetItem` / `UpdateItem` / `Query` / `DeleteItem` on the table from step 1 — `DeleteItem` is for `/admin/delete-puzzle` only (a constructed-but-unpublished puzzle is disposable draft data); **groups themselves are still never deleted**
  - `sqs:SendMessage` on the shared SMS queue (see step 5 — this is an *existing* queue, not one you create)
  - `s3:GetObject` / `PutObject` scoped to `arn:aws:s3:::<your-bucket-name>/puzzles/*`
  - `s3:ListBucket` on `arn:aws:s3:::<your-bucket-name>` — needed to figure out the next puzzle's `id` at publish time (a bucket-level permission, distinct from the object-level grants above)

### 5. Lambda environment variables

Set these under Configuration → Environment variables:

| Variable | Set it to |
|---|---|
| `DOMAIN_NAMES` | `https://fourplay.elliscode.com,http://fourplay.localhost` (comma-separated — the website and the packaged KaiOS app's origin, see step 2's CORS note; add more the same way if needed) |
| `DYNAMODB_TABLE_NAME` | Whatever you named the table in step 1 |
| `ADMIN_PHONES` | Comma-separated 10-digit phone numbers allowed to log into `admin.html` |
| `SMS_SQS_QUEUE_URL` | The URL of the **existing** shared SQS queue the Twilio-forwarding Lambda already consumes (the same one `kaios-calorie-counter`/`kaios-t9-wizard` use) — you're not creating a new queue, just pointing at that one |
| `ADMIN_COOKIE_DOMAIN` | `.fourplay.elliscode.com` (leading dot) |
| `PUZZLE_BUCKET_NAME` | The bucket name from step 2 |

### 6. API Gateway

- Create an API (REST or HTTP API, either works — `lambda_function.py` handles both event shapes) with an **ANY** method + proxy integration targeting the Lambda from step 4.
- Deploy it to a stage and note the invoke URL.
- Optional but recommended, to match the URLs already hardcoded in the frontend: set up a custom domain (`api.fourplay.elliscode.com`) for this API — needs its own ACM cert (same region as the API if it's a regional endpoint) and a Route53 record, same idea as step 3.

### 7. EventBridge scheduled rule

- Create a scheduled rule with schedule expression `cron(0 23 * * ? *)` — 23:00 UTC nightly, so the file is ready an hour ahead of the 00:00 UTC date it's for.
- Target: the **same** Lambda function from step 4 (a second trigger on it, not a new function). No input transformer needed — `lambda_handler` tells API Gateway and EventBridge events apart by shape automatically.
- This only *publishes* whatever's oldest in the admin-built puzzle queue — it doesn't build anything itself. See Part 2 below for keeping that queue stocked.

### 8. Point the code at your real URLs

The repo ships with `fourplay.elliscode.com` / `api.fourplay.elliscode.com` as placeholders. Before your first real deploy, update these to match whatever you actually set up above (skip this if you used those exact domains):

| File | Constant | 
|---|---|
| `frontend/admin.html` | `BASE_URL` |
| `s3/submit-group.html` | `API_HOST` |
| `frontend/js/puzzle.js` | `SITE_URL` |
| `frontend/index.html` | `#submit-group-anchor`'s `href` (hardcoded absolute — the packaged KaiOS app has no `submit-group.html` of its own to resolve a relative link against) |
| `s3/release.sh` | `BUCKET` |
| `backend/release.sh` | `--function-name=` |

---

## Part 2 — Bootstrap the first puzzle

A brand-new deployment has **zero** groups and **zero** constructed puzzles in DynamoDB, so `generate_puzzle()` will find an empty queue and do nothing — the site will fall back to showing one of the 8 rotating `puzzles/default00N.json` puzzles (see `frontend/js/puzzle.js`'s `getThePuzzle()`/`defaultPuzzleKeyFor()`) until you build at least one real puzzle:

1. Deploy the backend and frontend at least once (Part 3, both steps) so everything's live.
2. Submit at least 4 groups — either through the site's "Submit a group" page, or by hand.
3. Log into `admin.html` and **approve** at least 4 of them.
4. In `admin.html`'s **Build Puzzle** tab, add 4 of those approved groups and assign each a difficulty (1–4, one of each), then submit. This is what actually locks the groups (`used_in_puzzle`) — `generate_puzzle()` itself only ever *publishes* an already-built puzzle, it never builds one. Repeat this to build up a backlog of several puzzles if you don't want to have to keep doing this daily by hand right away.
5. Manually invoke the Lambda once with a synthetic EventBridge-shaped event, rather than waiting for the scheduled rule — in the Lambda console, create a **Test event** with this payload and run it:
   ```json
   { "source": "aws.events" }
   ```
6. Confirm it worked: check that `puzzles/{tomorrow's date}.json` now exists in the S3 bucket with an `id` field, and that the puzzle you built in step 4 now shows up under the "used" filter in the Build Puzzle tab instead of "unused". The site will show that puzzle starting the date it's for — if you want to play it immediately rather than waiting, browse via the `?date=` URL param, or just wait a day.
7. (Optional, any time after this) Set `firstPuzzleDate` in `puzzles/manifest.json` once you've decided the real first day of puzzle history — this bounds how far back the calendar picker will let people go (any date it *does* let you pick will always load something, real or a default fallback, so this is purely a UI floor, not a content gate). `s3/puzzles/manifest.json` in the repo ships with it blank (`{"firstPuzzleDate": ""}`, meaning no floor); upload your edited copy to `puzzles/manifest.json` in the bucket directly — like the daily puzzle files, `s3/release.sh` never touches anything under `puzzles/`, so this is a manual, no-code-change edit you can make whenever.

### Undoing a manual test run

Running the test event in step 5 above (or invoking the Lambda manually at any other point) publishes for real — `generate_puzzle()` doesn't know the difference between a real scheduled firing and a manual one. If you want to roll one back (e.g. you were just testing on real infrastructure and don't want it to count), `generate_puzzle()` touches three places, and undoing it cleanly means reverting all three:

1. **Delete the S3 file** it wrote: `puzzles/{that date}.json`.
2. **On the puzzle record** (`key1="puzzle"`) it published — find it via `admin.html`'s Build Puzzle tab under the "used" filter, or by matching its `puzzleDate` to the date you're undoing — `REMOVE used, puzzleDate, usedAt`. Setting `used` to `false` instead of removing it would also work functionally (the "unused" query filter in `puzzle.py` accepts either `attribute_not_exists(used)` or `used = false`), but `REMOVE` matches how the rest of the codebase reverts this kind of flag (see `delete_puzzle_route`'s `REMOVE used_in_puzzle` on groups) and avoids leaving a stray `usedAt`/`puzzleDate` behind on a record that's supposed to look never-published.
3. **On each of that puzzle's 4 group records** (`key1="group"`, their `groupId`s are right there in the puzzle record's `groups` list) — `REMOVE puzzleDate`. This one's purely informational (nothing in the code ever reads a group's `puzzleDate` back, so leaving it stale won't break anything functionally), but it's misleading to leave behind if you're trying to fully roll back — those 4 groups would still claim they ran on a date they didn't. Their `used_in_puzzle` flag is untouched by any of this either way — that was set back at construction time (`create_puzzle_route`), not by `generate_puzzle()`.

---

## Part 3 — Every time you deploy a change

Two independent pieces — deploy whichever one you actually changed (or both):

### Frontend (`frontend/`, `s3/blog/`, `s3/submit-group.html`)

```bash
cd s3
sh release.sh
```

Copies `frontend/`'s `css/`, `js/`, `index.html`, `admin.html` into `s3/`, then syncs the whole `s3/` directory up to the bucket (`--delete`, so anything removed locally gets removed from the bucket too) — **except** `puzzles/*`, which is excluded on purpose since that's written directly by the Lambda, not by this script.

If you're using CloudFront (step 3 above), it has its own cache — you likely want to also invalidate it after a deploy so changes show up immediately instead of waiting for the cache to expire:
```bash
aws cloudfront create-invalidation --distribution-id <YOUR_DISTRIBUTION_ID> --paths "/*"
```

### Backend (`backend/lambda/`)

```bash
cd backend
sh release.sh
```

Zips `backend/lambda/` and pushes it straight to the Lambda function via `aws lambda update-function-code`. No build step, no dependency install — it's stdlib + boto3 only.

### KaiOS app package (`frontend/`)

Only needed when you're submitting a new build to the KaiOS store — the website deploy above doesn't touch this.

```bash
cd frontend
sh kaios-release.sh
```

Zips `frontend/` (`index.html`, `css/`, `js/`, `manifest.webmanifest`, `assets/`) into `fourplay-<timestamp>.zip` for upload — excludes `admin.html` (a web-only surface with no reason to ship inside the player-facing app) and both release scripts. **Before this is meaningful**, `frontend/assets/icons/kaios_56.png` and `kaios_112.png` need to exist (referenced by `manifest.webmanifest`'s `icons` array) — they're design assets not included by default, so add them first.

---

## Part 4 — Sanity check after deploying

- `curl -X POST https://api.fourplay.elliscode.com/test` (with an `Origin` header matching `DOMAIN_NAMES`, or it'll 403) → should return `{"status": "up"}`
- Open the site, confirm today's puzzle loads and is playable
- Log into `admin.html`, confirm the OTP text arrives and login works
- Submit a test group from the site, confirm it shows up as Pending in `admin.html`
- If you've built a new KaiOS package, load it on-device (or in the simulator) and confirm the puzzle still loads — it's fetching `puzzles/*.json` from `https://fourplay.elliscode.com` even though the app itself runs from `http://fourplay.localhost`, so this also double-checks the CORS policy from step 2 is actually working
