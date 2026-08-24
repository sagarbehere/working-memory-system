"""Smoke test: the hook's monkey-patches actually install against the REAL
gateway classes (catches wrong-class-name import bugs like the
BaseAdapter -> BasePlatformAdapter one).

Run with the HERMES venv python, with the hermes-agent repo importable:

    ~/.hermes/hermes-agent/venv/bin/python tests/test_patch_install.py

(No WM_SKIP_PATCH here — the patch must run for this test to mean
anything. Read-only: import + assert, no state writes.)
"""
import importlib.util
import os
import pathlib
import sys

pkg = pathlib.Path(__file__).resolve().parents[1]
os.environ.pop("WM_SKIP_PATCH", None)  # ensure install_patches() runs

# Make the hermes-agent repo importable (same env the gateway runs in).
repo = pathlib.Path.home() / ".hermes" / "hermes-agent"
sys.path.insert(0, str(repo))
sys.path.insert(0, str(pkg / "hooks" / "working-memory-debounce"))

spec = importlib.util.spec_from_file_location(
    "wm_hook", pkg / "hooks" / "working-memory-debounce" / "handler.py"
)
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)  # module-level install_patches() runs

from gateway.platforms.base import BasePlatformAdapter  # noqa: E402
from gateway.platforms import telegram as tg  # noqa: E402

assert BasePlatformAdapter.handle_message.__name__ == "wm_handle_message", (
    "BasePlatformAdapter.handle_message not patched"
)
assert tg.TelegramAdapter._handle_command.__name__ == "wm_command", (
    "TelegramAdapter._handle_command not patched"
)
print("PATCH INSTALL OK: BasePlatformAdapter.handle_message ->", BasePlatformAdapter.handle_message.__name__)
print("PATCH INSTALL OK: TelegramAdapter._handle_command ->", tg.TelegramAdapter._handle_command.__name__)
print("markers:", handler.MARKERS, "| lanes:", len(handler.LANES), "| debounce:", handler.WM_DEBOUNCE, "/", handler.WM_MARKER_DEBOUNCE)
