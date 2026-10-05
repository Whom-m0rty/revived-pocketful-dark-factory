# FACTORY

Four Claude Code seats in one Band room build a service stage by stage from a written
specification. One seat builds behaviour and markup; an independent seat decides what
"meets the spec" means and checks it from behind an access barrier; a designer owns the
look — design system, stylesheet, icons and illustrations — and verifies the rendered layout
in Chromium; a coordinator keeps the work moving, reviews the code and settles disputes. Everything here is generic:
the mandates name no endpoint, field or error code of any track.

We chose this design by measuring alternatives on the pocketful track with the shipped
tests hidden from the factory (and then used as a yardstick), plus independent hidden
suites we wrote for stages 3 and 4. The numbers are below.

## Seats

| Seat | Model | Owns | Can read holdout |
|---|---|---|---|
| coordinator | claude-sonnet-5 | handoffs, room board, disputes, code review, closing the stage | no (denied by config) |
| builder | claude-opus-5-5 | behaviour and markup in `stage-N/`, built whole | no (denied by config) |
| tester | claude-opus-5-5 | interface contract, holdout scenarios, running the gate | yes |
| designer | claude-opus-5-5 | design system, stylesheet, SVG logo/icons/illustrations, mockups, layout verification | no (denied by config) |

Mandates: `mandates/<seat>.md`. Each seat runs Claude Code with an isolated config (no
personal memory, plugins or MCP servers), its own git identity, and `bypassPermissions`.

## How a stage runs

1. The human dispatches one task to the coordinator: track, stage, paths, budget, full spec.
2. The coordinator copies the previous stage folder forward, puts one task per seat on the
   room board, and in parallel asks the tester for the contract and the designer for the
   design. The designer first publishes `design/system.md` (tokens and the component class
   names the markup must use), then mockups, then the stylesheet and artwork.
3. The tester commits `contract.json` (one JSON Schema per operation, error bodies included)
   in its own short turn. The builder starts the moment it arrives; the tester then writes
   holdout scenarios outside the repository.
4. The builder builds the whole stage, API first, then the screens' markup and behaviour with
   the designer's class names (no visual CSS of its own), checks its work against the
   contract, commits and reports.
5. On each builder commit the tester runs the gate and the designer runs the layout check
   and fixes the stylesheet until it is clean. The builder receives only the spec sentences
   that failed (`FAILED "<sentence>"`) and the designer's concrete markup requests.
6. The builder fixes each item or disputes it with a quote; the coordinator rules from the
   spec text and records the ruling in `factory/stage-N/decisions.md`.
7. Before closing, the coordinator reads the code diff and sends at most five
   maintainability fixes, and makes sure the last gate and layout check ran on the final
   commit. The stage closes when the gate is green and the layout check prints
   `LAYOUT: clean` (or after four fix rounds / at the budget, on the best commit). The tester copies the holdout into
   `factory/stage-N/evidence/`; the coordinator posts the final report.

## How it catches bad work

- **Independent holdout.** The tester writes scenarios from the spec alone; the builder's
  and coordinator's configs deny reading them (`permissions.deny` on `Read(//…/holdout/**)`,
  enforced even in bypass mode — we tested it with `Read`, `cat` and Python). The builder
  never sees a test, only the sentence it violates, so it cannot fit code to tests.
- **Quotes, checked by a script.** Every scenario names the spec sentences it checks, word
  for word (`Spec: "…"`). `factory/tools/coverage.py` rejects any quote not in the spec and
  lists every spec sentence no scenario covers.
- **The gate** (`factory/tools/gate.py`) builds the Docker image, runs every scenario (files
  marked `# fresh-service` each on a new container), the shipped checks if given, and
  optional mutation patches, then prints one verdict. It refuses a folder that is its own git
  repository.
- **A failure is a signal, not an order.** The tester may not assert what the spec leaves
  open; a builder can dispute a failure and the coordinator rules from a quote. This stopped
  an LLM-written test from dragging correct code down (see "What failed").
- **Layout checked by a browser, not by eye.** `factory/tools/layout_check.py` loads every
  screen in headless Chromium at 375 and 1280 px (signed in where needed) and reports
  sideways overflow, elements off screen, overlapping boxes, clipped text, cards in a row that
  start or end at different heights, controls under 40 px and text under 14 px at phone width,
  and console errors. The designer fixes the stylesheet until it prints `LAYOUT: clean`, then
  compares the screenshots with its mockups. The design guidance it follows is the
  `product-ui-design` skill shipped in `factory/skills/`.

Evidence from our development runs (pocketful, shipped tests hidden from the factory):

- Stage 2: the designer's review found that a screen the spec requires to show the wallet
  balances did not show them; the shipped tests did not catch it. Fixed in one round.
- Stage 3: the first build passed every shipped test, but the tester's gate reported 10 spec
  sentences red. Two of them were exactly the two checks our independent hidden suite later
  failed on that build. After one fix round the build passed 149/149 hidden checks.
- Stage 4: one gate failure was the tester's own mistake (it demanded a byte-identical
  snapshot where the spec allows a new field). The tester showed why, fixed the scenario, and
  the builder was not made to change correct code.

## Design choices and what they cost

| Choice | Why (measured) |
|---|---|
| Frontier model as builder | A single Opus session with the spec, no tests, scored 147/147 and 35/35 on stages 1–2. Every factory built on cheaper builders scored lower (table below). Test quality, not builder count, was the ceiling. |
| Strong, independent tester | Cheap testers wrote status-only checks: the gate said 21/26 green while the service passed 57/147 real tests. An Opus tester's holdout found real misses on stage 3. |
| Contract first, its own turn | A machine-readable contract lifted a Haiku builder from 57/147 to 126–139/147 on its first try. Band publishes a reply only at the end of a turn, so the contract goes out in a short turn before the long scenario turn. |
| Failures as signals, disputes ruled from quotes | An unchecked LLM holdout once ranked two candidates in reverse (chose a 126/147 build over a 139/147 one). |
| Designer seat that owns the look | App is 25% of the score; the shipped UI tests check identifiers, not looks. When the designer only drew mockups and the builder styled from them, the result passed every test and still looked dated, with cards of uneven height side by side. With the designer writing the stylesheet and artwork and a Chromium layout check gating the stage, the same stage came out clean at 375 and 1280 px in one round. |
| Messages via `band send`, checks in the foreground, `gate.py` exits 0 | In Band a seat speaks by settling an inbound message; a background task that exits non-zero ends the turn as an error and the staged reply is lost. Two of our runs stalled this way before we changed it. |
| Rules enforced by config and scripts, not prose | Prose rules were broken by every model we tried: a coordinator forwarded test details, a tester edited an assertion to match code. Access denials and script checks held. |

Measured time and spend, development run of the final design (v4, pocketful, list price):

| Stage | Wall time | Gate | Shipped checks (suites 1..N) | Our hidden suite |
|---|---|---|---|---|
| 1 | 30 min (18 min lost to a Band daemon file-limit fault) | green, round 1 | 147/147 | — |
| 2 | 25 min | green, round 3 (UI fixes) | 147/147, 35/35 | — |
| 3 | 48 min (≈27 min lost to an account usage limit) | green, round 3 | 147, 35, 6/6 | 149/149 |
| 4 | 14 min | green, round 2 | 147, 35, 6, 5/5 | 90/92 |
| Total | ≈ 2 h, ≈ 1 h 15 min without outages | | | |

Spend: $42.76 for four stages (Opus $31.09, Sonnet $11.67). For comparison, one Opus session
per stage: 43 min, $10.43, hidden suites 147/149 and 91/92. The factory costs about four
times as much; on the hidden checks it finished level (239/241 vs 238/241), it found and fixed
a UI defect the single session left in, and its room log shows the work, the review and the
rulings.

Final judged run: see the table at the end of this file.

## What we tried that failed

All on pocketful stage 1 unless noted; the factory never saw the shipped tests.

| Variant | Result | Time | Spend | Lesson |
|---|---|---|---|---|
| One Opus session | 147/147 (s2 35/35) | 7 min | $1.50 | the bar to beat |
| One Haiku session | 137/147 (s2 1/35) | 11 min | $1.07 | cheap model alone is close on easy stages, fails the UI stage |
| 3 Sonnet seats, template mandates | 145/147 | 20 min | ≈ $11 (with part of s2) | review missed a real bug |
| 5 seats, 15 cards, Haiku builder | stopped before finishing s1 | > 25 min | $7.78 | card-by-card handoffs cost more than they catch |
| 5 seats, Haiku builder + 2 Haiku testers | 57 → 132/147 after 3 rounds | ≈ 55 min | $11.45 | cheap testers: weak checks, false confidence |
| Opus tester + 2 competing Haiku builders | 126 and 139/147 first try | ≈ 45 min | — | contract helps; LLM holdout picked the worse candidate |

Design: in the run with a mockup-only designer, our own layout check later found 17 defects
on the shipped UI (uneven card rows, 36 px buttons, 12 px captions at phone width). In the
next run the designer owned the stylesheet and the check, and closed the stage at
`LAYOUT: clean`.

Smaller lessons: on the toy track a builder handed the tester's task wrote the hidden tests
itself because the coordinator mentioned it in passing — every `@handle` delivers the whole
message, so mandates mention only the seat that must act. A seat called a command through a shell
variable and triggered a permission prompt; `timeout` is now on PATH.

## Stand it up

1. Install Band Desktop, Docker, Claude Code and Python 3.12+; create a Claude token
   (`claude setup-token`) and store it in the keychain as `df-claude-oauth`.
2. `factory/setup/install.sh <workspace> <result-repo> --create` writes the seat launchers,
   the two isolated Claude configs (holdout denied for the restricted seats, the
   `svg-ui-mockup` skill for the designer) and creates the four Band agents.
3. Copy `mandates/` and `factory/` into the result repository and commit.
4. Create a room, add the four seats.
5. For each stage, fill `factory/TASK_TEMPLATE.md` (`factory/make_task.py`) and send it to the
   coordinator; send nothing else until its final report.

Before a run: disconnect old room sessions (`band detach --host-session …`) — the Band daemon
has a 256 open-file limit — and start in a fresh usage window, alone.

## Final judged run

Fresh room, fresh repository, track pocketful. The only human input was one task per stage,
sent by a dispatcher script (`factory/dispatch_stages.py`) after the coordinator's final
report for the previous stage. No other message was sent to the room.

| Stage | Dispatched → closed (UTC, 5 Oct) | Wall time | Gate | Shipped checks (suites 1..N) | Our hidden suite |
|---|---|---|---|---|---|
| 1 | 16:55 → 17:28 | 32 min | green, round 2 | 147/147 | — |
| 2 | 17:29 → 18:16 | 47 min | green, round 3; layout clean after 3 design reviews | 147, 35/35 | — |
| 3 | 18:16 → 18:43 | 27 min | green, round 3 | 147, 35, 6/6 (5/6 in isolated mode, see below) | 149/149 |
| 4 | 18:44 → 19:14 | 30 min | green, round 3 | 147, 35, 6, 5/5 | 91/92 |
| Total | | 2 h 18 min | | every folder claims its stage, none overshoots | |

Spend (list price, measured with ccusage over the seats' sessions): **$35.72** — Opus $26.87
(builder, tester, designer), Sonnet $8.86 (coordinator); parts rounded. 39 commits by the
seats (coordinator 22, tester 8, builder 6, designer 3) plus the one setup commit.

Known miss: in stage 4 the tester's holdout caught that statement snapshots did not survive
importing a stage-3 export. The tester itself asked the coordinator whether the requirement
applied to an export that never carried snapshots; the coordinator ruled it did not, and the
scenario was narrowed. The spec does require it ("retaining settlement membership, corrections and
snapshots"), and our hidden suite still fails that one check. The arbitration step worked as
designed and still produced a wrong ruling: the arbiter is a model too.

A second miss showed up only when we ran the shipped checks in the judges' isolated mode after
the run: one stage-3 sample test fails there (5/6; every folder still claims its stage). The
statement sorts entries with the same timestamp by payment id, not by creation order; inside
the isolated network two payments can land in the same millisecond. Our seats ran the shipped
checks in host mode, where requests are slower and the tie never happens. Lesson for the
factory: give the seats the shipped-check command in `--mode isolated`, the way it is judged.
