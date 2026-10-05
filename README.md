# Pocketful — built by a four-seat dark factory

Entry for the WeAreDevelopers × BAND Dark Factory hackathon, track **pocketful** (a wallet
and payments app). Every line under `stage-*/` was written by the factory's seats in one
Band room; the only human input per stage was the task dispatched to the coordinator.

- **Team:** _fill in_
- **Video:** _link_ · **Presentation:** _link_

## Read this repository

| Path | What it is |
|---|---|
| `stage-1/` … `stage-4/` | one complete service per stage (Dockerfile, RUN.md, source); each extends the previous one |
| `FACTORY.md` | how the factory works, why it is built this way, measured cost and time, what failed |
| `mandates/` | the four seats' instructions (generic — no track detail) |
| `factory/tools/` | the checks the seats run: `gate.py`, `coverage.py`, `layout_check.py`, `shoot.py` |
| `factory/skills/` | the design skill the designer seat uses |
| `factory/setup/` | script that creates the seats in Band Desktop |
| `factory/stage-N/` | per stage: contract, design system and mockups, review screenshots, decisions, holdout evidence |
| `room.json` | the Band room log of the submitted run, downloaded unchanged (Download full session) |
| `room-messages-text.json` | every text, error and task message of the same room, read with `band room messages --json`. The download above holds the newest 1,400 messages only, so it starts at 17:03 and misses the stage-1 dispatch and first handoffs; this file covers the whole run from 16:55 |

## Run a stage

Each `stage-N/RUN.md` has the exact command; in short:

    docker build -t pocketful-stage-N stage-N && docker run --rm -p 8080:8080 -e PORT=8080 pocketful-stage-N

Then open http://localhost:8080. To check it with the official harness from the kickoff repo:

    python -m harness run --track pocketful --repo . --stage N

## The factory in one paragraph

A coordinator (Sonnet) hands out work and reviews the code; a builder (Opus) builds each
stage whole; a tester (Opus) writes the interface contract and holdout scenarios from the
spec alone, behind an access barrier the builder cannot cross, and runs the gate; a designer
(Opus) owns the design system, stylesheet and artwork and verifies the layout in Chromium.
The builder only ever sees the spec sentences it fails. Details and the measurements behind
every choice are in `FACTORY.md`.
