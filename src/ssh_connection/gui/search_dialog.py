"""
Host search popup (tkinter, pre-warmed on a dedicated thread).

Why not the old design: the first version spawned a PowerShell STA process
that ran `Add-Type -AssemblyName System.Windows.Forms`. Starting powershell.exe
plus loading the WinForms/Drawing assemblies costs ~1.5-3 s on every press of
the hotkey — unusable for a "type to jump to a host" popup — and the dialog's
owner-drawn ListBox broke as soon as PowerShell unwrapped the hashtable rows
differently than expected.

New design: one hidden Tk root is built ONCE at application start on its own
thread, running its own `mainloop`. Opening the popup is just `deiconify()` +
`SetForegroundWindow()`, i.e. instantaneous (no process spawn, no assembly
load, no widget construction). Closing only hides it again, so every
subsequent open is just as fast.

Threading contract:
- Everything that touches Tk happens on the popup thread. Other threads
  (tray menu callbacks, the Win32 hotkey thread) never call Tk directly:
  they push a request on a `queue.Queue`, which the Tk thread drains from a
  50 ms `after()` poll. Tcl is not thread-safe, and this keeps every Tcl call
  on the thread that created the interpreter.
- The chosen host is delivered through the `on_select` callback, invoked on
  the popup thread; the caller offloads the (blocking) SSH launch itself.
- `run_on_ui(fn)` runs any callable on this same Tk thread. The settings
  dialog uses it to live as a Toplevel of this root: ONE Tcl interpreter,
  ONE thread, instead of a second Tk() in another thread.

Favorites / recents (AppSettings): with an empty filter the list starts with
a "Preferiti" and a "Recenti" section; while filtering, favorites sort first
inside each environment. Ctrl+D toggles the selected host as favorite.
Ctrl+F opens the "Cerca file" window on the selected host (`on_file_search`).

Foreground: a process that is not in the foreground cannot normally raise a
window. Windows grants foreground rights to the process that received a
WM_HOTKEY, so the hotkey path works directly; for the tray-menu path we also
use the AttachThreadInput trick before SetForegroundWindow.
"""

import ctypes
import logging
import queue
import threading
from typing import Callable, List, Optional, Sequence, Tuple

from ..config.app_settings import AppSettings
from . import theme
from .theme import px

_ENVS = ("Tutti", "TEST", "PROD")

_STAR = "★"


def load_prefs_env() -> str:
    """Return the saved environment filter ('Tutti'/'TEST'/'PROD'), default 'Tutti'."""
    return AppSettings.get("search_env")


def save_prefs_env(env: str) -> None:
    # Merged into the shared prefs file: the old version rewrote the whole
    # file with only this key.
    AppSettings.update(search_env=env)


def _force_foreground(hwnd: int) -> None:
    """Raise `hwnd` above everything and give it the keyboard focus.

    SetForegroundWindow alone is refused when our process does not own the
    foreground; attaching our input queue to the current foreground thread
    lifts that restriction for the duration of the call.
    """
    try:
        u32 = ctypes.windll.user32
        fg = u32.GetForegroundWindow()
        cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        fg_tid = u32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(u32.AttachThreadInput(fg_tid, cur_tid, True))
        u32.ShowWindow(hwnd, 9)          # SW_RESTORE
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
        u32.SetActiveWindow(hwnd)
        if attached:
            u32.AttachThreadInput(fg_tid, cur_tid, False)
    except Exception as e:
        logging.debug(f"Foreground forcing failed: {e}")


def _rank(host: str, needle: str) -> int:
    """Lower is better: exact < prefix < word-boundary < plain substring."""
    h = host.lower()
    if h == needle:
        return 0
    if h.startswith(needle):
        return 1
    for sep in ("_", "-", "."):
        if (sep + needle) in h:
            return 2
    return 3


# --- host list shared with the "Cerca file" window -----------------------

def build_host_rows(envs: Sequence[Tuple[str, List[str]]], favorites: Sequence[str],
                    recents: Sequence[str], needle: str, sel_env: str) -> List[dict]:
    """Rows of a host list: {'type': 'sep', 'label'} separators and
    {'type': 'host', 'env', 'host', 'tagged'} hosts.

    With an empty filter the list starts with "★ Preferiti" (user order) and
    "Recenti" (not already pinned), then one section per environment; while
    filtering, only the environment sections remain, best match first
    (favorites before the others). `sel_env` 'TEST'/'PROD' narrows it."""
    needle = (needle or "").strip().lower()
    sel_env = sel_env or "Tutti"
    favs = set(favorites)
    env_of = {h: name for name, hosts in envs for h in hosts}

    def visible(h):
        return h in env_of and (sel_env == "Tutti" or env_of[h] == sel_env)

    rows: List[dict] = []

    def section(label, hosts, tagged):
        if hosts:
            rows.append({"type": "sep", "label": label, "host": None})
            rows.extend({"type": "host", "env": env_of[h], "host": h,
                         "tagged": tagged} for h in hosts)

    if not needle:
        section(_STAR + " Preferiti", [h for h in favorites if visible(h)], True)
        section("Recenti", [h for h in recents if visible(h) and h not in favs], True)

    for name, hosts in envs:
        if sel_env != "Tutti" and sel_env != name:
            continue
        if needle:
            matching = sorted((h for h in hosts if needle in h.lower()),
                              key=lambda h: (h not in favs, _rank(h, needle), h.lower()))
        else:
            matching = list(hosts)
        section(name, matching, False)
    return rows


def fill_host_listbox(listbox, rows: List[dict], favorites: Sequence[str]) -> None:
    """Show `rows` in a tk.Listbox: separators greyed and visually
    unselectable, favorites starred, quick-access rows tagged with the env.
    Separator colours come from the current theme: refill after a switch."""
    favs = set(favorites)
    sep_bg, muted = theme.color("sep_bg"), theme.color("muted")
    listbox.delete(0, "end")
    for i, r in enumerate(rows):
        if r["type"] == "sep":
            listbox.insert("end", "  " + r["label"])
            listbox.itemconfig(i, bg=sep_bg, fg=muted,
                               selectbackground=sep_bg, selectforeground=muted)
        else:
            mark = _STAR + " " if r["host"] in favs else "   "
            tag = f"   ({r['env']})" if r["tagged"] else ""
            listbox.insert("end", "  " + mark + r["host"] + tag)


def next_host_row(rows: List[dict], start: int, step: int) -> int:
    """First host row from `start`+step in direction `step` (-1 if none)."""
    i = start + step
    while 0 <= i < len(rows):
        if rows[i]["type"] == "host":
            return i
        i += step
    return -1


class SearchPopup:
    """Pre-warmed host search popup.

    Usage:
        popup = SearchPopup(host_provider=..., on_select=...)
        popup.start()                 # once, at application start
        popup.show(initial_env=None)  # from any thread, instantaneous
        popup.stop()                  # on quit

    `host_provider` is called (on the popup thread) every time the popup is
    shown, so a `~/.ssh/config` edited while the app runs is picked up without
    restarting.
    """

    def __init__(self,
                 host_provider: Callable[[], Sequence[Tuple[str, List[str]]]],
                 on_select: Callable[[str], None],
                 on_file_search: Optional[Callable[[str], None]] = None):
        self._host_provider = host_provider
        self._on_select = on_select
        self._on_file_search = on_file_search
        self._requests: "queue.Queue[tuple]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._root = None
        self._envs: List[Tuple[str, List[str]]] = []
        self._rows: List[dict] = []   # [{'type':'sep'|'host', 'env':..., 'host':...}]
        self._initial_env = "Tutti"
        self._env_touched = False
        self._favs: List[str] = []
        self._recents: List[str] = []

    # ------------------------------------------------------------------
    # Public API (callable from any thread)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="SearchPopup")
        self._thread.start()
        # Wait so the first show() after startup is already warm; never block
        # the tray forever if Tk fails to initialise.
        self._ready.wait(timeout=5.0)

    def show(self, initial_env: Optional[str] = None) -> None:
        """Ask the popup thread to display the window. Returns immediately."""
        if not (self._thread and self._thread.is_alive()):
            logging.warning("Search popup not running; starting it now")
            self.start()
        self._requests.put(("show", initial_env))

    def run_on_ui(self, fn: Callable[[], None]) -> None:
        """Run `fn` on the Tk thread (asynchronously). Returns immediately."""
        if not (self._thread and self._thread.is_alive()):
            self.start()
        self._requests.put(("call", fn))

    @property
    def root(self):
        """The Tk root — only to be used from inside run_on_ui callbacks."""
        return self._root

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._requests.put(("quit", None))
            self._thread.join(timeout=2.0)
        self._thread = None

    # ------------------------------------------------------------------
    # Popup thread

    def _run(self) -> None:
        try:
            import tkinter as tk
            from tkinter import ttk

            root = tk.Tk()
            self._root = root
            root.title("SSH Connection Manager - Cerca host")
            theme.init(root, dark=AppSettings.get("dark_theme"))
            root.resizable(False, False)
            root.attributes("-topmost", True)
            root.withdraw()                      # stay hidden until show()
            # Closing the window only hides it: the interpreter must survive
            # so the next open stays instantaneous.
            root.protocol("WM_DELETE_WINDOW", self._hide)

            # --- header band -------------------------------------------
            band = theme.style(tk.Frame(root, height=px(48)), "header")
            band.pack(fill="x")
            band.pack_propagate(False)
            theme.style(tk.Label(band, text="Cerca host", font=("Segoe UI", 14, "bold")),
                        "header_title").pack(side="left", padx=20)

            # --- filter row --------------------------------------------
            row = theme.style(tk.Frame(root), "window")
            row.pack(fill="x", padx=20, pady=(12, 8))

            self._var_filter = tk.StringVar()
            entry = theme.style(tk.Entry(row, textvariable=self._var_filter,
                                         font=("Segoe UI", 12)), "entry")
            entry.pack(side="left", fill="x", expand=True, ipady=4)
            self._entry = entry

            self._var_env = tk.StringVar(value="Tutti")
            combo = ttk.Combobox(row, textvariable=self._var_env,
                                 values=list(_ENVS), state="readonly",
                                 width=7, font=("Segoe UI", 10))
            theme.style(combo, "combo")
            combo.pack(side="left", padx=(8, 0))
            self._combo = combo

            # --- results listbox ---------------------------------------
            body = theme.style(tk.Frame(root), "window")
            body.pack(fill="both", expand=True, padx=20)
            listbox = theme.style(tk.Listbox(body, font=("Segoe UI", 10), activestyle="none",
                                             height=12, exportselection=False), "listbox")
            listbox.pack(side="left", fill="both", expand=True)
            sb = ttk.Scrollbar(body, orient="vertical", command=listbox.yview)
            sb.pack(side="right", fill="y")
            listbox.config(yscrollcommand=sb.set)
            self._list = listbox

            self._var_status = tk.StringVar(value="")
            theme.style(tk.Label(root, textvariable=self._var_status,
                                 font=("Segoe UI", 8), anchor="w"), "hint").pack(
                         fill="x", padx=22, pady=(4, 0))

            # --- buttons -----------------------------------------------
            btns = theme.style(tk.Frame(root), "window")
            btns.pack(fill="x", padx=20, pady=10)
            theme.style(tk.Button(btns, text="Connetti", command=self._confirm, relief="flat",
                                  font=("Segoe UI", 9, "bold"), width=12, cursor="hand2"),
                        "success").pack(side="right")
            theme.style(tk.Button(btns, text="Annulla", command=self._hide, relief="flat",
                                  font=("Segoe UI", 9), width=12, cursor="hand2"),
                        "button").pack(side="right", padx=(0, 8))

            # --- bindings ----------------------------------------------
            # Typing filters live; arrows/Enter/Esc work from the entry so the
            # user never has to leave the keyboard or click the list.
            self._var_filter.trace_add("write", lambda *_: self._refresh())
            combo.bind("<<ComboboxSelected>>", self._on_env_changed)
            for w in (root, entry, listbox, combo):
                w.bind("<Escape>", lambda e: self._hide())
                w.bind("<Return>", lambda e: self._confirm())
                w.bind("<Down>", lambda e: self._move(1))
                w.bind("<Up>", lambda e: self._move(-1))
                w.bind("<Next>", lambda e: self._move(10))
                w.bind("<Prior>", lambda e: self._move(-10))
                w.bind("<Control-d>", lambda e: self._toggle_favorite())
                w.bind("<Control-D>", lambda e: self._toggle_favorite())
                w.bind("<Control-f>", lambda e: self._file_search())
                w.bind("<Control-F>", lambda e: self._file_search())
            listbox.bind("<Double-Button-1>", lambda e: self._confirm())
            listbox.bind("<<ListboxSelect>>", self._on_click_select)
            # Separator rows carry their own colours: refill on a theme switch.
            theme.on_change(lambda: self._refresh(keep_host=self._current_host()))

            root.update_idletasks()
            self._ready.set()
            root.after(50, self._poll)
            root.mainloop()
        except Exception as e:
            logging.error(f"Search popup thread failed: {e}", exc_info=True)
            self._ready.set()

    # ------------------------------------------------------------------
    # Request pump (bridges other threads -> Tk thread)

    def _poll(self) -> None:
        try:
            while True:
                try:
                    kind, arg = self._requests.get_nowait()
                except queue.Empty:
                    break
                if kind == "show":
                    self._do_show(arg)
                elif kind == "call":
                    try:
                        arg()
                    except Exception as e:
                        logging.error(f"UI call failed: {e}", exc_info=True)
                elif kind == "quit":
                    self._root.quit()
                    return
        except Exception as e:
            logging.error(f"Search popup poll failed: {e}", exc_info=True)
        self._root.after(50, self._poll)

    # ------------------------------------------------------------------
    # Show / hide

    def _do_show(self, initial_env: Optional[str]) -> None:
        try:
            self._envs = [(n, list(h)) for n, h in (self._host_provider() or [])]
        except Exception as e:
            logging.error(f"Host provider failed: {e}", exc_info=True)
            self._envs = []

        env = initial_env or load_prefs_env()
        if env not in _ENVS:
            env = "Tutti"
        self._initial_env = env
        self._env_touched = False
        self._favs = AppSettings.favorites()
        self._recents = AppSettings.recents()
        self._var_env.set(env)
        self._var_filter.set("")     # also triggers _refresh() via the trace
        self._refresh()

        root = self._root
        self._center()
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.update_idletasks()
        _force_foreground(self._hwnd())
        self._entry.focus_force()
        self._entry.selection_range(0, "end")

    def _hide(self) -> str:
        try:
            self._root.withdraw()
        except Exception as e:
            logging.debug(f"Hide failed: {e}")
        return "break"

    def _hwnd(self) -> int:
        """Top-level window handle (winfo_id returns the Tk child window)."""
        try:
            return int(self._root.wm_frame(), 16)
        except Exception:
            return self._root.winfo_id()

    def _center(self) -> None:
        root = self._root
        w, h = px(480), px(430)
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - w) // 2
        y = max(60, (sh - h) // 3)   # slightly above centre, launcher style
        root.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------
    # List content

    def _refresh(self, keep_host: Optional[str] = None) -> None:
        rows = build_host_rows(self._envs, self._favs, self._recents,
                               self._var_filter.get(), self._var_env.get())
        self._rows = rows
        fill_host_listbox(self._list, rows, self._favs)

        n_hosts = len({r["host"] for r in rows if r["type"] == "host"})
        self._var_status.set(
            "{} host  -  Invio connette  -  Ctrl+D preferito  -  Ctrl+F cerca file  -  "
            "Esc chiude".format(n_hosts))
        target = -1
        if keep_host:
            target = next((i for i, r in enumerate(rows)
                           if r["type"] == "host" and r["host"] == keep_host), -1)
        self._select(target if target >= 0 else self._next_host(-1, 1))

    def _toggle_favorite(self) -> str:
        idx = self._current()
        if not (0 <= idx < len(self._rows)) or self._rows[idx]["type"] != "host":
            return "break"
        host = self._rows[idx]["host"]
        AppSettings.toggle_favorite(host)
        self._favs = AppSettings.favorites()
        self._refresh(keep_host=host)
        return "break"

    def _file_search(self) -> str:
        """Ctrl+F: open the file search window on the selected host."""
        idx = self._current()
        if (self._on_file_search is None or not (0 <= idx < len(self._rows))
                or self._rows[idx]["type"] != "host"):
            return "break"
        host = self._rows[idx]["host"]
        self._hide()
        try:
            self._on_file_search(host)
        except Exception as e:
            logging.error(f"Search on_file_search failed: {e}", exc_info=True)
        return "break"

    def _next_host(self, start: int, step: int) -> int:
        return next_host_row(self._rows, start, step)

    def _select(self, idx: int) -> None:
        lb = self._list
        lb.selection_clear(0, "end")
        if idx >= 0:
            lb.selection_set(idx)
            lb.activate(idx)
            lb.see(idx)

    def _current(self) -> int:
        sel = self._list.curselection()
        return sel[0] if sel else -1

    def _current_host(self) -> Optional[str]:
        idx = self._current()
        return self._rows[idx]["host"] if 0 <= idx < len(self._rows) else None

    def _move(self, delta: int) -> str:
        """Move the selection by `delta` host rows, skipping separators."""
        if not self._rows:
            return "break"
        cur = self._current()
        step = 1 if delta > 0 else -1
        idx = cur
        for _ in range(abs(delta)):
            nxt = self._next_host(idx, step)
            if nxt < 0:
                break
            idx = nxt
        if idx >= 0 and idx != cur:
            self._select(idx)
        return "break"

    def _on_click_select(self, _event) -> None:
        # A click can land on a separator row: bounce it to the next host.
        idx = self._current()
        if 0 <= idx < len(self._rows) and self._rows[idx]["type"] == "sep":
            self._select(self._next_host(idx, 1))

    def _on_env_changed(self, _event=None) -> None:
        self._env_touched = True
        self._refresh()
        self._entry.focus_set()

    # ------------------------------------------------------------------
    # Confirm

    def _confirm(self) -> str:
        idx = self._current()
        if not (0 <= idx < len(self._rows)) or self._rows[idx]["type"] != "host":
            return "break"
        host = self._rows[idx]["host"]

        # Persist the env only when the user explicitly changed the combo: an
        # env forced by a section submenu must not clobber the hotkey pref.
        if self._env_touched:
            env = self._var_env.get()
            if env in _ENVS and env != self._initial_env:
                save_prefs_env(env)

        self._hide()
        logging.info("Search selected host: %s", host)
        try:
            self._on_select(host)
        except Exception as e:
            logging.error(f"Search on_select failed: {e}", exc_info=True)
        return "break"


# ----------------------------------------------------------------------
# Manual smoke test:  py -m ssh_connection.gui.search_dialog
if __name__ == "__main__":  # pragma: no cover
    import time

    logging.basicConfig(level=logging.DEBUG)
    theme.enable_dpi_awareness()
    _demo = [("TEST", ["login_test", "stlit1tf01", "stlit1tf02", "app_test"]),
             ("PROD", ["login_prod", "stlit1pf01", "db_prod", "app_prod"])]
    _p = SearchPopup(host_provider=lambda: _demo,
                     on_select=lambda h: print("SELECTED:", h))
    _p.start()
    _p.show()
    while _p._thread and _p._thread.is_alive():
        time.sleep(0.2)
