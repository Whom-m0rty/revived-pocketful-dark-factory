"""Fill TASK_TEMPLATE.md for one stage and print it, ready to paste into the room.

    python make_task.py --run toy-01 --track toy --stage 1 --budget 45
"""
import argparse
import pathlib

KIT = pathlib.Path(__file__).resolve().parent
WORKSPACE = KIT.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--track", required=True)
    parser.add_argument("--stage", required=True, type=int)
    parser.add_argument("--budget", type=int, default=60)
    parser.add_argument("--no-shipped", action="store_true", help="hide the shipped checks (A/B runs)")
    parser.add_argument("--template", default="TASK_TEMPLATE.md")
    parser.add_argument("--seats", default="coordinator,analyst,tester,builder,mutator")
    args = parser.parse_args()
    spec_path = WORKSPACE / "dark-factory-wearedevs" / args.track / "spec" / f"stage-{args.stage}.md"
    text = (KIT / args.template).read_text()
    values = {"{N}": str(args.stage), "{TRACK}": args.track, "{WORKSPACE}": str(WORKSPACE),
              "{RUN}": args.run, "{BUDGET}": str(args.budget), "{HANDLES}": ", ".join(f"{seat} = okulov.maksim.v/{seat}" for seat in args.seats.split(",")) + ".", "{SPEC}": spec_path.read_text().strip()}
    for key, value in values.items():
        text = text.replace(key, value)
    if args.no_shipped:
        lines = text.splitlines()
        kept = []
        skip_next = False
        for line in lines:
            if line.startswith("Shipped checks command:"):
                kept.append("Shipped checks: none in this run. Do not look for any test suite; rely on your own checks.")
                skip_next = True
                continue
            if skip_next:
                skip_next = False
                continue
            kept.append(line)
        text = "\n".join(kept) + "\n"
    out = WORKSPACE / "band-work" / args.run / f"task-stage-{args.stage}.txt"
    out.write_text(text)
    print(text)
    print(f"\n(saved to {out})")


if __name__ == "__main__":
    main()
