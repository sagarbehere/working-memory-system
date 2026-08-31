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

This covers the DETERMINISTIC layer only. Whether the agent then classifies
and files a capture sensibly is an LLM judgment — see tests/scenarios.md,
a hand-run checklist.
"""
import os
import pathlib
import subprocess
import sys

PKG = pathlib.Path(__file__).resolve().parents[1]
TESTS = PKG / "tests"

# Labels are padded at print time, not written padded here — a hand-aligned
# list silently misaligns the moment someone adds a longer name.
SUITES = [
    ("wmlib", "test_wmlib.py"),
    ("todoist budget", "test_todoist_budget.py"),
    ("capture gate", "test_gate.py"),
    ("gate cases", "test_gate_cases.py"),
    ("watchdog", "test_watchdog.py"),
    ("debounce hook", "test_debounce.py"),
    ("no vestigial refs", "test_no_vestigial_refs.py"),
    ("vault schema sync", "test_vault_schema_sync.py"),
    ("documented CLI", "test_documented_cli.py"),
]
_W = max(len(label) for label, _ in SUITES)


def _hermes_repo() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get("HERMES_HOME") or (pathlib.Path.home() / ".hermes")
    ) / "hermes-agent"


def _gateway_available(env) -> bool:
    """Can the REAL gateway package be imported by this interpreter?

    The package lives in $HERMES_HOME/hermes-agent, so it must be on the
    path before the probe means anything — the probe used to run with a bare
    sys.path and therefore always said no, silently downgrading to stubs even
    on the VPS. It also needs this interpreter to have the gateway's
    dependencies, which the system python usually does not: run this script
    with $HERMES_HOME/hermes-agent/venv/bin/python for full fidelity.
    """
    probe = subprocess.run(
        [sys.executable, "-c", "import gateway.platforms.base"],
        capture_output=True, env=env, text=True)
    if probe.returncode != 0:
        reason = (probe.stderr.strip().splitlines() or ["unknown"])[-1]
        _gateway_available.reason = reason
    return probe.returncode == 0


def main() -> int:
    env = dict(os.environ)
    # Isolate from the user's real config: these suites must never read or
    # write a live WM_ROOT.
    env.pop("WM_ROOT", None)
    env.pop("WM_VAULT_PATH", None)
    env.pop("WM_TZ", None)

    # Put the Hermes repo on the path FIRST, so the probe can find the real
    # package when it exists rather than defaulting to the stub.
    repo = _hermes_repo()
    if repo.is_dir():
        env["PYTHONPATH"] = os.pathsep.join(
            [str(repo), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)

    real = _gateway_available(env)
    if real:
        print(f"gateway: using the REAL Hermes package ({repo})")
    else:
        why = getattr(_gateway_available, "reason", "not found")
        print(f"gateway: real package not importable — using tests/stubs\n"
              f"         ({why})")
        if repo.is_dir():
            print(f"         hint: {repo} exists; re-run with "
                  f"{repo / 'venv' / 'bin' / 'python'} to test against the "
                  f"real classes")
        env["PYTHONPATH"] = os.pathsep.join(
            [str(TESTS / "stubs"), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)

    failures = []
    for label, script in SUITES:
        path = TESTS / script
        if not path.exists():
            print(f"  {label:<{_W}}  SKIP (missing {script})")
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
            print(f"  {label:<{_W}}  PASS  {tail}")
        else:
            print(f"  {label:<{_W}}  FAIL")
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
