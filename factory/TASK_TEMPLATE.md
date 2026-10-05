You are @coordinator. Run stage {N} of the factory now, following your mandate.

Track: {TRACK}
Stage: {N}
Spec file: {WORKSPACE}/dark-factory-wearedevs/{TRACK}/spec/stage-{N}.md
Result repository (<RESULT>): {WORKSPACE}/band-work/{RUN}/result
Stage folder: <RESULT>/stage-{N}/
Holdout root (<HOLDOUT>), outside the repository: {WORKSPACE}/band-work/{RUN}/holdout
Tools (<TOOLS>): <RESULT>/factory/tools
Python with pytest, httpx and playwright (<PY>): {WORKSPACE}/dark-factory-wearedevs/.venv/bin/python
Health path for the gate: /health
`timeout` is on PATH; call commands directly, never through a shell variable.
Shipped checks command:
  cd {WORKSPACE}/dark-factory-wearedevs && {WORKSPACE}/band-work/{RUN}/result/factory/tools/timeout 1500 .venv/bin/python -m harness run --track {TRACK} --repo <RESULT> --stage {N} --out {WORKSPACE}/band-work/{RUN}/checks/s{N}
Seat handles in this room (write them with a leading @ when you message a seat): {HANDLES}
Human handle for the final report: okulov.maksim.v (mention it as @okulov.maksim.v).
Time budget for this stage: {BUDGET} minutes.

The full spec for this stage follows. Paste it in full into every handoff.

----- SPEC START -----
{SPEC}
----- SPEC END -----
