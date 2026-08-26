# The KaiOS `position: fixed` softkey-bar displacement

## TL;DR

On real KaiOS hardware (confirmed on both a KaiOS 3.1 device and a KaiOS 4.0
device, Gecko 84 and Gecko 123 respectively — not an engine-version-specific
bug), the fixed-position softkey bar (`#softkey`, `position: fixed; left: 0;
right: 0; bottom: 0;`) would render **too wide and shifted down**, specifically
right after D-pad focus landed on the **last column** of the 4×4 puzzle grid —
severely enough that the bar's text scrolled below the visible viewport and
became functionally unusable. Neither device is reproducible from a desktop
browser at any window size, and neither device has remote/USB devtools access,
so this took several rounds of wrong hypotheses before landing on real
evidence. The eventual fix: **stop trusting the browser's own `left/right/
bottom: 0` fixed-position math for this element and set its width/vertical
position explicitly from JS, using `window.innerWidth`/`innerHeight`** — see
`positionSoftkeyBar()` in `frontend/js/puzzle.js`.

## Symptom

`#softkey` is the fixed bottom bar labeling the phone's physical
SoftLeft/center/SoftRight keys, shown only at `≤240px` width
(`stylesheet.css`'s `KAIOS_WIDTH_BREAKPOINT`). Reported: navigate the puzzle
grid with the D-pad; everything looks normal until focus reaches a tile in
the last (4th) column, at which point the softkey bar visibly drops down and
mostly/entirely leaves the screen — on a short-enough viewport, `#settings-
link` (normally scrolled below the fold) becomes visible in the space where
the softkey bar should be, because the bar is no longer occupying it.

Not reproducible in Chromium at any tested viewport width (232–248px), with
the exact reported puzzle content and column reconstructed pixel-for-pixel.

## What we ruled out, in the order we ruled it out

1. **`.nav-selectable-ad`'s hard `min-width: 240px`.** Real, independently
   confirmed bug (the ad banner couldn't shrink below 240px, so on any real
   screen narrower than exactly 240px it forced genuine horizontal page
   overflow, unconditionally, from the very first paint — nothing to do with
   which column was focused). Fixed by dropping `min-width` and changing
   `max-width: 240px` to `max-width: 100%`. Worth keeping regardless of
   whether it was the cause of *this* symptom, but retesting after this fix
   shipped showed the softkey bug was still there — so it wasn't the (whole)
   story.

2. **Non-inset `box-shadow` bleeding past the grid's right edge.** Theory: the
   last column's tile sits flush against `#puzzle`'s right edge with no
   sibling gap to absorb the halo, and non-inset `box-shadow`'s contribution
   to *scrollable* (as opposed to pure paint) overflow has a real history of
   cross-engine inconsistency. Converted every outward halo where it was
   visually safe to (`.card`, `.input-wrap`'s sibling elements had to stay
   outward — see below) to `inset`. This *reduced* the reported displacement
   magnitude but did not eliminate it — the first sign that more than one
   thing might be going on, or that this theory was only partially right (or
   wrong and coincidentally correlated).

   Side-lesson from this step: **`inset` box-shadow is invisible on any
   element whose child content fills its box edge-to-edge with zero
   padding** (paint order puts children on top of an element's own inset
   shadow). `.input-wrap` (wraps a native `<input type="date">`) and
   `.nav-selectable-ad` (wraps a full-bleed `<img>`) both went invisible when
   converted to inset and had to be reverted back to outward — only
   `.card > div` (which has real padding around its text) actually shows an
   inset ring correctly. Check an element's own padding before assuming
   inset is a safe swap.

3. **CSS Grid `1fr` track rounding** (the last track absorbing a rounding
   remainder, common across grid implementations). No evidence for this in
   Chromium at any tested width, and ultimately contradicted by the real
   on-device data (see below) — the grid's own rect was byte-for-byte
   identical between the working and broken states.

## The diagnostic method — no remote devtools, so build one that screenshots

With no ADB/`about:debugging` access to either device, guessing CSS fixes and
asking for a retest was slow and each round only produced a qualitative
"still broken" / "less broken." What actually cracked it: a small on-screen,
screenshot-friendly diagnostic readout — a `<pre>` element showing real
`getBoundingClientRect()` numbers directly on the page, toggleable from the
Settings menu (a URL query param doesn't work for a packaged KaiOS app with a
fixed `start_url` — had to be an in-app toggle, persisted to `localStorage`
so it survives relaunching the packaged app). It showed, refreshed on every
focus change:

- `window.innerWidth`/`innerHeight`/`devicePixelRatio`
- `document.documentElement.scrollWidth` vs `clientWidth`
- **`#softkey`'s own rect directly** — the actual symptom, not an inferred
  upstream cause
- `#puzzle`'s rect and the currently-focused element's rect — the leading
  suspects at the time, measured instead of assumed

This is the technique worth reusing on any future device with no remote
debugging: don't infer from CSS reasoning alone, put the real numbers where a
screenshot can capture them, in whatever the user can already reproduce.
Removed once the bug was confirmed fixed — see "Diagnostic tooling" below for
what to rebuild if this class of bug ever recurs.

## What the readout actually showed — the real root cause

Two consecutive screenshots, same puzzle, same viewport, focus moved from a
working column to the last one:

```
# Working (2nd-to-last column):
viewport: 240x294 dpr=1
scrollWidth=240 clientWidth=240 overflow=0
#softkey: L0.0 R240.0 T264.0 B294.0 W240.0 H30.0
#puzzle:  L4.0 R236.0 T27.0 B261.0 W232.0 H234.0

# Broken (last column):
viewport: 240x294 dpr=1
scrollWidth=257 clientWidth=240 overflow=17
#softkey: L0.0 R256.6 T284.3 B314.3 W256.6 H30.0
#puzzle:  L4.0 R236.0 T27.0 B261.0 W232.0 H234.0
```

Two things this proved, conclusively, that no amount of further CSS
speculation could have:

1. **`#puzzle`'s rect never changed at all.** The grid, the tiles, the
   box-shadow — none of it differs between the working and broken states.
   Everything chased in steps 2–3 above was never the cause of this
   particular symptom.
2. **`#softkey` itself is what's wrong**, specifically its own
   `position: fixed; left: 0; right: 0; bottom: 0;` resolution. Its width
   blew up from 240 to 256.6 (+16.6px, which is where basically all of the
   17px `scrollWidth` overflow came from) and it shifted down 20.3px (top
   264→284.3, bottom 294→314.3), while its declared `height: 30px` stayed
   exactly 30 throughout. Both deltas are ~6.9% of their respective axis
   (16.6/240, 20.3/294) — a proportional blow-up specific to this element's
   fixed-positioning containing block, **decoupled from
   `window.innerWidth`/`innerHeight`**, which the very same snapshot shows
   reporting the correct `240×294` the whole time.

No confirmed explanation for *why* Gecko's fixed-positioning containing block
would diverge from the visual viewport specifically once focus reaches the
last grid column, on two different Gecko major versions. Mobile engines have
a real history of exactly this category of bug (a fixed element's containing
block resolving against a layout viewport that differs from the visual one,
often during scroll/on-screen-UI-chrome transitions) — plausible, not proven.
Same posture as the calorie-counter `fetch()` mystery: we have a working fix,
not a confirmed mechanism, and we're not re-litigating it further unless it
resurfaces.

## The fix

`window.innerWidth`/`innerHeight` were confirmed reliable at the exact
moment the bug was visible (same debug snapshot, same instant). So: stop
trusting the browser's own `left/right/bottom: 0` resolution for this one
element, and set its position explicitly from JS using the numbers already
confirmed correct.

```js
// frontend/js/puzzle.js
function positionSoftkeyBar() {
  if (window.innerWidth > KAIOS_WIDTH_BREAKPOINT) {
    return; // #softkey is display:none above this width anyway
  }
  let softkey = document.getElementById("softkey");
  softkey.style.width = window.innerWidth + "px";
  softkey.style.right = "auto";
  softkey.style.top = window.innerHeight - 30 + "px";
  softkey.style.bottom = "auto";
}
```

`left: 0` and `height: 30px` were left alone — both measured correctly in
*every* broken screenshot, only width and vertical position needed
overriding. The CSS `position: fixed; left: 0; right: 0; bottom: 0;` stays in
place as the baseline (correct everywhere else, including desktop where this
function is a no-op); the inline styles just override it specifically where
it's been confirmed wrong.

Called in three places: once at boot, on `window`'s `resize`, and — the one
that actually matters most — inside `setFocus()`, since every broken
screenshot was captured right after a D-pad focus change.

Confirmed fixed on both real devices after deploying this. **A very subtle
horizontal scrollbar can still appear** in the same last-column scenario —
accepted as a non-issue (barely visible, doesn't affect usability, and
`#softkey`'s own position no longer moves because of it).

## Diagnostic tooling — removed after confirmation, here's how to rebuild it

Unlike `kaios-calorie-counter`'s equivalent tooling (kept in place
permanently — see `kaios-fetch-vs-xhr-cors-mystery.md`), this project's
debug readout was deliberately temporary and was removed once the fix was
confirmed, since it added a Settings menu button and an unstyled on-screen
overlay to a real shipping app. If a similarly hard-to-reproduce on-device
bug shows up again with no remote debugging access, rebuild something like:

- A `<pre>` element, `display: none` by default, shown via a `body` class.
- A toggle **reachable from in-app UI** (Settings menu button, not a URL
  query param — a packaged KaiOS app has a fixed `start_url` nobody can add
  a param to), persisted to `localStorage` so it survives relaunching the
  app, not just the current page load.
- Populate it with `getBoundingClientRect()` of whatever's suspected, plus
  raw `window.innerWidth`/`innerHeight`/`scrollWidth`/`clientWidth` — refresh
  it on whatever event actually triggers the bug (here: every focus change),
  not just on a timer, so it's already correct by the moment a screenshot is
  taken.
- Ask for a screenshot at the exact moment the bug is visible, not a
  description of it — real numbers beat another round of guessing every
  time.

## Addendum: a second, distinct bug — paint, not layout (found right after this one shipped)

The fix above wasn't the end of it. Almost immediately, a *different*
trigger surfaced: pressing **ArrowDown while focused on the bottom-right
tile** (last row, last column) moves focus to `#settings-anchor` and
triggers a real page scroll via `scrollToVisible()` — unlike the bug above,
where `#puzzle`'s rect never moved between working/broken states, this one
does involve an actual scroll. Reported symptom: the softkey bar visibly
displaced *upward*, badly enough that the ad-placeholder's background color
became visible underneath it.

**The debug readout (still active in this build) showed `#softkey`'s own
`getBoundingClientRect()` as numerically correct** — `L0 R240 T264 B294
W240 H30`, exactly matching a 240×294 viewport — in the very screenshot
reported as broken. That looked like a flat contradiction of the visual
report, until a second, critical fact came out:

### The screenshot doesn't reliably show what the screen shows

On this device, a captured screenshot is not guaranteed to match the live,
composited screen at the same moment — confirmed by comparing an actual
photo of the live device against a screenshot taken at what should have
been the same instant; they showed different scroll positions / different
visible content. **This means screenshot pixels can't be trusted as
ground truth for a rendering bug on this hardware.** The on-screen debug
*text* readout is a separate matter — it reads real DOM/JS state
(`getBoundingClientRect()`, `window.innerWidth`, etc.) at the moment it's
computed, which is trustworthy — but the screenshot image capturing that
text is only as reliable as the mechanism taking it, which apparently can
lag or diverge from the actual compositor output. If this class of bug
comes up again: trust the numbers a debug readout prints, but confirm the
*visual* symptom by asking for a description from the user's own eyes (or a
phone photo of the live screen), not by trusting a screenshot's pixels.

### Root cause: correct layout, stale paint

Putting the two facts together — `getBoundingClientRect()` correct, but the
user visually confirming (live, by eye) that the bar really was
misplaced — pointed at a **paint/compositing** bug, not a layout bug.
`getBoundingClientRect()` only reflects computed layout; a real device can
still display stale pixels for a `position: fixed` element if its
compositor layer fails to repaint correctly after a scroll. This is a known
category of bug on constrained mobile engines: fixed-position elements are
composited on a separate pathway during scroll, distinct from the ordinary
in-flow/absolute repaint path — and that pathway is the one shown to be
unreliable here. (Same posture as everywhere else in this file: a working
fix, not a confirmed browser-internals mechanism.)

### The fix: stop using `position: fixed` for `#softkey` at all

Switching `#softkey` to `position: absolute` takes it off that
fixed-during-scroll compositing pathway entirely — it now repaints through
the same ordinary path as every other positioned element on the page, which
has never shown this problem. Since `absolute` positions relative to the
*document* rather than the viewport, `positionSoftkeyBar()`'s `top` formula
just needs `window.scrollY` added back in, and needs to re-run on scroll,
not only on focus/resize/boot:

```css
/* stylesheet.css */
#softkey {
  position: absolute; /* not fixed -- see positionSoftkeyBar() in puzzle.js */
  bottom: 0;
  left: 0;
  right: 0;
  /* ...unchanged otherwise */
}
```

```js
// puzzle.js
function positionSoftkeyBar() {
  if (window.innerWidth > KAIOS_WIDTH_BREAKPOINT) {
    return;
  }
  let softkey = document.getElementById("softkey");
  softkey.style.width = window.innerWidth + "px";
  softkey.style.right = "auto";
  softkey.style.top = window.scrollY + window.innerHeight - SOFTKEY_H + "px";
  softkey.style.bottom = "auto";
}

// ...
window.addEventListener("resize", positionSoftkeyBar);
window.addEventListener("scroll", positionSoftkeyBar); // new
positionSoftkeyBar();
```

`#softkey` is a direct child of `<body>`, and neither `html` nor `body` sets
`position`, so `absolute` resolves against the initial containing block —
the same reference frame `fixed` used, so no ancestor changes were needed.
`body`'s `padding-bottom`/`margin-bottom` reservation (so page content
doesn't render underneath the bar) is unaffected — `absolute` is
out-of-flow exactly like `fixed` was.

Not yet reconfirmed on-device as of this writing — pending a live retest
(by eye or phone photo, not a screenshot) of the exact bottom-right-tile →
ArrowDown → Settings sequence.
