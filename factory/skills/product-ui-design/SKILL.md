---
name: product-ui-design
description: Design and build the visual layer of a web product — design system, page layouts, components, icons and illustrations — and verify the rendered layout in Chromium. Use when designing, styling, illustrating or reviewing any web UI.
---

# Product UI design

You are the product designer and you own the visual layer: the stylesheet, design tokens,
icons, illustrations and the logo. The goal is a UI a reviewer would screenshot for a
portfolio: calm, current, obviously deliberate. Not a 2015 admin template.

## 1. Before drawing: decide the system

Write `design/system.md` first, then build everything from it.

- **Type.** One family that ships in the container (a system stack, or a variable font file
  copied into the image; never a font CDN). Scale: 13 / 15 / 17 / 22 / 30 / 40 px, weights
  400/500/650. Tabular numbers (`font-variant-numeric: tabular-nums`) for every amount, time
  and count. Money is the hero of a money screen: big, tabular, the currency smaller and muted.
- **Colour.** A neutral ramp with a slight hue (not pure grey), one accent, plus success,
  warning and danger. Background a step off white, surfaces white, borders hairline
  (1 px, ~8% ink). Text contrast ≥ 4.5:1. Define light and dark tokens; respect
  `prefers-color-scheme`.
- **Space and shape.** 4 px base, layout steps 8/12/16/24/32/48. One radius for controls
  (10–12 px), one for cards (16–20 px). Shadows soft and rare; borders do most separation.
- **Components, named.** Publish the class vocabulary the markup will use (for example
  `.page`, `.page-header`, `.card`, `.card-title`, `.stat`, `.field`, `.input`,
  `.segmented`, `.btn`, `.btn-primary`, `.btn-quiet`, `.list`, `.list-row`, `.badge`,
  `.empty`, `.skeleton`, `.toast`, `.banner`). The builder marks up with these names; you
  style them.

## 2. Current patterns, not 2015 ones

- Single clear primary action per view; secondary actions quiet (ghost/text buttons).
- Short choices (2–4 options) as segmented controls or radio cards, not a `<select>`.
- Labels above inputs, 14–15 px, helper text 13 px muted, errors inline under the field.
- Lists as rows with a leading avatar/icon, primary + secondary line, trailing amount and
  status badge; not tables on phones.
- Every async action has pending, success and failure feedback on screen (spinner in the
  button, toast or inline banner). Every list has an empty state with an illustration and
  one sentence plus the next action. Loading uses skeletons that match the final layout.
- Cards in one row share height (grid with `align-items: stretch`; actions pinned to the
  bottom with `margin-top: auto` in a flex column). A row of uneven cards is a bug.
- Phone first: at 375 px one column, 16 px gutters, controls ≥ 44 px tall, sticky primary
  action where a form is long. Desktop: content max-width 1040–1200 px, centred.
- Motion: 150–200 ms ease-out on hover/press/appear; respect `prefers-reduced-motion`.
- Focus rings visible (2 px accent outline, offset 2 px). Never remove outlines.

## 3. Pictures

Draw your own artwork as SVG and ship it in the container:
- a simple logo mark and wordmark;
- a consistent icon set (24 px grid, 1.75 px stroke, round caps) for navigation and actions;
- one illustration per empty state and for sign-in, in two or three tokens' colours, flat,
  geometric, no text inside.
Inline icons via `<svg>` sprites or files under the static assets folder; give decorative
images empty `alt` and meaningful ones a short `alt`.

## 4. Mockups

Before styling, draw each screen and state as SVG at 375 and 1280 wide
(`<screen>-<state>-<width>.svg`), real content, every element the spec locates labelled with
its identifier. Mockups show the system above, not a wireframe.

## 5. Verify in Chromium, then fix, until clean

Run the factory's layout checker against the running service for every screen, at 375 and
1280, signed in where needed (`--storage KEY=VALUE` puts a session token in localStorage):

    layout_check.py --base-url <url> --page / --page /<other> --storage <key>=<token> --out <dir>

It reports `overflow`, `offscreen`, `overlap`, `clipped`, `uneven-row`, `ragged-row`,
`small-target`, `small-text` and console errors, and saves screenshots. Fix every finding in
the stylesheet (or ask the builder for a markup change), rerun, and repeat until it prints
`LAYOUT: clean`. Then look at every screenshot against your mockup and fix what is off by more
than a spacing step. Report findings you could not fix and why.
