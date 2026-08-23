// Adapted from dumbphone-apps/s3/connections/js/connections.js — same
// select/submit/shuffle/mistake/win-lose state machine, backed by a static
// puzzles/YYYY-MM-DD.json + IndexedDB instead of a server round trip for
// the puzzle data and every guess. Runs on both a normal browser (click/tap,
// plus arrow keys as a bonus) and real KaiOS 3 hardware (D-pad + softkey
// bar) — see the "KaiOS D-pad navigation" section below, and xhrGetJson,
// syncAnswerRowHeight, and computeContrastColors for the Gecko 84
// compatibility fixes needed to get there.

const TEXT = ["Perfect!", "Great!", "Solid!", "Phew!", "Next Time!"];
// Index 0 = easiest (difficulty 1) ... index 3 = hardest (difficulty 4),
// matching backend/lambda/fourplay/puzzle.py's difficulty assignment and
// css/stylesheet.css's .difficulty-1..4 color classes.
const DIFFICULTY_CLASSES = ["difficulty-1", "difficulty-2", "difficulty-3", "difficulty-4"];
// Same order/mapping as DIFFICULTY_CLASSES, for the emoji recap grid in
// buildShareText() below — matches the --difficulty-1..4 colors in
// stylesheet.css (green -> yellow -> orange -> red, easiest to hardest).
const DIFFICULTY_EMOJI = {
  "difficulty-1": "\u{1F7E9}", // green square
  "difficulty-2": "\u{1F7E8}", // yellow square
  "difficulty-3": "\u{1F7E7}", // orange square
  "difficulty-4": "\u{1F7E5}", // red square
};

// Placeholder — same domain convention as admin.html's BASE_URL and
// submit-group.html's API_HOST. This isn't just the share link anymore: it's
// also the real host every puzzle-data fetch is hard-coded against (see
// getThePuzzle()/fetchFirstPuzzleDate() below) — Fourplay ships both as this
// website *and* as a packaged KaiOS app that serves its own code from
// http://fourplay.localhost, which never has a puzzles/ directory of its
// own (a packaged app can't be re-uploaded daily for tomorrow's puzzle), so
// a relative path would 404 there. Always fetching from SITE_URL means both
// origins read the same live data regardless of which one served the page.
const SITE_URL = "https://fourplay.elliscode.com/";
const API_HOST = "https://api.fourplay.elliscode.com";

// --- Feature flags -----------------------------------------------------
// Toggle the SMS-share affordance on/off independently per area. Both areas
// stay navigable/focusable either way (results-content is still where
// gameOver() lands focus; #answers is still nav-selectable once it has
// content) — these only control whether Enter/click/tap on them actually
// shares, whether the softkey center label says "Share", whether the
// visible "Share" button renders, and the pointer-cursor hover cue. See
// wireResultsContentClick()/wireAnswersClick()/ensureAnswersShareHint().
let SHARE_RESULTS_ENABLED = true;
let SHARE_ANSWERS_ENABLED = false;

const puzzleEl = document.getElementById("puzzle");
const answers = document.getElementById("answers");
const gameControls = document.getElementById("game-controls");
const guesses = document.getElementById("guesses");
const results = document.getElementById("results");
const displayText = document.getElementById("display-text");
const dateText = document.getElementById("date-text");
const resultBlocks = document.getElementById("result-blocks");
const comment = document.getElementById("comment");
const commentSpan = document.getElementById("comment-span");
const datePicker = document.getElementById("date-picker");
const guessSpot = document.getElementById("guess-spot");

let gameEnded = false;
let gameWon = null;
let puzzleSolution = undefined;
let attempts = [];
let attemptsSet = [];
let loading = false;
let previousDatePickerValue = undefined;
// Set once at boot by fetchFirstPuzzleDate() — null means "no minimum",
// the safe default when puzzles/manifest.json is missing/blank/unreachable.
let firstPuzzleDate = null;

function textSort(x, y) {
  return x.localeCompare(y);
}

// https://stackoverflow.com/a/2450976
function shuffle(array) {
  let output = [...array];
  let currentIndex = output.length;
  while (currentIndex != 0) {
    let randomIndex = Math.floor(Math.random() * currentIndex);
    currentIndex--;
    [output[currentIndex], output[randomIndex]] = [output[randomIndex], output[currentIndex]];
  }
  return output;
}

function findParentWithClass(el, className) {
  while (el && !el.classList.contains(className)) {
    el = el.parentElement;
  }
  return el;
}

// kaios-calorie-counter/notes/kaios-fetch-vs-xhr-cors-mystery.md documents
// fetch() GETs reproducibly failing on real KaiOS 3 hardware (Gecko 84)
// while XMLHttpRequest GETs work every time — root cause never confirmed,
// but XHR is the proven-safe choice, so every GET in this file goes through
// this instead of fetch().
function xhrGetJson(url) {
  return new Promise((resolve, reject) => {
    let xhr = new XMLHttpRequest();
    xhr.open("GET", url, true);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (e) {
          reject(e);
        }
      } else {
        reject(new Error("HTTP " + xhr.status));
      }
    };
    xhr.onerror = () => reject(new Error("network error"));
    xhr.send();
  });
}

// --- IndexedDB persistence --------------------------------------------------
// One record per puzzle date: {date, attemptsSet, completed, won}. Replaces
// connections.js's get-guesses/set-guesses server round trip entirely —
// there's no account system here, everything lives on-device.
const DB_NAME = "fourplay";
const DB_VERSION = 1;
const STORE_NAME = "gameState";
let dbPromise = undefined;

function openDatabase() {
  if (dbPromise) {
    return dbPromise;
  }
  dbPromise = new Promise((resolve, reject) => {
    let request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      let db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "date" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return dbPromise;
}

async function loadGameState(date) {
  try {
    let db = await openDatabase();
    return await new Promise((resolve, reject) => {
      let tx = db.transaction(STORE_NAME, "readonly");
      let request = tx.objectStore(STORE_NAME).get(date);
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error);
    });
  } catch (e) {
    return null;
  }
}

async function persistGameState() {
  try {
    let db = await openDatabase();
    let record = { date: datePicker.value, attemptsSet, completed: gameEnded, won: gameWon };
    await new Promise((resolve, reject) => {
      let tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).put(record);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  } catch (e) {
    // IndexedDB unavailable (private browsing, old browser, etc.) — the
    // game still works for this session, it just won't remember it later.
  }
}

// Testing helper — run clearGameData() from the devtools console to wipe
// all locally-saved game history and reload with a clean slate. Closes the
// cached connection first: indexedDB.deleteDatabase() on a database with an
// open connection just hangs waiting on a "blocked" event otherwise.
async function clearGameData() {
  if (dbPromise) {
    let db = await dbPromise;
    db.close();
    dbPromise = undefined;
  }
  await new Promise((resolve, reject) => {
    let request = indexedDB.deleteDatabase(DB_NAME);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
    request.onblocked = () => resolve();
  });
  console.log("Fourplay: local game data cleared, reloading...");
  location.reload();
}
window.clearGameData = clearGameData;

// --- Date handling -----------------------------------------------------------
function todayString() {
  let now = new Date();
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
}

function getUrlDateParam() {
  let match = window.location.search.match(/[?&]date=(\d{4}-\d{2}-\d{2})(&|$)/);
  return match ? match[1] : null;
}

function pickInitialDate() {
  let today = todayString();
  let urlDate = getUrlDateParam();
  if (urlDate && urlDate <= today) {
    return urlDate;
  }
  return today;
}

// puzzles/manifest.json is back, but much narrower than before it was
// removed: its only field is firstPuzzleDate, a UI bound on how far back the
// calendar picker can go (see init()/setDate()) — not a source of truth for
// which dates have real puzzles (that's still just "try the dated file,
// fall back to a default00N.json", unaffected by this). Missing file, a
// missing/blank field, or any fetch failure all resolve to null — "no
// minimum," the same safe-default behavior as today.
async function fetchFirstPuzzleDate() {
  try {
    let data = await xhrGetJson(`${SITE_URL}puzzles/manifest.json`);
    return data.firstPuzzleDate || null;
  } catch (e) {
    return null;
  }
}

// Container queries (cqw) aren't available on Gecko 84 — there's no old-CSS
// way to size .answer off #puzzle's width instead of its own, so this
// measures a live tile's rendered height directly and hands it to
// stylesheet.css's .answer { height: var(--tile-height) } rule. Re-run on
// resize since the tile size itself is fluid (percentage-based grid).
function syncAnswerRowHeight() {
  let card = document.querySelector(".card");
  if (!card) {
    return;
  }
  let height = card.getBoundingClientRect().height;
  if (height > 0) {
    document.documentElement.style.setProperty("--tile-height", height + "px");
  }
}

// Relative color syntax (oklch(from ...)) would pick a contrasting text
// color in pure CSS, but that's a Firefox 128 feature and isn't available
// on Gecko 84 — same WCAG relative-luminance math, just run once in JS at
// boot instead, writing the result into --difficulty-N-text custom
// properties that stylesheet.css's .difficulty-1..4 rules consume.
function relativeLuminance(hex) {
  let clean = hex.replace("#", "");
  let channels = [0, 2, 4].map((i) => {
    let v = parseInt(clean.substr(i, 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function pickTextColor(hex) {
  let luminance = relativeLuminance(hex);
  let contrastWithWhite = 1.05 / (luminance + 0.05);
  let contrastWithBlack = (luminance + 0.05) / 0.05;
  return contrastWithWhite > contrastWithBlack ? "#fff" : "#000";
}

function computeContrastColors() {
  let root = document.documentElement;
  let style = getComputedStyle(root);
  for (let i = 1; i <= 4; i++) {
    let hex = style.getPropertyValue(`--difficulty-${i}`).trim();
    if (hex) {
      root.style.setProperty(`--difficulty-${i}-text`, pickTextColor(hex));
    }
  }
}

// --- Puzzle loading ------------------------------------------------------
function clearThePuzzle() {
  puzzleSolution = undefined;
  gameEnded = false;
  gameWon = null;
  document.body.classList.remove("game-ended");
  attempts = [];
  attemptsSet = [];
  deselectAll();
  Array.from(document.getElementsByClassName("card")).forEach((x) => x.remove());
  Array.from(document.getElementsByClassName("row")).forEach((x) => x.remove());
  // Clears every solved .answer row *and* the #answers-share-hint button
  // together (drawAnswer()/ensureAnswersShareHint() will recreate the hint
  // fresh the next time a group is actually solved).
  answers.innerHTML = "";
  // #answers only becomes nav-selectable once drawAnswer() actually gives it
  // content (see there) — with none yet, it's zero-height and sits at the
  // exact same Y as the first tile row, which would merge it into that
  // row's column-indexing in moveFocus() if it were selectable while empty.
  answers.removeAttribute("nav-selectable");
  seedGuesses();
  results.style.display = "none";
  gameControls.style.display = "none";
  guesses.style.display = "none";
}

function seedGuesses() {
  while (guessSpot.firstElementChild) {
    guessSpot.firstElementChild.remove();
  }
  for (let i = 0; i < 4; i++) {
    let guessDiv = document.createElement("div");
    guessDiv.classList.add("guess");
    guessSpot.appendChild(guessDiv);
  }
}

// There are 8 hand-authored default puzzles (puzzles/default001.json ..
// default008.json) for getThePuzzle() to fall back on when a date has no
// real published puzzle — day-of-year mod 8 (+1) deterministically picks
// one of the 8 per calendar date, so the same missing date always shows the
// same fallback (not random) while different dates spread across all 8
// rather than always landing on the same one. All 8 share "id": 0, so
// "Fourplay #0" is correct regardless of which one loads.
function defaultPuzzleKeyFor(dateStr) {
  let date = new Date(dateStr + "T00:00:00Z");
  let startOfYear = Date.UTC(date.getUTCFullYear(), 0, 1);
  let dayOfYear = Math.floor((date.getTime() - startOfYear) / 86400000) + 1;
  let n = (dayOfYear % 8) + 1;
  return `puzzles/default${String(n).padStart(3, "0")}.json`;
}

async function getThePuzzle() {
  if (loading) {
    return;
  }
  loading = true;
  try {
    try {
      puzzleSolution = await xhrGetJson(`${SITE_URL}puzzles/${datePicker.value}.json`);
    } catch (e) {
      // No puzzle published for that date (yet, or ever, e.g. way in the
      // past before the site existed) — fall back to one of the 8
      // hand-authored default puzzles rather than showing an error, chosen
      // deterministically by defaultPuzzleKeyFor() above.
      puzzleSolution = await xhrGetJson(`${SITE_URL}${defaultPuzzleKeyFor(datePicker.value)}`);
    }

    gameControls.style.display = "flex";
    guesses.style.display = "flex";

    let words = [];
    for (let group of puzzleSolution.groups) {
      for (let word of group.words) {
        words.push(word);
      }
    }
    for (let word of shuffle(words)) {
      drawCard(word);
    }
    syncAnswerRowHeight();

    let saved = await loadGameState(datePicker.value);
    if (saved && Array.isArray(saved.attemptsSet)) {
      for (let attempt of saved.attemptsSet) {
        checkGuess(attempt);
      }
      interruptMessage();
      removeAllShakes();
    }

    // setFocus() calls updateSoftkeys() itself. selectables()[0] is
    // #results-content (already visible + first in document order) if the
    // replay above found an already-completed saved game, or
    // #wrap-date-picker otherwise — either way, the right thing to land on.
    setFocus(selectables()[0]);
  } catch (e) {
    showMessage("No puzzle for that date!");
  }
  loading = false;
}

function drawCard(word) {
  let cardDiv = document.createElement("div");
  cardDiv.classList.add("card");
  cardDiv.setAttribute("nav-selectable", "true");
  let contentDiv = document.createElement("div");
  contentDiv.addEventListener("click", select);
  contentDiv.innerText = word;
  cardDiv.setAttribute("answer-text", word);
  cardDiv.appendChild(contentDiv);
  puzzleEl.appendChild(cardDiv);
}

function drawAnswer(group, colorClass) {
  let cardDiv = document.createElement("div");
  cardDiv.classList.add("answer");
  let contentDiv = document.createElement("div");
  let categoryP = document.createElement("p");
  let wordListP = document.createElement("p");
  categoryP.innerText = group.category;
  contentDiv.appendChild(categoryP);
  wordListP.innerText = group.words.join(", ");
  contentDiv.appendChild(wordListP);
  cardDiv.appendChild(contentDiv);
  cardDiv.classList.add(colorClass);
  answers.appendChild(cardDiv);
  // #answers itself is the D-pad stop (one item for however many groups are
  // solved so far), not each individual answer row — setting this here,
  // now that there's actually content, is what avoids the zero-height
  // empty-#answers row-merging problem noted in clearThePuzzle().
  answers.setAttribute("nav-selectable", "true");
  ensureAnswersShareHint();
}

// Same purely-visual affordance as #results-content's "Share" button in
// index.html, but #answers is built incrementally (a new .answer row per
// solved group) rather than static markup, so this can't just be written
// once in HTML — appendChild on an *already-existing* node moves it rather
// than cloning it, so calling this at the end of every drawAnswer() keeps
// the button pinned as the last child as new rows get added after it.
function ensureAnswersShareHint() {
  if (!SHARE_ANSWERS_ENABLED) {
    return;
  }
  let hint = document.getElementById("answers-share-hint");
  if (!hint) {
    hint = document.createElement("button");
    hint.id = "answers-share-hint";
    hint.type = "button";
    hint.className = "share-hint";
    hint.tabIndex = -1;
    hint.setAttribute("aria-hidden", "true");
    hint.textContent = "Share";
  }
  answers.appendChild(hint);
}

// --- Interaction -----------------------------------------------------------
// Shared by the click handler (select, below) and the D-pad Enter-key
// handler (toggleFocusedTile, in the KaiOS nav section) — same "toggle this
// tile, capped at 4 chosen" rule regardless of how it was triggered.
function toggleChosen(contentDiv) {
  if (gameEnded) {
    return;
  }
  if (contentDiv.classList.contains("chosen")) {
    contentDiv.classList.remove("chosen");
  } else if (document.querySelectorAll("div.chosen").length < 4) {
    contentDiv.classList.add("chosen");
  }
}

function select(event) {
  toggleChosen(event.target);
}

// Anonymous, analytics-only ping — the backend computes a score/success
// verdict from this and only logs it (see backend/lambda/fourplay/
// results.py), nothing is stored, nothing comes back. Best-effort: a
// failure here must never surface to the player or affect the game in any
// way, so this is deliberately fire-and-forget with no user-facing error
// path. No explicit Content-Type header, same CORS-preflight reason as
// submit-group.html's fetch — "application/json" would force a preflight
// this POST-only backend has no route for.
function submitResult() {
  fetch(`${API_HOST}/submit-result`, {
    method: "POST",
    body: JSON.stringify({
      // datePicker.value (the calendar date actually played), not
      // puzzleSolution.date — one of the 8 rotating default puzzles can
      // back many different dates, so its own "date" field (blank on most
      // of them) isn't what analytics should bucket by.
      date: datePicker.value,
      attempts: attempts.map((attempt) => attempt.map((c) => DIFFICULTY_CLASSES.indexOf(c) + 1)),
    }),
  }).catch(() => {});
}

function checkGuessCallback() {
  let chosenTiles = Array.from(document.getElementsByClassName("chosen"));
  if (chosenTiles.length != 4) {
    return;
  }
  let chosenValues = chosenTiles.map((x) => findParentWithClass(x, "card").getAttribute("answer-text")).sort(textSort);
  if (attemptsSet.map((x) => JSON.stringify(x)).includes(JSON.stringify(chosenValues))) {
    showMessage("Already Guessed!");
    return;
  }
  // Hooked here rather than inside gameOver() itself: gameOver() also runs
  // during the IndexedDB replay path (getThePuzzle() reconstructing an
  // already-completed prior session via direct checkGuess() calls, which
  // never goes through this function) — hooking the live-guess entry point
  // instead means reloading or revisiting an already-finished puzzle never
  // re-submits a duplicate result, only a genuinely just-now completion does.
  let wasAlreadyEnded = gameEnded;
  checkGuess(chosenValues);
  persistGameState();
  if (!wasAlreadyEnded && gameEnded) {
    submitResult();
  }
}

// Pure game-state mutator — used both for a live guess (via
// checkGuessCallback above) and to replay a previously-saved game on load,
// same split as connections.js's checkGuess/checkGuessCallback.
function checkGuess(chosenValues) {
  attemptsSet.push(chosenValues);
  for (let i = 0; i < puzzleSolution.groups.length; i++) {
    let group = puzzleSolution.groups[i];
    let groupValues = group.words.slice().sort(textSort);
    if (JSON.stringify(chosenValues) == JSON.stringify(groupValues)) {
      let colorClass = DIFFICULTY_CLASSES[i];
      attempts.push([colorClass, colorClass, colorClass, colorClass]);
      Array.from(document.getElementsByClassName("card"))
        .filter((x) => chosenValues.includes(x.getAttribute("answer-text")))
        .forEach((x) => x.remove());
      drawAnswer(group, colorClass);
      succeedPuzzleIfAppropriate();
      return;
    }
  }

  let thisGuess = [];
  for (let chosenValue of chosenValues) {
    for (let i = 0; i < puzzleSolution.groups.length; i++) {
      let groupValues = puzzleSolution.groups[i].words.slice().sort(textSort);
      if (groupValues.includes(chosenValue)) {
        thisGuess.push(DIFFICULTY_CLASSES[i]);
      }
    }
  }
  let howClose = DIFFICULTY_CLASSES.map((x) => thisGuess.reduce((t, y) => t + (x == y ? 1 : 0), 0));
  if (howClose.includes(3)) {
    showMessage("One away...");
  } else {
    showMessage("Wrong...");
  }
  attempts.push(thisGuess);

  let remainingGuess = Array.from(document.getElementsByClassName("guess"));
  let removeThisOne = remainingGuess.pop();
  removeThisOne.remove();
  if (remainingGuess.length == 0) {
    failPuzzle();
  } else {
    Array.from(document.getElementsByClassName("card"))
      .filter((x) => chosenValues.includes(x.getAttribute("answer-text")))
      .forEach((x) => x.classList.add("shake"));
    setTimeout(removeAllShakes, 500);
  }
}

function removeAllShakes() {
  Array.from(document.getElementsByClassName("shake")).forEach((x) => x.classList.remove("shake"));
}

function failPuzzle() {
  gameOver(false);
}

function succeedPuzzleIfAppropriate() {
  if (!document.querySelector("div.card")) {
    gameOver(true);
  }
}

function deselectAll() {
  Array.from(document.querySelectorAll("div.chosen")).forEach((x) => x.classList.remove("chosen"));
}

function gameOver(success) {
  Array.from(document.getElementsByClassName("card")).forEach((x) => x.remove());
  for (let i = 0; i < DIFFICULTY_CLASSES.length; i++) {
    let colorClass = DIFFICULTY_CLASSES[i];
    if (!document.querySelector(`div.${colorClass}`)) {
      drawAnswer(puzzleSolution.groups[i], colorClass);
    }
  }
  gameEnded = true;
  gameWon = success;
  document.body.classList.add("game-ended");
  deselectAll();
  gameControls.style.display = "none";
  guesses.style.display = "none";
  results.style.display = "block";
  let textIndex = success ? attempts.length - 4 : TEXT.length - 1;
  displayText.innerText = TEXT[textIndex];
  dateText.innerText = datePicker.value;
  resultBlocks.innerHTML = "";
  for (let attempt of attempts) {
    drawAttempt(attempt);
  }
  persistGameState();
  // setFocus() calls updateSoftkeys() itself (the center label depends on
  // what's focused), so no separate call needed here.
  setFocus(document.getElementById("results-content"));
}

function drawAttempt(attempt) {
  let row = document.createElement("div");
  row.classList.add("row");
  for (let item of attempt) {
    let cell = document.createElement("div");
    cell.classList.add("result-cell");
    cell.classList.add(item);
    row.appendChild(cell);
  }
  resultBlocks.appendChild(row);
}

// --- Toasts ------------------------------------------------------------
let fadeTimeout = undefined;
let hideTimeout = undefined;
function showMessage(messageText) {
  clearTimeout(fadeTimeout);
  clearTimeout(hideTimeout);
  commentSpan.innerText = messageText;
  comment.classList.remove("slow-fade");
  comment.style.display = "flex";
  fadeTimeout = setTimeout(startFade, 1000);
}
function interruptMessage() {
  clearTimeout(fadeTimeout);
  clearTimeout(hideTimeout);
  comment.style.display = "none";
}
function startFade() {
  comment.classList.add("slow-fade");
  hideTimeout = setTimeout(hideMessage, 2000);
}
function hideMessage() {
  comment.style.display = "none";
}

function shuffleTiles() {
  let cards = Array.from(document.getElementsByClassName("card"));
  for (let card of shuffle(cards)) {
    puzzleEl.appendChild(card);
  }
}

function setDate(event) {
  let requested = event.target.value;
  if (loading) {
    datePicker.value = previousDatePickerValue;
    return;
  }
  if (requested > todayString() || (firstPuzzleDate && requested < firstPuzzleDate)) {
    datePicker.value = previousDatePickerValue;
    showMessage("No puzzle for that date!");
    return;
  }
  previousDatePickerValue = requested;
  clearThePuzzle();
  getThePuzzle();
}

// --- KaiOS D-pad navigation + softkey bar -----------------------------
// Focus tracking is adapted from kaios-calorie-counter/frontend-v3/app.js's
// selectables()/focused()/setFocus() (a nav-selectable/nav-selected
// attribute pair on real elements, no invisible proxy inputs needed) — much
// simpler than dumbphone-apps' utils.js framework. The 2D row/column
// movement math in moveFocus() below is the one piece actually ported from
// dumbphone-apps' connections.js (connectionsArrowCallback): kaios-calorie-
// counter's own moveFocus() is a flat 1D list (up/down through a vertical
// screen), not grid-aware, and this puzzle is a 4-column grid.
// offsetParent is null for anything with display:none (itself or an
// ancestor) — cheap, standard "is this actually rendered" check, same one
// kaios-calorie-counter/frontend-v3/app.js's isVisible() uses. Needed now
// that #results-content is nav-selectable: it sits earlier in document
// order than #wrap-date-picker (the #results panel comes first in
// index.html), so without this filter it wins selectables()[0] and grabs
// initial focus even while #results is still display:none.
function isVisible(el) {
  return el.offsetParent !== null;
}

function selectables() {
  return Array.from(document.querySelectorAll('[nav-selectable="true"]')).filter(isVisible);
}

function focused() {
  return document.querySelector('[nav-selected="true"]');
}

// The fixed-position softkey bar (see stylesheet.css) sits on top of
// whatever's scrolled to the bottom of the viewport — the browser's default
// scroll-into-view-on-focus has no idea that strip is there, so a tile in
// the bottom rows can end up scrolled to just behind it, focused but
// invisible. Ported from kaios-calorie-counter/frontend-v3/app.js's
// SOFTKEY_H/scrollToVisible for the same reason.
const SOFTKEY_H = 30;

function scrollToVisible(el) {
  let rect = el.getBoundingClientRect();
  if (rect.bottom + SOFTKEY_H > window.innerHeight) {
    window.scrollBy(0, rect.bottom + SOFTKEY_H - window.innerHeight);
  } else if (rect.top < 0) {
    window.scrollBy(0, rect.top);
  }
}

function setFocus(el) {
  if (!el) {
    return;
  }
  let prev = focused();
  if (prev) {
    prev.removeAttribute("nav-selected");
  }
  el.setAttribute("nav-selected", "true");
  if (!el.hasAttribute("tabindex")) {
    el.setAttribute("tabindex", "-1");
  }
  el.focus();
  scrollToVisible(el);
  // The softkey center label depends on what's currently focused (e.g.
  // "Share" only while #results-content is selected) — keep it in sync
  // every time focus moves, not just when gameEnded flips.
  updateSoftkeys();
}

// Groups the current nav-selectable elements into rows by on-screen
// Y-position (works for any mix of shapes — the single full-width date
// wrapper naturally forms its own one-element row above the tile grid,
// no special-casing needed), then moves within that row (left/right) or to
// the same column index in the row above/below (up/down), wrapping in
// every direction — same algorithm connections.js's
// connectionsArrowCallback uses for the same 4-column tile grid.
function moveFocus(key) {
  let items = selectables();
  if (!items.length) {
    return;
  }
  let current = focused();
  if (!current || !items.includes(current)) {
    setFocus(items[0]);
    return;
  }

  let rows = {};
  for (let item of items) {
    let y = item.getBoundingClientRect().y.toString();
    if (!rows[y]) {
      rows[y] = [];
    }
    rows[y].push(item);
  }
  // Object.keys() doesn't guarantee visual order here: JS always sorts
  // integer-like string keys numerically ascending *first*, then falls back
  // to insertion order for the rest — and CSS Grid frequently gives some
  // rows an exact-pixel Y (an "integer-like" key) while others land on a
  // sub-pixel fractional Y depending on rounding, so the two groups get
  // sorted independently and interleaved unpredictably. Explicit numeric
  // sort avoids that entirely.
  let rowNames = Object.keys(rows).sort((a, b) => parseFloat(a) - parseFloat(b));
  let rowName = current.getBoundingClientRect().y.toString();
  let row = rows[rowName];
  let rowIndex = row.indexOf(current);

  if (key === "ArrowUp" || key === "ArrowDown") {
    // Move to exactly the adjacent row (wrapping at the ends) and clamp the
    // column index to whatever that row actually has — not every row is 4
    // wide (the date-picker wrapper, answer rows, and the submit-group link
    // are each their own 1-item row). Looking for an *exact* index match
    // and skipping past rows without one (the previous approach) meant
    // those 1-item rows were only ever reachable from column 0, since
    // that's the only column index they have.
    let rowIdx = rowNames.indexOf(rowName);
    rowIdx = rowIdx + (key === "ArrowUp" ? -1 : 1);
    rowIdx = rowIdx < 0 ? rowIdx + rowNames.length : rowIdx;
    rowIdx = rowIdx > rowNames.length - 1 ? rowIdx - rowNames.length : rowIdx;
    let targetRow = rows[rowNames[rowIdx]];
    setFocus(targetRow[Math.min(rowIndex, targetRow.length - 1)]);
  } else {
    rowIndex = rowIndex + (key === "ArrowLeft" ? -1 : 1);
    rowIndex = rowIndex < 0 ? rowIndex + row.length : rowIndex;
    rowIndex = rowIndex > row.length - 1 ? rowIndex - row.length : rowIndex;
    setFocus(row[rowIndex]);
  }
}

// The NYT-Connections-style share text: the site link, the puzzle's own id
// (a stored field, not derived — see backend/lambda/fourplay/puzzle.py's
// _next_puzzle_id()), the calendar date played (datePicker.value, not
// puzzleSolution.date — see submitResult()'s comment above for why), then
// one emoji row per attempt — the same attempts/DIFFICULTY_CLASSES data
// drawAttempt() already renders as .result-cell divs, just as text instead
// of DOM.
function buildShareText() {
  let squares = attempts.map((attempt) => attempt.map((c) => DIFFICULTY_EMOJI[c]).join("")).join("\n");
  return `${SITE_URL}\nFourplay #${puzzleSolution.id}\n${datePicker.value}\n\n${squares}`;
}

// sms: with no recipient pre-fills just the body — the exact separator
// before "body=" is the one real cross-platform quirk here: iOS requires
// "&", every other platform (Android, KaiOS's Gecko-based browser, etc.)
// wants "?".
function shareText(text) {
  let isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  let separator = isIOS ? "&" : "?";
  window.location.href = `sms:${separator}body=${encodeURIComponent(text)}`;
}

function shareViaSms() {
  shareText(buildShareText());
}

function toTitleCase(word) {
  return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
}

// Same shape as buildShareText() above, but the category + word list for
// each group *solved so far* instead of the emoji recap grid — reads from
// puzzleSolution.groups (clean, original-case data) filtered down to
// whichever groups already have a drawn .answer row, the same "is this one
// solved" check gameOver() uses, rather than the full spoiler solution.
function buildAnswersShareText() {
  let solved = puzzleSolution.groups.filter((group, i) => document.querySelector(`.answer.${DIFFICULTY_CLASSES[i]}`));
  let groupsText = solved.map((group) => `${group.category}\n${group.words.map(toTitleCase).join(", ")}`).join("\n\n");
  return `${SITE_URL}\nFourplay answers #${puzzleSolution.id}\n${datePicker.value}\n\n${groupsText}`;
}

function shareAnswersViaSms() {
  shareText(buildAnswersShareText());
}

// Generic "activate the currently-focused nav-selectable element" — same
// fallback shape as kaios-calorie-counter's interact()/el.click(). Four
// elements need special-casing instead of a plain .click(): the date
// wrapper (focusing the real input is what pops the picker, see
// wireDatePickerWrapper below), .card tiles (their click listener lives on
// the tile's *inner* div, not this nav-selectable outer one, so a plain
// .click() on the outer div wouldn't do anything), and #results-content /
// #answers (share instead of a no-op click, gated by the SHARE_*_ENABLED
// flags at the top of the file — falls through to the harmless .click()
// no-op when disabled, same as before either feature existed). Everything
// else — e.g. the "Submit a group" link — is a real interactive element
// already, so .click() on it directly (a real <a>, so this navigates it)
// is correct.
function interact(el) {
  if (!el) {
    return;
  }
  if (el.id === "wrap-date-picker") {
    datePicker.focus();
  } else if (el.classList.contains("card")) {
    toggleChosen(el.querySelector("div"));
  } else if (el.id === "results-content" && SHARE_RESULTS_ENABLED) {
    shareViaSms();
  } else if (el.id === "answers" && SHARE_ANSWERS_ENABLED) {
    shareAnswersViaSms();
  } else {
    el.click();
  }
}

// When disabled, strips the static "Share" button out of index.html's
// #results-content entirely (rather than just leaving it un-wired) so
// there's no dead-looking button sitting around.
function wireResultsContentClick() {
  let container = document.getElementById("results-content");
  if (!SHARE_RESULTS_ENABLED) {
    let hint = container.querySelector(".share-hint");
    if (hint) {
      hint.remove();
    }
    return;
  }
  container.classList.add("share-enabled");
  container.addEventListener("click", shareViaSms);
}

function wireAnswersClick() {
  if (!SHARE_ANSWERS_ENABLED) {
    return;
  }
  answers.classList.add("share-enabled");
  answers.addEventListener("click", shareAnswersViaSms);
}

// Softkey labels reflect whatever's currently focused — not just gameEnded
// — since a few elements have one single obvious center action and nothing
// meaningful for the other two keys: the date wrapper ("Change"), the
// submit-group link ("Open"), and #results-content/#answers ("Share", only
// when its SHARE_*_ENABLED flag is on). Anything else (a tile, a disabled
// share area, or nothing yet) falls back to the normal Select/Guess/
// Deselect All trio, blanked out once the game's ended.
function updateSoftkeys() {
  let current = focused();
  let id = current ? current.id : null;
  let left = document.getElementById("sk-left");
  let center = document.getElementById("sk-center");
  let right = document.getElementById("sk-right");
  let onShareableResults = id === "results-content" && SHARE_RESULTS_ENABLED;
  let onShareableAnswers = id === "answers" && SHARE_ANSWERS_ENABLED;

  if (id === "wrap-date-picker") {
    left.textContent = "";
    center.textContent = "Change";
    right.textContent = "";
  } else if (id === "submit-group-anchor") {
    left.textContent = "";
    center.textContent = "Open";
    right.textContent = "";
  } else if (onShareableResults || onShareableAnswers) {
    left.textContent = "";
    center.textContent = "Share";
    right.textContent = "";
  } else {
    left.textContent = gameEnded ? "" : "Deselect All";
    center.textContent = gameEnded ? "" : "Select";
    right.textContent = gameEnded ? "" : "Guess";
  }
}

// The nav-selectable D-pad stop for the date field is this wrapper, not
// #date-picker itself — landing on the real input during arrow-key
// traversal would pop its native picker immediately (see
// kaios-calorie-counter/frontend-v3/app.js:1721-1730's identical reasoning
// for the same pattern there). Pressing Enter while the wrapper is focused
// (see handleKeydown below) calls .focus() on the real input instead, which
// is what actually triggers KaiOS's full-screen date picker on Gecko.
function wireDatePickerWrapper() {
  document.getElementById("wrap-date-picker").addEventListener("click", () => datePicker.focus());
}

// Named so both the physical-key handling in handleKeydown below and the
// on-screen softkey <label> click listeners (wireSoftkeyClicks) share one
// implementation — the labels exist mainly so this can be tested with a
// mouse on a laptop without simulating real SoftLeft/SoftRight key events.
function activateSoftLeft() {
  deselectAll();
}

function activateSoftCenter() {
  interact(focused());
}

function activateSoftRight() {
  checkGuessCallback();
}

function wireSoftkeyClicks() {
  document.getElementById("sk-left").addEventListener("click", activateSoftLeft);
  document.getElementById("sk-center").addEventListener("click", activateSoftCenter);
  document.getElementById("sk-right").addEventListener("click", activateSoftRight);
}

function handleKeydown(event) {
  // Let the native date input handle its own arrow-key segment navigation
  // (day/month/year) once it's actually focused — only the wrapper (not
  // yet drilled into the real control) goes through grid navigation.
  if (event.target === datePicker) {
    return;
  }

  if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) {
    event.preventDefault();
    moveFocus(event.key);
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    activateSoftCenter();
    return;
  }
  if (event.key === "SoftLeft") {
    event.preventDefault();
    activateSoftLeft();
    return;
  }
  if (event.key === "SoftRight") {
    event.preventDefault();
    activateSoftRight();
  }
}

// --- Boot --------------------------------------------------------------
computeContrastColors();
wireDatePickerWrapper();
wireResultsContentClick();
wireAnswersClick();
wireSoftkeyClicks();
document.addEventListener("keydown", handleKeydown);
window.addEventListener("resize", syncAnswerRowHeight);

(async function init() {
  firstPuzzleDate = await fetchFirstPuzzleDate();
  if (firstPuzzleDate) {
    datePicker.min = firstPuzzleDate;
  }
  datePicker.max = todayString();
  datePicker.value = pickInitialDate();
  previousDatePickerValue = datePicker.value;
  clearThePuzzle();
  updateSoftkeys();
  getThePuzzle();
})();
