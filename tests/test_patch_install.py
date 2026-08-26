"""Smoke test: the hook's monkey-patches actually install against the REAL
gateway classes (catches wrong-class-name import bugs like the
BaseAdapter -> BasePlatformAdapter one).

Run with the HERMES venv python, with the hermes-agent repo importable:

    $HERMES_HOME/hermes-agent/venv/bin/python tests/test_patch_install.py

(No WM_SKIP_PATCH here — the patch must run for this test to mean
anything. Read-only: import + assert, no state writes.)

The core assertion is the BasePlatformAdapter.handle_message patch — the
shared inbound seam that exists in every Hermes layout. The Telegram
/done interception is version-dependent (the module moved from
gateway/platforms/telegram.py to plugins/platforms/telegram/adapter.py in
Hermes v0.20.x), so it is verified when the module can be found, and
reported as skipped otherwise. Both imports are guarded by a 10s alarm so
a heavy plugin import can never hang the test.
"""

import importlib
import importlib.util
import os
import pathlib
import signal
import sys

pkg = pathlib.Path(__file__).resolve().parents[1]
os.environ.pop("WM_SKIP_PATCH", None)  # ensure install_patches() runs

# Make the hermes-agent repo importable (same env the gateway runs in).
repo = pathlib.Path(os.environ.get("HERMES_HOME") or pathlib.Path.home() / ".hermes") / "hermes-agent"
sys.path.insert(0, str(repo))
sys.path.insert(0, str(pkg / "hooks" / "working-memory-debounce"))

spec = importlib.util.spec_from_file_location(
    "wm_hook", pkg / "hooks" / "working-memory-debounce" / "handler.py"
)
handler = importlib.util.module_from_spec(spec)


def _find_telegram():
    """Import the TelegramAdapter module for this Hermes layout, or None.

    Tries the plugin layout (v0.20.x) then the legacy module, and leaves
    the module in sys.modules — exactly like a real gateway, which loads
    its platforms BEFORE hooks run. A 10s alarm guards the import: plugin
    modules can pull in heavy stack, and a test must fail fast, not hang.
    """
    candidates = [
        ("plugins.platforms.telegram.adapter", "TelegramAdapter"),
        ("gateway.platforms.telegram", "TelegramAdapter"),
    ]

    def _alarm(*_args):
        raise TimeoutError("telegram import took too long")

    old_handler = signal.signal(signal.SIGALRM, _alarm)
    try:
        for mod_name, attr in candidates:
            signal.alarm(10)
            try:
                mod = importlib.import_module(mod_name)
                return getattr(mod, attr)
            except (ImportError, AttributeError):
                continue
            except TimeoutError:
                print(f"SKIP: telegram import ({mod_name}) timed out; /done interception not verified")
                return None
            finally:
                signal.alarm(0)
    finally:
        signal.signal(signal.SIGALRM, old_handler)
    return None


# Preload the telegram module BEFORE the hook imports (a real gateway has
# its platforms loaded before hooks run), so the hook can find it in
# sys.modules without ever force-importing it.
tg_cls = _find_telegram()

spec.loader.exec_module(handler)  # module-level install_patches() runs

from gateway.platforms.base import BasePlatformAdapter  # noqa: E402

assert BasePlatformAdapter.handle_message.__name__ == "wm_handle_message", (
    "BasePlatformAdapter.handle_message not patched"
)
print("PATCH INSTALL OK: BasePlatformAdapter.handle_message ->", BasePlatformAdapter.handle_message.__name__)

if tg_cls is None:
    print("SKIP: TelegramAdapter not importable in this Hermes layout; /done interception not verified")
else:
    assert tg_cls._handle_command.__name__ == "wm_command", (
        "TelegramAdapter._handle_command not patched"
    )
    print("PATCH INSTALL OK: TelegramAdapter._handle_command ->", tg_cls._handle_command.__name__)

print("markers:", handler.MARKERS, "| lanes:", len(handler.LANES), "| debounce:", handler.WM_DEBOUNCE)
