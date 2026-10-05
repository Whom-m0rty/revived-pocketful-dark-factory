"""Dispatch stage tasks one at a time, each only after the coordinator's final report.

    python dispatch_stages.py --room <room id> --run final --coordinator-id <uuid> \
        --coordinator-handle okulov.maksim.v/coordinator --stages 1 2 3 4 [--max-hours 4]

Sends task-stage-N.txt as the human, then waits for a text message from the coordinator
that mentions the human and says stage N is closed. Sends nothing else, ever. Writes
band-work/<run>/dispatch-log.jsonl (dispatch and close times per stage).
"""
import argparse
import json
import pathlib
import re
import subprocess
import time
from datetime import datetime, timezone

WORK = pathlib.Path(__file__).resolve().parent.parent / "band-work"
HEADER = re.compile(r"^(\S+Z) \[text\] (.+?) \((Agent|User)\): (.*)$")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def room_messages(room):
    out = subprocess.run(["band", "room", "messages", room, "--type", "text"],
                         capture_output=True, text=True, timeout=120).stdout
    messages, current = [], None
    for line in out.splitlines():
        match = HEADER.match(line)
        if match:
            current = {"at": match.group(1), "sender": match.group(2), "text": match.group(4)}
            messages.append(current)
        elif current:
            current["text"] += "\n" + line
    return messages


def closed(message, stage, human_id, since):
    if message["at"] <= since or not message["sender"].startswith("coordinator"):
        return False
    text = message["text"]
    if human_id not in text:
        return False
    return re.search(rf"[Ss]tage {stage}\b[^\n]{{0,120}}\bclosed", text) is not None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--room", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--coordinator-id", required=True)
    parser.add_argument("--coordinator-handle", required=True)
    parser.add_argument("--human-id", default="154fa7a0-e9aa-4f5c-9f5a-2bec9e5cfdf0")
    parser.add_argument("--stages", nargs="+", type=int, required=True)
    parser.add_argument("--max-hours", type=float, default=4.0)
    args = parser.parse_args()
    run_dir = WORK / args.run
    log = run_dir / "dispatch-log.jsonl"
    for stage in args.stages:
        task = (run_dir / f"task-stage-{stage}.txt").read_text()
        sent_at = now()
        result = subprocess.run(["band", "room", "send", args.room, f"@{args.coordinator_handle} {task}",
                                 "--mention", args.coordinator_id], capture_output=True, text=True, timeout=120)
        print(f"{sent_at} dispatched stage {stage}: {result.stdout.strip() or result.stderr.strip()}", flush=True)
        with log.open("a") as handle:
            handle.write(json.dumps({"stage": stage, "event": "dispatched", "at": sent_at}) + "\n")
        deadline = time.time() + args.max_hours * 3600
        done = None
        while time.time() < deadline and not done:
            time.sleep(60)
            try:
                for message in room_messages(args.room):
                    if closed(message, stage, args.human_id, sent_at):
                        done = message
                        break
            except Exception as error:  # a transient CLI failure must not end the run
                print(f"{now()} poll failed: {error}", flush=True)
        if not done:
            print(f"{now()} stage {stage} not closed within {args.max_hours} h; stopping, nothing more sent", flush=True)
            return
        print(f"{done['at']} stage {stage} closed", flush=True)
        with log.open("a") as handle:
            handle.write(json.dumps({"stage": stage, "event": "closed", "at": done["at"]}) + "\n")
    print(f"{now()} all stages closed", flush=True)


if __name__ == "__main__":
    main()
