# Foursure

A daily word puzzle: find the four groups of four connected words, NYT Connections-style. Groups are proposed by anyone, moderated by an admin, and grouped into puzzles automatically each day from the approved pool. No accounts, no framework, no build step — game progress lives entirely on-device in IndexedDB.

Ships as both a website (`https://foursure.elliscode.com`, still reachable at the legacy `https://fourplay.elliscode.com` during the gradual rebrand) and a packaged KaiOS feature-phone app — same `frontend/` source for both. See "KaiOS packaging" under Frontend below.

## Repo layout

| Path | What it is |
|---|---|
| `frontend/` | The puzzle page (`index.html`) and the admin moderation panel (`admin.html`) — plain HTML/CSS/JS. Also holds the KaiOS packaging bits (`manifest.webmanifest`, `kaios-release.sh`) — see "KaiOS packaging" below. See `backend/README.md` for the routes they call. |
| `backend/` | The Lambda API (group submission, admin moderation, puzzle generation) + DynamoDB. See `backend/README.md`. |
| `s3/` | Static hosting root for the deployed site: the built frontend, the daily puzzle JSON files (plus 8 hand-authored `default00N.json` fallbacks), the blog, and `submit-group.html` (a standalone page, not part of `frontend/` — see below). |

## Features

**For players:**
- **Today's puzzle** (`index.html`) — a 4x4 grid, select 4 tiles and submit; wrong guesses cost one of four chances and shake the board; a full solve/fail shows a recap grid. A calendar picker lets you play any past date (down to an optional floor, `puzzles/manifest.json`'s `firstPuzzleDate`, if one's been set); if no puzzle was published for a pickable date, one of 8 hand-authored default puzzles loads instead (deterministically chosen by day-of-year, so a given date always shows the same one). Every attempt is saved to IndexedDB per-date, so reloading or coming back later picks up exactly where you left off.
- **Submit a group** (`submit-group.html`, linked from the puzzle page) — anyone can propose 4 words + the category connecting them. Submissions are anonymous and go into the moderation queue.

**For moderation:**
- `admin.html` — phone-OTP-gated review tool. Approve, reject, or edit any submitted group (editing rejects the original and creates a new, already-approved replacement — the original submission is never deleted).

## Architecture

```
Puzzle page (frontend/index.html) — served either as the website or as a
packaged KaiOS app (running from http://fourplay.localhost)
  ├─ static puzzle data (S3, always fetched as an absolute
  │  https://{brand}.elliscode.com/... URL -- foursure.elliscode.com by
  │  default, or fourplay.elliscode.com if that's the domain actually
  │  serving the page -- regardless of which of the two origins above
  │  served the page) — puzzles/YYYY-MM-DD.json, falling
  │  back to one of 8 rotating puzzles/default00N.json if that date has
  │  nothing published, plus puzzles/manifest.json (just a firstPuzzleDate
  │  floor for the calendar picker) — no auth
  └─ API (Lambda + API Gateway + DynamoDB)
       ├─ /submit-group                                  — public group submission
       └─ /admin/*                                        — moderation (phone-OTP admin login)

Puzzle generation — the same Lambda, triggered by an EventBridge scheduled
rule instead of API Gateway — publishes the oldest admin-built puzzle still
in the queue, assigning it the next sequential id, and writes tomorrow's
puzzle straight to S3.
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

### KaiOS packaging

`frontend/manifest.webmanifest` + `frontend/kaios-release.sh` produce the zip submitted to the KaiOS store — same pattern as `kaios-calorie-counter`. `admin.html` is deliberately excluded from the package (it's a web-only moderation tool); `puzzles/` was never part of `frontend/` in the first place — the packaged app fetches puzzle data live from `https://foursure.elliscode.com` (or `https://fourplay.elliscode.com`, whichever brand that build is stamped for) instead of carrying a local copy, since a submitted package can't be re-uploaded daily for tomorrow's puzzle (see `backend/README.md`).

```
cd frontend && sh kaios-release.sh
```

Requires `frontend/assets/icons/kaios_56.png`/`kaios_112.png` to exist first (design assets, not checked in).
