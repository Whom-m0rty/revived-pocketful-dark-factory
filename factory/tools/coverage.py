"""Check that a requirement matrix covers a specification, sentence by sentence.

    python coverage.py --spec SPEC.md --matrix matrix.jsonl [--plan plan.jsonl]

The matrix is JSON Lines, one object per line:

    {"id": "R01", "kind": "req", "quote": "<exact words from the spec>"}
    {"id": "N01", "kind": "non-normative", "quote": "<exact words>", "reason": "..."}

The plan is JSON Lines, one card per line:

    {"card": "C01", "requirements": ["R01", "R02"], "status": "todo", "rounds": 0}

The script does not judge meaning. It checks three things a model can get wrong
without noticing:
  1. every quote is really in the spec (no invented requirements);
  2. every unit of the spec (sentence, list item, table row, code block) is quoted by
     at least one matrix entry (nothing dropped silently);
  3. every requirement is assigned to exactly one card (nothing unplanned);
  4. no id that is already committed changed its quote or disappeared (ids are frozen
     once handed off, so scenarios named after them stay correct).

Exit code 0 only when all three hold. Output is plain text, one problem per line.
"""
import argparse
import json
import os
import re
import subprocess
import sys

MIN_PARTIAL_QUOTE = 15
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*\"(\[])")


def normalize(text):
    text = text.replace("**", "").replace("`", "").replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def is_table_separator(line):
    stripped = line.strip().strip("|").replace(" ", "")
    return bool(stripped) and set(stripped) <= set("-:|")


def spec_units(markdown):
    """Split the spec into checkable units. Headings are titles, not requirements."""
    units = []
    paragraph = []
    in_code = False
    code_lines = []
    table_header_seen = False

    def flush_paragraph():
        if paragraph:
            text = " ".join(paragraph)
            for sentence in SENTENCE_BREAK.split(text):
                if normalize(sentence):
                    units.append(sentence)
            paragraph.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                units.append("\n".join(code_lines))
                code_lines = []
                in_code = False
            else:
                flush_paragraph()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith(">"):
            stripped = stripped.lstrip("> ").strip()
        if not stripped:
            flush_paragraph()
            table_header_seen = False
            continue
        if stripped.startswith("#"):
            flush_paragraph()
            continue
        if stripped.startswith("|"):
            flush_paragraph()
            if is_table_separator(stripped):
                continue
            if not table_header_seen:
                table_header_seen = True
                continue
            units.append(stripped)
            continue
        if re.match(r"^([-*+]|\d+\.)\s+", stripped):
            flush_paragraph()
            item = re.sub(r"^([-*+]|\d+\.)\s+", "", stripped)
            for sentence in SENTENCE_BREAK.split(item):
                if normalize(sentence):
                    units.append(sentence)
            continue
        paragraph.append(stripped)
    flush_paragraph()
    return units


def read_jsonl(path, problems):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as error:
                problems.append(f"{path}:{number}: not valid JSON ({error})")
    return rows


QUOTE_LINE = re.compile(r'^\s*(Spec|Non-normative):\s*"(.+)"\s*(?:#\s*(.*))?$')


def scenario_quotes(folder):
    """Matrix-like entries from every `Spec: "..."` / `Non-normative: "..."` line under folder."""
    entries = []
    number = 0
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            if not (name.endswith(".py") or name.endswith(".txt")):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    match = QUOTE_LINE.match(line)
                    if not match:
                        continue
                    number += 1
                    kind = "req" if match.group(1) == "Spec" else "non-normative"
                    quote = match.group(2).replace('\\"', '"')
                    entry = {"id": f"Q{number:04d}", "kind": kind, "quote": quote,
                             "source": os.path.relpath(path, folder)}
                    if kind == "non-normative":
                        entry["reason"] = match.group(3) or "marked non-normative"
                    entries.append(entry)
    return entries


def committed_matrix(matrix_path):
    """The matrix as last committed in git, or {} when it was never committed."""
    folder = os.path.dirname(os.path.abspath(matrix_path))
    top = subprocess.run(["git", "-C", folder, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if top.returncode != 0:
        return {}
    relative = os.path.relpath(os.path.abspath(matrix_path), top.stdout.strip())
    shown = subprocess.run(["git", "-C", top.stdout.strip(), "show", f"HEAD:{relative}"],
                           capture_output=True, text=True)
    if shown.returncode != 0:
        return {}
    entries = {}
    for line in shown.stdout.splitlines():
        if line.strip():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("id"):
                entries[entry["id"]] = normalize(str(entry.get("quote", "")))
    return entries


def unit_is_covered(unit, quotes):
    unit_text = normalize(unit)
    for quote in quotes:
        if unit_text in quote:
            return True
        if len(quote) >= min(len(unit_text), MIN_PARTIAL_QUOTE) and quote in unit_text:
            return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--matrix", help="requirement matrix (JSON Lines)")
    parser.add_argument("--scenarios", help="scenario folder: quotes come from `Spec: \"...\"` and `Non-normative: \"...\"` lines")
    parser.add_argument("--plan")
    args = parser.parse_args()

    problems = []
    with open(args.spec, encoding="utf-8") as handle:
        spec_text = handle.read()
    whole_spec = normalize(spec_text.replace("|", " "))
    whole_spec_with_bars = normalize(spec_text)
    if args.scenarios:
        matrix = scenario_quotes(args.scenarios)
    elif args.matrix:
        matrix = read_jsonl(args.matrix, problems)
    else:
        parser.error("give --matrix or --scenarios")

    seen_ids = set()
    quotes = []
    requirement_ids = []
    for entry in matrix:
        entry_id = entry.get("id")
        kind = entry.get("kind")
        quote = normalize(str(entry.get("quote", "")))
        if not entry_id:
            problems.append(f"matrix entry without id: {entry}")
            continue
        if entry_id in seen_ids:
            problems.append(f"{entry_id}: duplicate id")
        seen_ids.add(entry_id)
        if kind not in ("req", "non-normative"):
            problems.append(f"{entry_id}: kind must be 'req' or 'non-normative', got {kind!r}")
        if kind == "non-normative" and not entry.get("reason"):
            problems.append(f"{entry_id}: non-normative entry needs a reason")
        if not quote:
            problems.append(f"{entry_id}: empty quote")
            continue
        if quote not in whole_spec and quote not in whole_spec_with_bars:
            problems.append(f"{entry_id}: quote is not in the spec, copy it word for word: {entry.get('quote')!r}")
            continue
        quotes.append(quote)
        if kind == "req":
            requirement_ids.append(entry_id)

    current = {}
    for entry in matrix:
        if entry.get("id"):
            current[entry["id"]] = normalize(str(entry.get("quote", "")))
    frozen = committed_matrix(args.matrix) if args.matrix else {}
    for frozen_id, frozen_quote in frozen.items():
        if frozen_id not in current:
            problems.append(f"{frozen_id}: committed id was removed; ids are frozen, add new ids instead")
        elif current[frozen_id] != frozen_quote:
            problems.append(f"{frozen_id}: committed id changed its quote; ids are frozen, add a new id instead")

    units = spec_units(spec_text)
    uncovered = []
    for unit in units:
        if not unit_is_covered(unit, quotes):
            uncovered.append(unit)
    for unit in uncovered:
        problems.append(f"uncovered spec unit: {unit.strip()[:200]!r}")

    if args.plan:
        plan = read_jsonl(args.plan, problems)
        assigned = {}
        for card in plan:
            for requirement in card.get("requirements", []):
                if requirement in assigned:
                    problems.append(f"{requirement}: in both {assigned[requirement]} and {card.get('card')}")
                assigned[requirement] = card.get("card")
                if requirement not in requirement_ids:
                    problems.append(f"{card.get('card')}: {requirement} is not a requirement in the matrix")
        for requirement in requirement_ids:
            if requirement not in assigned:
                problems.append(f"{requirement}: not assigned to any card")

    covered = len(units) - len(uncovered)
    percent = 100.0 * covered / len(units) if units else 100.0
    print(f"spec units: {len(units)}, covered: {covered} ({percent:.1f}%), "
          f"requirements: {len(requirement_ids)}, problems: {len(problems)}")
    for problem in problems:
        print(f"  - {problem}")
    print("COVERAGE OK" if not problems else "COVERAGE FAILED")
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()
