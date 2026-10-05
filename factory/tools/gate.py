"""Build a service, run every check on it, and print one verdict.

    python gate.py --service DIR --holdout DIR --report-dir DIR \
        [--requirements R01,R02] [--matrix matrix.jsonl] [--mutants DIR] \
        [--min-kill 0.8] [--shipped-cmd "..."] [--health-path PATH] [--view builder|full]

What it does, in order:
  1. docker build DIR once; start it with PORT set and wait for the health path (60 s).
  2. run the holdout scenarios (pytest files in --holdout) with TARGET_URL set.
     A scenario names its requirement in the test name: test_R07_something.
     All files share one container, except files containing the line `# fresh-service`:
     each of those gets a brand-new container (for rules about a freshly started service).
  3. run --shipped-cmd if given (the checks the task supplies), exit code counts.
  4. with --mutants: apply each *.patch to a copy of DIR, rebuild, rerun the holdout,
     --workers mutants at a time.
     A mutant is killed when at least one scenario fails on it.
  5. with --matrix: list requirements that have no scenario.

`--view builder` (default) prints only requirement ids that failed, never test text,
so it can be pasted to the seat that writes the code. `--view full` adds failure
messages for the seats that own the scenarios. The full report is always written to
--report-dir/gate-report.json, which must live outside the code repository.

Read the verdict from the `VERDICT:` line. Exit code is 0 unless --strict is given
(then 1 on RED): agent runtimes report a non-zero exit as a crashed task.
"""
import argparse
import ast
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ElementTree
from concurrent.futures import ThreadPoolExecutor

REQUIREMENT_IN_NAME = re.compile(r"R\d+")
IMAGE = "factory-gate-under-test"
FRESH_MARK = "# fresh-service"
QUOTES = {}


def run(command, timeout, **kwargs):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"timed out after {timeout}s")


def tail(text, lines=30):
    return "\n".join(text.strip().splitlines()[-lines:])


def build_image(service_dir, tag):
    build = run(["docker", "build", "-q", "-t", tag, str(service_dir)], timeout=900)
    if build.returncode != 0:
        return "build failed:\n" + tail(build.stdout + build.stderr)
    return ""


def start(tag, port, health_path):
    started = run(["docker", "run", "-d", "--rm", "-p", f"127.0.0.1:{port}:8080",
                   "-e", "PORT=8080", "--cpus", "2", "--memory", "2g", tag], timeout=60)
    if started.returncode != 0:
        return None, "container did not start:\n" + tail(started.stderr)
    container = started.stdout.strip()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{health_path}", timeout=2) as response:
                if response.status == 200:
                    return container, ""
        except OSError:
            pass
        time.sleep(1)
    logs = run(["docker", "logs", container], timeout=30)
    stop(container)
    return None, "service never became healthy in 60 s:\n" + tail(logs.stdout + logs.stderr)


def stop(container):
    if container:
        run(["docker", "stop", "-t", "2", container], timeout=60)


SPEC_LINE = re.compile(r'^\s*Spec:\s*"(.+)"')


def spec_quotes_by_test(holdout_dir):
    """Map test function name -> spec quotes from its docstring (`Spec: "..."` lines)."""
    index = {}
    for path in pathlib.Path(holdout_dir).rglob("test_*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                quotes = []
                for line in (ast.get_docstring(node) or "").splitlines():
                    match = SPEC_LINE.match(line)
                    if match:
                        quotes.append(match.group(1).replace('\\"', '"'))
                index.setdefault(node.name, [])
                for quote in quotes:
                    if quote not in index[node.name]:
                        index[node.name].append(quote)
    return index


def run_holdout(holdout_dir, targets, base_url, python, junit_path):
    environment = {"TARGET_URL": base_url, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                   "HOME": str(pathlib.Path.home()), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    command = [python, "-m", "pytest", *[str(t) for t in targets], "-q", "-p", "no:cacheprovider", "--import-mode=importlib",
               "--junitxml", str(junit_path)]
    result = run(command, timeout=900, env=environment, cwd=str(holdout_dir))
    cases = []
    if not pathlib.Path(junit_path).exists():
        return cases, tail(result.stdout + result.stderr)
    tree = ElementTree.parse(junit_path)
    for case in tree.iter("testcase"):
        name = case.get("name", "")
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        skipped = case.find("skipped") is not None
        found = REQUIREMENT_IN_NAME.findall(name)
        if not found:
            found = ['"' + quote + '"' for quote in QUOTES.get(name.split("[")[0], [])]
        cases.append({
            "name": name,
            "file": case.get("classname", ""),
            "requirements": found,
            "passed": problem is None and not skipped,
            "message": "" if problem is None else (problem.get("message") or "")[:500],
        })
    return cases, ""


def requirement_results(cases):
    results = {}
    for case in cases:
        for requirement in case["requirements"]:
            if requirement not in results:
                results[requirement] = True
            if not case["passed"]:
                results[requirement] = False
    return results


def sort_key(requirement):
    return int(requirement[1:]) if requirement[1:].isdigit() else 0


def matrix_requirements(matrix_path):
    ids = []
    with open(matrix_path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                entry = json.loads(line)
                if entry.get("kind") == "req":
                    ids.append(entry["id"])
    return ids


def patch_requirement(patch_path):
    for line in patch_path.read_text(encoding="utf-8", errors="replace").splitlines()[:20]:
        match = re.match(r"^#?\s*Requirement:\s*(R\d+)", line.strip())
        if match:
            return match.group(1)
    return "?"


def scenario_groups(holdout_dir):
    """Files marked `# fresh-service` each get their own new container; the rest share one."""
    shared, fresh = [], []
    for path in sorted(pathlib.Path(holdout_dir).rglob("test_*.py")):
        if FRESH_MARK in path.read_text(encoding="utf-8", errors="replace"):
            fresh.append(path)
        else:
            shared.append(path)
    return shared, fresh


def run_scenarios(tag, port, args, junit_stem):
    """Run every scenario against image `tag`. Returns (cases, error)."""
    shared, fresh = scenario_groups(args.holdout)
    groups = []
    if shared:
        groups.append(shared)
    for path in fresh:
        groups.append([path])
    cases, errors = [], []
    for number, group in enumerate(groups):
        container, problem = start(tag, port, args.health_path)
        if container is None:
            return cases, problem
        try:
            junit = args.report_dir / f"{junit_stem}-{number}.xml"
            found, error = run_holdout(args.holdout, group, f"http://127.0.0.1:{port}", args.python, junit)
        finally:
            stop(container)
        cases.extend(found)
        if error:
            errors.append(error)
    return cases, "\n".join(errors)


def run_one_mutant(patch_path, index, service_dir, args):
    requirement = patch_requirement(patch_path)
    with tempfile.TemporaryDirectory() as scratch:
        copy = pathlib.Path(scratch) / "service"
        shutil.copytree(service_dir, copy, ignore=shutil.ignore_patterns(".git"))
        with open(patch_path, encoding="utf-8") as patch_input:
            applied = run(["patch", "-p1", "-d", str(copy), "--forward", "--batch"],
                          timeout=60, stdin=patch_input)
        if applied.returncode != 0:
            return {"mutant": patch_path.name, "requirement": requirement,
                    "status": "did-not-apply", "detail": tail(applied.stdout, 5)}
        tag = f"{IMAGE}-mutant-{index}"
        problem = build_image(copy, tag)
    if problem:
        return {"mutant": patch_path.name, "requirement": requirement,
                "status": "killed", "detail": "mutant does not build"}
    port = args.port + 10 + index
    cases, error = run_scenarios(tag, port, args, f"mutant-{patch_path.stem}")
    failed = [case["name"] for case in cases if not case["passed"]]
    status = "killed" if failed or error else "survived"
    return {"mutant": patch_path.name, "requirement": requirement,
            "status": status, "detail": ", ".join(failed[:5])}


def run_mutants(service_dir, mutants_dir, args):
    patches = sorted(pathlib.Path(mutants_dir).glob("*.patch"))
    outcomes = [None] * len(patches)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for index, patch_path in enumerate(patches):
            futures[pool.submit(run_one_mutant, patch_path, index, service_dir, args)] = index
        for future, index in futures.items():
            outcomes[index] = future.result()
    return outcomes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True, type=pathlib.Path)
    parser.add_argument("--holdout", required=True, type=pathlib.Path)
    parser.add_argument("--report-dir", required=True, type=pathlib.Path)
    parser.add_argument("--requirements", default="")
    parser.add_argument("--matrix")
    parser.add_argument("--mutants")
    parser.add_argument("--min-kill", type=float, default=0.8)
    parser.add_argument("--shipped-cmd")
    parser.add_argument("--health-path", default="/health")
    parser.add_argument("--port", type=int, default=18090)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--view", choices=["builder", "full"], default="builder")
    parser.add_argument("--workers", type=int, default=4, help="mutants built and run in parallel")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 on RED; by default the verdict is only printed, because agent "
                             "runtimes treat a non-zero exit as a crashed task")
    args = parser.parse_args()

    args.report_dir.mkdir(parents=True, exist_ok=True)
    QUOTES.update(spec_quotes_by_test(args.holdout))
    scope = [r.strip() for r in args.requirements.split(",") if r.strip()]
    report = {"service": str(args.service), "scope": scope, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    lines = []
    green = True

    if (args.service / ".git").exists():
        problem = "the service folder is a nested repository (it has its own .git); commit it into the result repository instead"
    else:
        problem = build_image(args.service, IMAGE)
    if problem:
        report["build"] = problem
        lines.append("BUILD: FAILED")
        lines.append(problem)
        (args.report_dir / "gate-report.json").write_text(json.dumps(report, indent=2))
        print("\n".join(lines))
        print("VERDICT: RED")
        sys.exit(1 if args.strict else 0)
    lines.append("BUILD: ok")
    cases, error = run_scenarios(IMAGE, args.port, args, "holdout")
    report["holdout_cases"] = cases
    if error:
        green = False
        lines.append("HOLDOUT: could not run (scenario files are broken, tell the scenario owner)")
        if args.view == "full":
            lines.append(error)

    results = requirement_results(cases)
    report["requirements"] = results
    checked = scope if scope else sorted(results, key=sort_key)
    failing = [r for r in checked if results.get(r) is False]
    untested_in_scope = [r for r in scope if r not in results]
    outside_failing = [r for r in sorted(results, key=sort_key) if r not in checked and results[r] is False]
    lines.append(f"HOLDOUT: {len(checked) - len(failing) - len(untested_in_scope)}/{len(checked)} requirements green"
                 + (f" (scope {','.join(scope)})" if scope else ""))
    for requirement in failing:
        lines.append(f"  FAILED {requirement}")
        if args.view == "full":
            for case in cases:
                if requirement in case["requirements"] and not case["passed"]:
                    lines.append(f"      {case['name']}: {case['message'][:300]}")
    for requirement in untested_in_scope:
        lines.append(f"  NO SCENARIO {requirement}")
    if outside_failing:
        lines.append(f"  also failing outside scope: {', '.join(outside_failing)}")
    if failing or untested_in_scope:
        green = False

    if args.shipped_cmd:
        shipped = run(["/bin/sh", "-c", args.shipped_cmd], timeout=1800)
        report["shipped"] = {"exit": shipped.returncode, "tail": tail(shipped.stdout + shipped.stderr, 60)}
        lines.append(f"SHIPPED CHECKS: {'ok' if shipped.returncode == 0 else 'FAILED (exit ' + str(shipped.returncode) + ')'}")
        if shipped.returncode != 0:
            green = False
            for line in (shipped.stdout + shipped.stderr).splitlines():
                if "FAILED" in line or "stage" in line.lower() and ("fail" in line.lower() or "error" in line.lower()):
                    lines.append(f"  {line.strip()[:200]}")

    if args.matrix:
        missing = [r for r in matrix_requirements(args.matrix) if r not in results]
        report["requirements_without_scenarios"] = missing
        if missing:
            lines.append(f"REQUIREMENTS WITHOUT SCENARIOS: {', '.join(missing)}")
            if not scope:
                green = False

    if args.mutants:
        outcomes = run_mutants(args.service, args.mutants, args)
        report["mutants"] = outcomes
        counted = [o for o in outcomes if o["status"] in ("killed", "survived")]
        killed = [o for o in counted if o["status"] == "killed"]
        ratio = len(killed) / len(counted) if counted else 1.0
        lines.append(f"MUTANTS: {len(killed)}/{len(counted)} killed ({ratio:.0%}), need {args.min_kill:.0%}")
        for outcome in outcomes:
            if outcome["status"] == "survived":
                lines.append(f"  SURVIVED {outcome['mutant']} ({outcome['requirement']})")
            elif outcome["status"] == "did-not-apply":
                lines.append(f"  DID NOT APPLY {outcome['mutant']} ({outcome['requirement']})")
        if ratio < args.min_kill:
            green = False

    report["verdict"] = "GREEN" if green else "RED"
    (args.report_dir / "gate-report.json").write_text(json.dumps(report, indent=2))
    print("\n".join(lines))
    print(f"VERDICT: {report['verdict']}")
    sys.exit(0 if green or not args.strict else 1)


if __name__ == "__main__":
    main()
