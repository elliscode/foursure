# Fourplay

A daily word puzzle: find the four groups of four connected words, NYT Connections-style. Groups are proposed by anyone, moderated by an admin, and grouped into puzzles automatically each day from the approved pool. No accounts, no framework, no build step — game progress lives entirely on-device in IndexedDB.

## Repo layout

| Path | What it is |
|---|---|
| `frontend/` | The puzzle page (`index.html`) and the admin moderation panel (`admin.html`) — plain HTML/CSS/JS. See `backend/README.md` for the routes they call. |
| `backend/` | The Lambda API (group submission, admin moderation, puzzle generation) + DynamoDB. See `backend/README.md`. |
| `s3/` | Static hosting root for the deployed site: the built frontend, the daily puzzle JSON files + manifest, the blog, and `submit-group.html` (a standalone page, not part of `frontend/` — see below). |

## Features

**For players:**
- **Today's puzzle** (`index.html`) — a 4x4 grid, select 4 tiles and submit; wrong guesses cost one of four chances and shake the board; a full solve/fail shows a recap grid. A calendar picker (restricted to dates a puzzle actually exists for) lets you play any past puzzle. Every attempt is saved to IndexedDB per-date, so reloading or coming back later picks up exactly where you left off.
- **Submit a group** (`submit-group.html`, linked from the puzzle page) — anyone can propose 4 words + the category connecting them. Submissions are anonymous and go into the moderation queue.

**For moderation:**
- `admin.html` — phone-OTP-gated review tool. Approve, reject, or edit any submitted group (editing rejects the original and creates a new, already-approved replacement — the original submission is never deleted).

## Architecture

```
Puzzle page (frontend/index.html)
  ├─ static puzzle data (S3) — puzzles/manifest.json + puzzles/YYYY-MM-DD.json, no auth
  └─ API (Lambda + API Gateway + DynamoDB)
       ├─ /submit-group                                  — public group submission
       └─ /admin/*                                        — moderation (phone-OTP admin login)

Puzzle generation — the same Lambda, triggered by an EventBridge scheduled
rule instead of API Gateway — picks 4 random approved/unused groups, assigns
each a random difficulty, and writes tomorrow's puzzle + manifest straight
to S3.
```

## Backend

Single Python 3.14 Lambda (no framework), one DynamoDB table (`key1`/`key2` single-table design), no build step. Full route list, environment variables, and one-time AWS setup steps are in `backend/README.md`.

```
cd backend && sh release.sh
```

## Frontend

Static HTML/CSS/JS, no framework or build step. `puzzle.js` is adapted from a sibling project's Connections implementation; see its comments for what changed (IndexedDB instead of a server round trip, no D-pad navigation).

```
cd s3 && sh release.sh   # copy frontend/ in and sync the whole s3/ root up (except puzzles/, which is bucket-managed)
```
