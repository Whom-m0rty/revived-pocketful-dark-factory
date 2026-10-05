# Stage 4 — coordinator rulings

## Snapshot retention on a stage-3 → stage-4 export/import

**Question** (from @tester, gate round 1 on 0f12b398ea135e67ba45c5168b13e47948982667):
`test_stage3_export_into_stage4` creates a statement snapshot on the real shipped stage-3
service (1b705b5), exports it, imports that export into stage 4, and expects the same
snapshot token to still page with 200. It gets 404 instead. The tester confirmed the
stage-3 export's `state` object has no snapshot data at all — no `snapshots` key, and the
token string does not appear anywhere in the export. Settlement membership, corrections
and replays do carry over correctly on the same import. Should the scenario (a) stand as
written, requiring stage 4 to recover snapshot data that stage 3's export never captured,
or (b) be narrowed to check snapshot retention only on a stage-4 own export/import round
trip, while the stage-3 → stage-4 path keeps checking settlement membership, corrections
and replays?

**Quote** (stage-3.md, "Stable statement pagination"): "Tokens last until reset. No
storage survival across container restarts is required." No stage 1, 2 or 3 export/import
requirement (stage-1.md §10) lists snapshot tokens among the things an import must
preserve — that list is accounts/login, bearer tokens, currency, balances, payments,
requests, permissions, idempotent request bodies and responses. Snapshots are introduced
only in stage-3.md and are explicitly scoped to live only until reset, with no promise of
surviving even a restart, let alone requiring an earlier, already-gated stage's export
format to carry them. Stage-4.md's "retaining settlement membership, corrections and
snapshots" describes what stage 4 must preserve *from the data an export actually
contains* — it cannot obligate stage 3's export (already built and gated under a spec that
never asked for this) to contain data it was never required to serialize.

**Ruling:** (b). Narrow the scenario: snapshot retention is verified on a stage-4 own
export → reset → import round trip (already passing per the tester's separate check).
The stage-3 → stage-4 upgrade scenario continues to check settlement membership,
corrections, revisions and replay bodies, which do survive. This is not a stage-4 code
defect — stage 4's own round trip already satisfies the requirement. Tester: update
`test_stage3_export_into_stage4` to drop the snapshot-survives-the-upgrade assertion, and
add that assertion to stage-4's own export/import round-trip test if it is not already
covered there.
