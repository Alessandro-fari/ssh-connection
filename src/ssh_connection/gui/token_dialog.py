"""
2FA token prompt of "Init TEST" / "Init PROD".

It used to be a Windows Forms dialog run as a separate PowerShell process:
starting powershell.exe and loading the WinForms assemblies took seconds at
every Init, and the window ignored the app theme and the DPI scale (blurry
on a 125% screen). Now it is a Toplevel of the SearchPopup Tk root, built
hidden at startup like the settings dialog: opening it is a deiconify, as
instant as the host search, and it follows the light / dark theme.

(The PowerShell version existed because a Tk root + mainloop on a worker
thread of the tray process upset the tray's message loop. The SearchPopup
thread already hosts a stable Tk interpreter, so that concern is gone.)

Threading: `ask(env)` is called by the Init worker thread and blocks until
the user answers (or `timeout`), while the window lives on the Tk thread.
One window per environment, so Init TEST and Init PROD can wait for their
tokens at the same time. Never call `ask` from the Tk thread itself.
"""

import logging
import threading
from typing import Dict, Optional, Sequence

from . import theme
from .search_dialog import _force_foreground
from .theme import px


class TokenDialog:
    def __init__(self, ui_host, envs: Dict[str, dict]):
        """`envs`: {env: {"login": host, "targets": (host, ...)}} (INIT_ENVS)."""
        self._ui = ui_host
        self._envs = envs
        self._windows: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Any thread

    def prewarm(self) -> None:
        self._ui.run_on_ui(lambda: [self._build(env) for env in self._envs])

    def ask(self, env: str, timeout: float = 300.0) -> Optional[str]:
        """Show the prompt of `env` and wait for the token (None if
        cancelled, closed, empty or timed out)."""
        if threading.current_thread().name == "SearchPopup":
            raise RuntimeError("TokenDialog.ask() would block the Tk thread")
        done = threading.Event()
        box = {"token": None}
        self._ui.run_on_ui(lambda: self._show(env, box, done))
        if not done.wait(timeout):
            logging.info(f"Token prompt for {env} timed out")
            self._ui.run_on_ui(lambda: self._finish(env, None))
            done.wait(2.0)
        token = (box["token"] or "").strip()
        return token or None

    # ------------------------------------------------------------------
    # Tk thread

    def _build(self, env: str) -> dict:
        if env in self._windows:
            return self._windows[env]
        import tkinter as tk

        from . import widgets as w
        cfg = self._envs[env]
        top = tk.Toplevel(self._ui.root)
        top.title(f"Init {env}")
        theme.toplevel(top)
        top.resizable(False, False)
        top.withdraw()
        top.protocol("WM_DELETE_WINDOW", lambda: self._finish(env, None))

        body = theme.style(tk.Frame(top, padx=px(26), pady=px(22)), "window")
        body.pack(fill="both", expand=True)
        head = theme.style(tk.Frame(body), "window")
        head.pack(fill="x")
        w.page_title(head, f"Init {env}").pack(side="left")
        theme.style(tk.Label(head, text=env, font=theme.font(8, "semibold"), padx=px(8),
                             pady=px(1)), "env_prod" if env == "PROD" else "env_test").pack(
                                 side="left", padx=px(10))
        targets: Sequence[str] = cfg.get("targets", ())
        w.label(body, f"Apre {cfg['login']}" + (f" e le connessioni a {', '.join(targets)}"
                                                if targets else "") + ".",
                "hint", size=9, anchor="w", justify="left",
                wraplength=px(380)).pack(fill="x", pady=(px(4), px(16)))

        w.label(body, "Token 2FA", anchor="w").pack(fill="x", pady=(0, px(4)))
        var = tk.StringVar()
        row = theme.style(tk.Frame(body), "window")
        row.pack(fill="x")
        entry = theme.style(tk.Entry(row, textvariable=var, font=theme.font(16), show="•",
                                     justify="center", width=16), "entry")
        entry.pack(side="left", fill="x", expand=True, ipady=px(6))
        eye = w.IconButton(row, "eye", lambda: entry.configure(
            show="" if entry.cget("show") else "•"), tooltip="Mostra / nascondi")
        eye.pack(side="left", padx=(px(6), 0))
        hint = tk.StringVar(value="Genera un token nuovo: ogni token vale per un solo login.")
        hint_label = w.label(body, "", "hint", size=9, anchor="w", textvariable=hint)
        hint_label.pack(fill="x", pady=(px(6), 0))

        btns = theme.style(tk.Frame(body), "window")
        btns.pack(fill="x", pady=(px(18), 0))
        w.Button(btns, "Connetti", lambda: self._confirm(env), "primary", width=10).pack(
            side="right")
        w.Button(btns, "Annulla", lambda: self._finish(env, None), width=10).pack(
            side="right", padx=(0, px(8)))
        for wd in (top, entry):
            wd.bind("<Return>", lambda e: self._confirm(env) or "break")
            wd.bind("<Escape>", lambda e: self._finish(env, None) or "break")
        top.update_idletasks()
        win = {"top": top, "entry": entry, "var": var, "hint": hint, "hint_label": hint_label,
               "box": None, "done": None}
        self._windows[env] = win
        return win

    def _show(self, env: str, box: dict, done: threading.Event) -> None:
        win = self._build(env)
        if win["done"] is not None:          # a previous prompt still open: close it
            self._finish(env, None)
        win["box"], win["done"] = box, done
        win["var"].set("")
        win["entry"].configure(show="•")
        win["hint"].set("Genera un token nuovo: ogni token vale per un solo login.")
        theme.set_role(win["hint_label"], "hint")
        top = win["top"]
        top.update_idletasks()
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        x = (top.winfo_screenwidth() - w) // 2
        y = max(60, (top.winfo_screenheight() - h) // 3)
        top.geometry(f"+{x}+{y}")
        top.deiconify()
        top.lift()
        top.attributes("-topmost", True)
        top.update_idletasks()
        try:
            _force_foreground(int(top.wm_frame(), 16))
        except Exception:
            pass
        win["entry"].focus_force()

    def _confirm(self, env: str) -> None:
        win = self._windows.get(env)
        if win is None:
            return
        token = win["var"].get().strip()
        if not token:
            win["hint"].set("Inserisci il token.")
            theme.set_role(win["hint_label"], "error")
            win["entry"].focus_set()
            return
        self._finish(env, token)

    def _finish(self, env: str, token: Optional[str]) -> None:
        win = self._windows.get(env)
        if win is None:
            return
        win["top"].withdraw()
        win["var"].set("")                   # never keep the token in the widget
        box, done = win["box"], win["done"]
        win["box"] = win["done"] = None
        if box is not None:
            box["token"] = token
        if done is not None:
            done.set()
