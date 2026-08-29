# Test stubs

A minimal stand-in for the Hermes `gateway` package, so the capture-gate
tests run on any machine instead of only where Hermes is installed.

It implements exactly the four names `handler.py` imports —
`BasePlatformAdapter`, `MessageEvent`, `SessionSource`, `Platform` — and
nothing else. It deliberately does NOT replace `tests/test_patch_install.py`,
whose whole purpose is asserting against the *real* classes (it exists
because of a `BaseAdapter` -> `BasePlatformAdapter` rename that a stub would
happily have accepted). Run that one on the VPS with the Hermes venv.

`tests/run_all.py` puts this directory on `sys.path` only when the real
`gateway` package is not importable, so on the VPS the real one wins.
