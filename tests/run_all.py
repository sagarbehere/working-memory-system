#!/usr/bin/env python3
"""Run every test suite that works without a live Hermes gateway.

    python3 tests/run_all.py

The capture-gate tests need four names from Hermes' ``gateway`` package. If
the real package is importable (on the VPS, under the Hermes venv) it is
used; otherwise ``tests/stubs`` supplies a minimal stand-in so the suite
also runs on a dev machine. test_patch_install.py is deliberately NOT run
from here — it exists to assert against the REAL gateway classes, which a
stub would happily satisfy. Run that one on the VPS; verify-on-vps.sh does.

Every suite writes to its own temporary directory. Nothing here touches a
real WM_ROOT, and nothing makes a network call.
"""
import os
import pathlib
import subprocess
import sys

PKG = pathlib.Path(__file__).resolve().parents[1]
TESTS = PKG / "tests"

SUITES = [
    ("wmlib            ", "test_wmlib.py"),
    ("records store    ", "test_records.py"),
    ("reminder store   ", "test_reminders.py"),
    ("reminder tick    ", "test_reminder_check.py"),
    ("capture gate     ", "test_gate.py"),
    ("gate health      ", "test_gate_health.py"),
    ("reminder resolver", "test_reminder_resolver.py"),
    ("backup push      ", "test_backup.py"),
    ("debounce hook    ", "test_debounce.py"),
]


def _gateway_available() -> bool:
    probe = subprocess.run(
        [sys.executable, "-c", "import gateway.platforms.base"],
        capture_output=True)
    return probe.returncode == 0


def main() -> int:
    env = dict(os.environ)
    # Isolate from the user's real config: these suites must never read or
    # write a live WM_ROOT.
    env.pop("WM_ROOT", None)
    env.pop("WM_VAULT_PATH", None)
    env.pop("WM_TZ", None)

    real = _gateway_available()
    if real:
        print("gateway: using the real Hermes package")
    else:
        print("gateway: not importable — using tests/stubs")
        env["PYTHONPATH"] = os.pathsep.join(
            [str(TESTS / "stubs"), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)

    failures = []
    for label, script in SUITES:
        path = TESTS / script
        if not path.exists():
            print(f"  {label}  SKIP (missing {script})")
            continue
        suite_env = dict(env)
        if script == "test_debounce.py":
            # This suite builds its own HERMES_HOME fixture and reads
            # $HERMES_HOME/hermes-agent for the real package.
            import tempfile
            suite_env["HERMES_HOME"] = tempfile.mkdtemp(prefix="wm-run-all-")
        else:
            suite_env.setdefault("HERMES_HOME", "/nonexistent-hermes-home")
        r = subprocess.run([sys.executable, str(path)], cwd=str(PKG),
                           capture_output=True, text=True, env=suite_env)
        tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
        if r.returncode == 0:
            print(f"  {label}  PASS  {tail}")
        else:
            print(f"  {label}  FAIL")
            failures.append((script, r.stdout, r.stderr))

    if failures:
        print(f"\n{len(failures)} suite(s) FAILED:\n")
        for script, out, err in failures:
            print(f"--- {script} ---")
            print(out.strip()[-3000:])
            print(err.strip()[-3000:])
        return 1
    print("\nALL SUITES PASSED")
    if not real:
        print("NOTE: run tests/test_patch_install.py on the VPS with the Hermes "
              "venv — it is the only check a stub cannot stand in for.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
