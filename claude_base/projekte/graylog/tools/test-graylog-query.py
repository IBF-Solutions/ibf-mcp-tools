#!/usr/bin/env python3
"""Test runner for graylog-query.py.

Loads test cases from testdata/cases.json and runs each one as a subprocess.
Checks exit code, output content, and JSON validity.

Usage:
    python tools/test-graylog-query.py
    python tools/test-graylog-query.py --filter verdicts
    python tools/test-graylog-query.py --fail-fast
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPT  = Path(__file__).parent / "graylog-query.py"
CASES   = Path(__file__).parent / "testdata" / "cases.json"

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
RESET  = "\033[0m"

LOGIN_EXPIRED_MARKER = "IBF-LOGIN ABGELAUFEN"


def run_case(case):
    args = [sys.executable, str(SCRIPT)] + [str(a) for a in case["args"]]
    t0 = time.monotonic()
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = time.monotonic() - t0
    output = result.stdout + result.stderr
    return result.returncode, output, elapsed


def check_case(case, returncode, output):
    exp = case.get("expect", {})
    failures = []

    expected_exit = exp.get("exit_code", 0)
    if returncode != expected_exit:
        failures.append(f"exit_code: got {returncode}, expected {expected_exit}")

    for s in exp.get("output_contains", []):
        if s not in output:
            failures.append(f"output_contains: {s!r} not found")

    for s in exp.get("output_not_contains", []):
        if s in output:
            failures.append(f"output_not_contains: {s!r} found but should be absent")

    if exp.get("valid_json"):
        try:
            data = json.loads(output.strip())
        except json.JSONDecodeError as e:
            failures.append(f"valid_json: parse error — {e}")
            return failures

        jtype = exp.get("json_type")
        if jtype == "array" and not isinstance(data, list):
            failures.append(f"json_type: expected array, got {type(data).__name__}")
        if jtype == "object" and not isinstance(data, dict):
            failures.append(f"json_type: expected object, got {type(data).__name__}")

        for key in exp.get("json_keys", []):
            if isinstance(data, dict) and key not in data:
                failures.append(f"json_keys: key {key!r} missing from JSON object")

        item_keys = exp.get("json_array_item_keys", [])
        if item_keys and isinstance(data, list) and data:
            for key in item_keys:
                if key not in data[0]:
                    failures.append(f"json_array_item_keys: key {key!r} missing from first item")

    return failures


def main():
    p = argparse.ArgumentParser(description="Run graylog-query.py test cases")
    p.add_argument("--filter", help="Only run cases whose name contains this string")
    p.add_argument("--fail-fast", action="store_true", help="Stop on first failure")
    p.add_argument("--verbose", "-v", action="store_true", help="Show full output for each case")
    args = p.parse_args()

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if args.filter:
        cases = [c for c in cases if args.filter.lower() in c["name"].lower()]
        if not cases:
            print(f"{YELLOW}No cases match filter: {args.filter!r}{RESET}")
            sys.exit(1)

    # Pre-flight: Graylog-Erreichbarkeit und Login prüfen
    print("Verbindung prüfen ...", end="", flush=True)
    rc, out, _ = run_case({"args": ["--action", "count", "--last", "60"]})
    if LOGIN_EXPIRED_MARKER in out:
        print(f" {RED}FAIL{RESET}\n")
        print(out)
        sys.exit(1)
    if rc != 0:
        print(f" {RED}FAIL{RESET}\n")
        print(out)
        sys.exit(1)
    print(f" {GREEN}OK{RESET}\n")

    print(f"Running {len(cases)} test case(s) against {SCRIPT.name}\n")

    passed = failed = 0
    for case in cases:
        returncode, output, elapsed = run_case(case)
        failures = check_case(case, returncode, output)
        name = case["name"]
        desc = case.get("description", "")

        if failures:
            failed += 1
            if LOGIN_EXPIRED_MARKER in output:
                print(f"{RED}ABBRUCH{RESET}  Login abgelaufen — verbleibende Tests übersprungen")
                print(output)
                break
            print(f"{RED}FAIL{RESET}  {name:<40}  ({elapsed:.1f}s)")
            print(f"      {desc}")
            for f in failures:
                print(f"      {RED}x{RESET} {f}")
            if args.verbose:
                print(f"      --- output ---\n{output.strip()}\n")
            if args.fail_fast:
                break
        else:
            passed += 1
            print(f"{GREEN}PASS{RESET}  {name:<40}  ({elapsed:.1f}s)")
            if args.verbose:
                print(f"      {desc}")

    print(f"\n{passed} passed, {failed} failed out of {passed + failed} cases")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
