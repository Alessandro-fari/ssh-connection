#!/usr/bin/env python3
"""
Tests for the Init token prompt (gui/token_dialog.py): pre-warmed Tk window
on the SearchPopup thread, asked from a worker thread like the Init flow.

Run:  py tests/test_token_dialog.py
"""

import sys
import tempfile
import threading
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from ssh_connection.config.app_settings import AppSettings
from ssh_connection.ssh.init_orchestrator import INIT_ENVS, InitOrchestrator

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  {'OK' if condition else 'FAIL'}  {name}  {'' if condition else detail}")


def main():
    AppSettings.path = Path(tempfile.mkdtemp(prefix="sshcm-token-")) / "prefs.json"
    from ssh_connection.gui.search_dialog import SearchPopup
    from ssh_connection.gui.token_dialog import TokenDialog
    popup = SearchPopup(host_provider=lambda: [], on_select=lambda h: None)
    popup.start()
    dialog = TokenDialog(popup, INIT_ENVS)
    dialog.prewarm()
    done = threading.Event()

    def on_ui(fn):
        def wrapped():
            try:
                fn()
            finally:
                done.set()
        done.clear()
        popup.run_on_ui(wrapped)
        return done.wait(10)

    def ask_async(env, out, key, timeout=20.0):
        t = threading.Thread(target=lambda: out.__setitem__(key, dialog.ask(env, timeout)))
        t.start()
        return t

    def visible(env):
        res = {}
        on_ui(lambda: res.update(v=dialog._windows[env]["top"].winfo_viewable()))
        return res["v"] == 1

    def wait_visible(env, timeout=3.0):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if visible(env):
                return time.monotonic() - start
            time.sleep(0.01)
        return None

    try:
        print("\n[1] Token prompt")
        time.sleep(0.3)                                   # pre-warm done
        out = {}
        t = ask_async("TEST", out, "t")
        opened_in = wait_visible("TEST")
        check("prompt opens at once (pre-warmed, no process spawn)",
              opened_in is not None and opened_in < 0.5, str(opened_in))
        on_ui(lambda: (dialog._windows["TEST"]["var"].set("  123456 "),
                       dialog._confirm("TEST")))
        t.join(5)
        check("Connetti returns the token (trimmed)", out.get("t") == "123456", str(out))
        check("window hidden and field cleared after use",
              not visible("TEST") and dialog._windows["TEST"]["var"].get() == "")

        t = ask_async("PROD", out, "p")
        wait_visible("PROD")
        res = {}
        on_ui(lambda: (dialog._confirm("PROD"),
                       res.update(hint=dialog._windows["PROD"]["hint"].get())))
        check("empty token refused, prompt stays open",
              visible("PROD") and "token" in res["hint"].lower(), str(res))
        on_ui(lambda: dialog._finish("PROD", None))
        t.join(5)
        check("Annulla returns None", out.get("p") is None)

        t1 = ask_async("TEST", out, "a")
        t2 = ask_async("PROD", out, "b")
        wait_visible("TEST")
        wait_visible("PROD")
        on_ui(lambda: (dialog._windows["PROD"]["var"].set("222"), dialog._confirm("PROD"),
                       dialog._windows["TEST"]["var"].set("111"), dialog._confirm("TEST")))
        t1.join(5)
        t2.join(5)
        check("Init TEST and PROD can wait for their tokens together",
              (out.get("a"), out.get("b")) == ("111", "222"), str(out))

        t = ask_async("TEST", out, "timeout", timeout=0.5)
        t.join(5)
        check("timeout returns None and hides the prompt",
              out.get("timeout") is None and not visible("TEST"))

        InitOrchestrator.token_prompt = dialog.ask
        t = threading.Thread(target=lambda: out.__setitem__("o", InitOrchestrator._ask_token("TEST")))
        t.start()
        wait_visible("TEST")
        on_ui(lambda: (dialog._windows["TEST"]["var"].set("999"), dialog._confirm("TEST")))
        t.join(5)
        check("Init uses the installed prompt (no PowerShell)", out.get("o") == "999", str(out))

        err = {}
        on_ui(lambda: err.update(e=_raises(lambda: dialog.ask("TEST"))))
        check("asking from the Tk thread is refused (would deadlock)", err.get("e") is True)
    finally:
        InitOrchestrator.token_prompt = None
        popup.stop()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        sys.exit(1)


def _raises(fn):
    try:
        fn()
    except RuntimeError:
        return True
    return False


if __name__ == "__main__":
    main()
