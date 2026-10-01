"""
"Cerca file" window: browse the folders of a host like WinSCP, filter or
search files (by name and optionally content), open them (downloaded and
shown in the text editor) or download them.

Same Tk interpreter and thread as the search popup and the settings dialog
(a Toplevel driven through `SearchPopup.run_on_ui()`). Network work never
runs on the Tk thread: every listing / search / download goes to a worker
thread using a `RemoteSession` (one SSH connection per chosen host, reused
until the host changes or the window closes), and results come back through
`run_on_ui`. One operation at a time per host; "Interrompi" closes the
running remote command. Picking another host drops the old session: a late
result of the old host is discarded.

Layout: host panel on the left (same rows as the host popup), on the right
the path bar (◀ back, ▲ up, ⌂ home, editable path with the folder history,
⟳ refresh), the name / content filters and the file list.
- Browsing (default): the list shows the current folder, folders first,
  with ".." on top. Double click / Enter enters a folder or opens a file,
  Backspace goes up. Symbolic links are followed and the *logical* path is
  kept (see RemoteSession.listdir). Typing in "Nome" filters the current
  folder locally, without touching the server.
- Search: "Cerca" (or Enter in "Nome") runs `find` from the current folder
  (with subfolders unless unchecked); results show their folder, and
  "Vai alla cartella" browses to the folder of the selected result.

A folder is listed automatically when a host is picked only if its tunnel
is already up (`route_ready`): browsing the host list must never pop up a
jump host login. Otherwise an explicit action (Enter, ⟳, a path) connects,
opening the login like a terminal connection would.

"Apri" works like WinSCP's "open": the file is downloaded to a private
temporary folder (OPEN_DIR/<host>/<unique>/name), shown in the text editor
chosen by the user (gui.text_editor: "Apri con" with Solo questa volta /
Sempre, Blocco note, Notepad++, VS Code...), and the local copy is deleted
when the editor is closed. It is a read-only copy: edits are never uploaded
back. When the editor process hands the file to an already open window and
exits at once (Windows 11 Notepad tabs, Notepad++, VS Code), or the file is
opened with another program, the copy is removed later by
`purge_open_dir()` (at application exit and at the next start).

The folders visited and searched are remembered per host (AppSettings
`file_search_paths`, most recent first) and offered in the path combo.
"""

import fnmatch
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from ..config.app_settings import AppSettings
from ..ssh.remote_files import (MAX_RESULTS, RemoteError, RemoteFile, RemoteSession, glob_for,
                                is_supported_host, parent_path, route_ready)
from . import text_editor, theme, widgets
from .search_dialog import (_ENVS, _force_foreground, build_host_rows, fill_host_listbox,
                            next_host_row)
from .theme import px

# Files opened with "Apri" are downloaded here first.
OPEN_DIR = Path(tempfile.gettempdir()) / "SSH-Connection-Manager"
# An editor process living less than this handed the file to another
# window: the copy may still be loading, so it is left for purge_open_dir.
EDITOR_HANDOFF_SECONDS = 5.0
# Ask before downloading bigger files with "Apri".
BIG_FILE = 50 * 1024 * 1024
# Opened with their associated program instead of the text editor (binary content).
BINARY_EXTENSIONS = {".gz", ".tgz", ".zip", ".tar", ".bz2", ".xz", ".7z", ".jar",
                     ".war", ".ear", ".pdf", ".png", ".jpg", ".jpeg", ".gif",
                     ".xlsx", ".xls", ".docx", ".doc"}
# Delay before listing the folder of a freshly picked host (typing in the
# host filter changes host at every key: don't connect at every key).
AUTO_LIST_DELAY_MS = 400

UP_IID = "up"


def format_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return str(n)


def format_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def name_matches(name: str, name_filter: str) -> bool:
    """Local filter of the current folder, same rules as the remote search
    (plain text = contains, wildcards as typed, case-insensitive)."""
    return fnmatch.fnmatchcase(name.lower(), glob_for(name_filter).lower())


def purge_open_dir(max_age_seconds: float = 0) -> None:
    """Delete the temporary copies made by "Apri" (only those older than
    `max_age_seconds`, when given)."""
    if not OPEN_DIR.is_dir():
        return
    now = time.time()
    for host_dir in OPEN_DIR.iterdir():
        for copy_dir in (host_dir.iterdir() if host_dir.is_dir() else []):
            try:
                if max_age_seconds and now - copy_dir.stat().st_mtime < max_age_seconds:
                    continue
                shutil.rmtree(copy_dir, ignore_errors=True)
            except OSError:
                pass
        try:
            host_dir.rmdir()          # only if empty
        except OSError:
            pass


def _remove_copy(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)
    try:
        path.parent.parent.rmdir()    # host folder, if now empty
    except OSError:
        pass


def downloads_dir() -> Path:
    d = Path.home() / "Downloads"
    return d if d.is_dir() else Path.home()


class FileSearchDialog:
    def __init__(self, ui_host,
                 host_provider: Callable[[], Sequence[Tuple[str, List[str]]]]):
        # Leftovers of a previous run (copies handed to another editor
        # window, or the app closed while the editor was open).
        threading.Thread(target=purge_open_dir, args=(3600,), daemon=True).start()
        self._ui = ui_host
        self._host_provider = host_provider
        self._top = None
        self._session: Optional[RemoteSession] = None
        self._busy_session: Optional[RemoteSession] = None
        self._env_of = {}
        self._hosts: List[str] = []
        self._envs: List[Tuple[str, List[str]]] = []
        self._favs: List[str] = []
        self._recents: List[str] = []
        self._host_rows: Optional[List[dict]] = None
        self._env_initialized = False
        self._current_host = ""
        # browsing state
        self._folder = ""                  # listed folder ("" = nothing listed yet)
        self._entries: List[RemoteFile] = []
        self._back: List[str] = []
        self._mode = "browse"              # or "search"
        self._results: List[RemoteFile] = []
        self._search_root = ""
        self._rows: List[RemoteFile] = []  # rows shown, by tree iid
        self._sort = ("name", False)

    @property
    def _busy(self) -> bool:
        return self._busy_session is not None and self._busy_session is self._session

    # ------------------------------------------------------------------
    # Public API (any thread)

    def show(self, host: Optional[str] = None) -> None:
        self._ui.run_on_ui(lambda: self._do_show(host))

    # ------------------------------------------------------------------
    # Build (Tk thread)

    def _build(self) -> None:
        if self._top is not None:
            return
        import tkinter as tk
        from tkinter import ttk
        w = widgets

        top = tk.Toplevel(self._ui.root)
        self._top = top
        top.title("SSH Connection Manager - Cerca file")
        theme.toplevel(top)
        top.minsize(px(900), px(560))
        top.withdraw()
        top.protocol("WM_DELETE_WINDOW", self._hide)
        top.bind("<Escape>", lambda e: self._hide())
        self._icon_folder, self._icon_file = w.folder_image(), w.file_image()

        def frame(parent, surface="bg", **kw):
            return theme.style(tk.Frame(parent, **kw), "window", surface)

        # --- host panel (left, navigation pane): same list as the host popup
        left = frame(top, "nav", width=px(270), padx=px(14), pady=px(14))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        w.section_title(left, "Host", "nav").pack(anchor="w", pady=(0, px(8)))
        self._var_host_filter = tk.StringVar()
        host_search = w.SearchEntry(left, self._var_host_filter, "Filtra host...", "nav")
        host_search.pack(fill="x")
        host_entry = host_search.entry
        self._host_entry = host_entry
        self._var_env = tk.StringVar(value="Tutti")
        self._env_segmented = w.Segmented(left, _ENVS, self._var_env,
                                          command=self._on_env_changed, surface="nav")
        self._env_segmented.pack(anchor="w", pady=(px(8), px(8)))
        host_list = w.ListView(left, surface="nav", detail_width=56, font_size=10)
        host_list.pack(fill="both", expand=True)
        self._host_list = host_list
        w.label(left, "↑↓ scegli  ·  Invio apre  ·  Ctrl+D preferito", "hint", "nav",
                size=8, anchor="w").pack(anchor="w", pady=(px(6), 0))

        self._var_host_filter.trace_add("write", lambda *_: self._on_host_filter())
        host_list.bind("<<ListboxSelect>>", lambda e: self._on_host_clicked())
        for wd in (host_entry, host_list):
            wd.bind("<Down>", lambda e: self._move_host(1))
            wd.bind("<Up>", lambda e: self._move_host(-1))
            wd.bind("<Next>", lambda e: self._move_host(10))
            wd.bind("<Prior>", lambda e: self._move_host(-10))
            wd.bind("<Control-d>", lambda e: self._toggle_favorite())
            wd.bind("<Control-D>", lambda e: self._toggle_favorite())
            # Enter: this host, list its folder now (connecting if needed)
            wd.bind("<Return>", lambda e: self._host_confirmed() or "break")

        # --- right side ----------------------------------------------------
        right = frame(top, padx=px(18), pady=px(14))
        right.pack(side="left", fill="both", expand=True)
        hline = frame(right)
        hline.pack(fill="x")
        self._host_title = w.label(hline, "", "title", size=16, weight="semibold")
        self._host_title.pack(side="left")
        self._env_label = theme.style(tk.Label(hline, text="", font=theme.font(8, "semibold"),
                                               padx=px(8), pady=px(1)), "env_none")
        self._env_label.pack(side="left", padx=px(10))

        # path bar: back / up / home / path / refresh
        pbar = frame(right)
        pbar.pack(fill="x", pady=(px(10), px(8)))
        self._nav_btns = []
        for name, tip, cmd in (("back", "Indietro (Alt+←)", self._go_back),
                               ("up", "Cartella superiore (Backspace)", self._go_up),
                               ("home", "Home (~)", self._go_home)):
            b = w.IconButton(pbar, name, cmd, tooltip=tip)
            b.pack(side="left", padx=(0, px(2)))
            self._nav_btns.append(b)
        self._var_path = tk.StringVar()
        path_combo = theme.style(ttk.Combobox(pbar, textvariable=self._var_path,
                                              font=theme.font(10)), "combo")
        path_combo.pack(side="left", fill="x", expand=True, padx=(px(6), px(4)))
        path_combo.bind("<Return>", lambda e: self._path_entered() or "break")
        path_combo.bind("<<ComboboxSelected>>", lambda e: self._path_entered())
        self._path_combo = path_combo
        b = w.IconButton(pbar, "refresh", self._refresh_folder, tooltip="Aggiorna")
        b.pack(side="left")
        self._nav_btns.append(b)

        # filters + search
        form = frame(right)
        form.pack(fill="x")
        self._var_name = tk.StringVar()
        name_box = w.SearchEntry(form, self._var_name, "Nome: parte del nome o *.log")
        name_box.pack(side="left", fill="x", expand=True)
        name_entry = name_box.entry
        self._name_entry = name_entry
        self._var_text = tk.StringVar()
        text_box = w.SearchEntry(form, self._var_text, "Contiene testo...", icon=False)
        text_box.pack(side="left", fill="x", expand=True, padx=(px(8), 0))
        text_entry = text_box.entry
        self._var_recursive = tk.BooleanVar(value=True)
        theme.style(tk.Checkbutton(form, text="Sottocartelle", variable=self._var_recursive,
                                   font=theme.font(10), cursor="hand2"), "check").pack(
                                       side="left", padx=(px(10), 0))
        self._search_btn = w.Button(form, "Cerca", self._search_or_stop, "primary", width=10)
        self._search_btn.pack(side="left", padx=(px(10), 0))
        w.label(right, "Scrivendo nel Nome filtri la cartella; Invio o Cerca cercano anche "
                       "nelle sottocartelle (es. *.log, app-202?-*).", "hint", size=8,
                anchor="w").pack(fill="x", pady=(px(4), 0))
        self._var_name.trace_add("write", lambda *_: self._on_name_filter())
        for wd in (name_entry, text_entry):
            wd.bind("<Return>", lambda e: self._search_or_stop() or "break")

        # search-results banner (hidden while browsing)
        self._banner = theme.style(tk.Frame(right, padx=px(10), pady=px(4)), "banner")
        self._var_banner = tk.StringVar()
        theme.style(tk.Label(self._banner, textvariable=self._var_banner, font=theme.font(9),
                             anchor="w"), "banner_label").pack(side="left")
        theme.style(tk.Button(self._banner, text="Torna alla cartella", command=self._back_to_folder,
                              font=theme.font(9, "semibold"), cursor="hand2"),
                    "banner_button").pack(side="right")

        self._tframe = tframe = theme.style(tk.Frame(right), "field_frame")
        tframe.pack(fill="both", expand=True, pady=(px(8), 0))
        tree = theme.style(ttk.Treeview(tframe, columns=("folder", "size", "mtime"),
                                        show="tree headings", selectmode="browse"), "tree")
        tree.heading("#0", text="Nome", anchor="w", command=lambda: self._sort_by("name"))
        tree.column("#0", width=px(300), stretch=True)
        for col, title, width, anchor in (("folder", "Cartella", 240, "w"),
                                          ("size", "Dimensione", 100, "e"),
                                          ("mtime", "Modificato", 140, "w")):
            tree.heading(col, text=title, anchor=anchor, command=lambda c=col: self._sort_by(c))
            tree.column(col, width=px(width), anchor=anchor, stretch=(col == "folder"))
        tree.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        sb = ttk.Scrollbar(tframe, orient="vertical", command=tree.yview)
        sb.pack(side="right", fill="y", pady=1)
        tree.configure(yscrollcommand=sb.set)
        tree.bind("<Double-Button-1>", lambda e: self._activate())
        tree.bind("<Return>", lambda e: self._activate() or "break")
        tree.bind("<BackSpace>", lambda e: self._go_up() or "break")
        tree.bind("<Alt-Left>", lambda e: self._go_back() or "break")
        tree.bind("<Control-c>", lambda e: self._copy_path())
        self._tree = tree

        self._var_status = tk.StringVar()
        self._status_label = theme.style(
            tk.Label(right, textvariable=self._var_status, font=theme.font(9), anchor="w",
                     justify="left", wraplength=px(780)), "hint")
        self._status_label.pack(fill="x", pady=(px(8), 0))

        btns = frame(right)
        btns.pack(fill="x", pady=(px(10), 0))
        w.Button(btns, "Chiudi", self._hide).pack(side="right")
        self._action_btns = []
        for text, cmd, kind in (("Apri", self._activate, "primary"),
                                ("Scarica...", self._download, "button"),
                                ("Copia percorso", self._copy_path, "button"),
                                ("Vai alla cartella", self._goto_result_folder, "button")):
            b = w.Button(btns, text, cmd, kind)
            b.pack(side="left", padx=(0, px(8)))
            self._action_btns.append(b)
        self._goto_btn = self._action_btns[-1]
        # Status icons of the hosts and file icons follow the theme.
        theme.on_change(self._on_theme_changed)
        self._apply_mode()
        top.update_idletasks()

    # ------------------------------------------------------------------
    # Show / hide (Tk thread)

    def _do_show(self, host: Optional[str]) -> None:
        self._build()
        try:
            envs = [(n, list(h)) for n, h in (self._host_provider() or [])]
        except Exception as e:
            logging.error(f"Host provider failed: {e}", exc_info=True)
            envs = []
        # Jump hosts can't be browsed (2FA token): not offered at all.
        self._envs = [(n, [h for h in hs if is_supported_host(h)]) for n, hs in envs]
        self._env_of = {h: env for env, hosts in self._envs for h in hosts}
        self._hosts = [h for _, hs in self._envs for h in hs]
        requested = host in self._env_of
        self._favs = AppSettings.favorites()
        self._recents = AppSettings.recents()

        if host not in self._env_of:
            host = self._current_host
        if host not in self._env_of:
            host = next((h for h in self._favs + self._recents if h in self._env_of),
                        self._hosts[0] if self._hosts else "")
        if not self._env_initialized:
            self._env_initialized = True
            self._var_env.set(AppSettings.get("search_env"))
        if self._var_env.get() not in ("Tutti", self._env_of.get(host)):
            self._var_env.set("Tutti")        # the requested host must be visible
        self._var_host_filter.set("")         # also refreshes the list
        if host != self._current_host or self._session is None:
            self._set_host(host, force=True)
        self._refresh_hosts()

        top = self._top
        if not top.winfo_viewable():
            w, h = px(1140), px(720)
            x = (top.winfo_screenwidth() - w) // 2
            y = max(40, (top.winfo_screenheight() - h) // 3)
            top.geometry(f"{w}x{h}+{x}+{y}")
        top.deiconify()
        top.lift()
        top.update_idletasks()
        try:
            _force_foreground(int(top.wm_frame(), 16))
        except Exception:
            pass
        # Host given (Ctrl+F from the popup): straight to the file list;
        # otherwise the host field, to type or pick one.
        if requested:
            self._tree.focus_force()
        else:
            self._host_entry.focus_force()
            self._host_entry.selection_range(0, "end")

    def _hide(self) -> str:
        if self._top is not None:
            self._top.withdraw()
        self._drop_session()
        return "break"

    def _drop_session(self) -> None:
        session, self._session = self._session, None
        self._busy_session = None
        if session is not None:
            session.cancel()
            threading.Thread(target=session.close, daemon=True).start()

    # --- host panel -------------------------------------------------------

    def _refresh_hosts(self) -> None:
        """Rebuild the host list (filter + env) and highlight the current
        host when it is listed. Typing a filter selects the best match."""
        rows = build_host_rows(self._envs, self._favs, self._recents,
                               self._var_host_filter.get(), self._var_env.get())
        self._host_rows = rows
        fill_host_listbox(self._host_list, rows, self._favs, self._ui_active_hosts())
        idx = next((i for i, r in enumerate(rows)
                    if r["type"] == "host" and r["host"] == self._current_host), -1)
        filtering = bool(self._var_host_filter.get().strip())
        if filtering:
            best = next_host_row(rows, -1, 1)
            if best >= 0:
                self._select_host_row(best)
            else:
                self._host_list.selection_clear(0, "end")
                self._status(f"Nessun host contiene \"{self._var_host_filter.get().strip()}\".",
                             error=True)
        elif idx >= 0:
            self._select_host_row(idx, commit=False)
        else:
            self._host_list.selection_clear(0, "end")

    def _ui_active_hosts(self) -> set:
        active = getattr(self._ui, "active_hosts", None)
        return active() if callable(active) else set()

    def _on_theme_changed(self) -> None:
        self._icon_folder, self._icon_file = widgets.folder_image(), widgets.file_image()
        if self._host_rows is not None:
            self._refresh_hosts()
        f = self._selected()
        self._render(select_name=f.name if f else None)

    def _select_host_row(self, idx: int, commit: bool = True) -> None:
        lb = self._host_list
        lb.selection_clear(0, "end")
        lb.selection_set(idx)
        lb.activate(idx)
        lb.see(idx)
        if commit:
            self._set_host(self._host_rows[idx]["host"])

    def _on_host_filter(self) -> None:
        if self._top is not None and self._host_rows is not None:
            self._refresh_hosts()

    def _on_env_changed(self) -> None:
        self._refresh_hosts()
        self._host_entry.focus_set()

    def _on_host_clicked(self) -> None:
        sel = self._host_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if self._host_rows[idx]["type"] == "sep":        # bounce off separators
            idx = next_host_row(self._host_rows, idx, 1)
            if idx < 0:
                return
        self._select_host_row(idx)

    def _move_host(self, delta: int) -> str:
        """Move the host selection by `delta` hosts, skipping separators."""
        rows = self._host_rows or []
        sel = self._host_list.curselection()
        cur = idx = sel[0] if sel else -1
        step = 1 if delta > 0 else -1
        for _ in range(abs(delta)):
            nxt = next_host_row(rows, idx, step)
            if nxt < 0:
                break
            idx = nxt
        if idx >= 0 and idx != cur:
            self._select_host_row(idx)
        return "break"

    def _toggle_favorite(self) -> str:
        """Ctrl+D: pin / unpin the current host (shared with menu and popup)."""
        if self._current_host:
            now = AppSettings.toggle_favorite(self._current_host)
            self._favs = AppSettings.favorites()
            self._refresh_hosts()
            self._status(f"{self._current_host} {'aggiunto ai' if now else 'rimosso dai'} preferiti")
        return "break"

    def _host_confirmed(self) -> None:
        """Enter in the host panel: list the folder now (explicit action,
        so a jump host login may open if the tunnel is down)."""
        if self._current_host and not self._folder:
            self._navigate(self._var_path.get() or "~", push=False)
        self._tree.focus_set()

    def _set_host(self, host: str, force: bool = False) -> None:
        if host == self._current_host and not force:
            return
        self._current_host = host
        self._drop_session()                  # also cancels a running operation
        self._set_busy(False)
        if host:
            self._session = RemoteSession(host)
        env = self._env_of.get(host, "")
        self._host_title.configure(text=host or "Nessun host")
        self._env_label.configure(text=env)
        theme.set_role(self._env_label, "env_prod" if env == "PROD" else
                       "env_test" if env else "env_none")
        history = AppSettings.file_search_paths(host) if host else []
        self._path_combo["values"] = history
        self._var_path.set(history[0] if history else "~")
        self._folder, self._entries, self._back = "", [], []
        self._results, self._mode = [], "browse"
        self._apply_mode()
        self._render()
        self._status("")
        if host and self._top is not None:
            self._top.after(AUTO_LIST_DELAY_MS, lambda: self._auto_list(host))

    def _auto_list(self, host: str) -> None:
        """List the default folder of a freshly picked host, but only when
        its tunnel is already up (probe on a worker thread)."""
        if host != self._current_host or self._folder or self._busy:
            return
        session = self._session

        def probe():
            ready = route_ready(host)

            def apply():
                if self._session is not session or self._folder or self._busy:
                    return
                if ready:
                    self._navigate(self._var_path.get() or "~", push=False)
                else:
                    self._status(f"Il tunnel di {host} non è attivo. Premi Invio (o ⟳) per "
                                 f"connetterti: se serve si apre il login con il token.")
            self._ui.run_on_ui(apply)
        threading.Thread(target=probe, daemon=True, name="RouteProbe").start()

    # ------------------------------------------------------------------
    # Helpers (Tk thread)

    def _status(self, text: str, error: bool = False) -> None:
        self._var_status.set(text)
        theme.set_role(self._status_label, "error" if error else "hint")

    def _set_busy(self, busy: bool, session: Optional[RemoteSession] = None) -> None:
        self._busy_session = session if busy else None
        self._search_btn.configure(text="Interrompi" if busy else "Cerca")
        theme.set_role(self._search_btn, "danger" if busy else "primary")
        for b in self._action_btns + self._nav_btns:
            b.configure(state="disabled" if busy else "normal")
        if not busy:
            self._apply_mode()

    def _apply_mode(self) -> None:
        """Browse: no Cartella column, no banner. Search: both, plus the
        "Vai alla cartella" button."""
        searching = self._mode == "search"
        self._tree.configure(displaycolumns=("folder", "size", "mtime") if searching
                             else ("size", "mtime"))
        if searching:
            self._var_banner.set(f"Risultati della ricerca in {self._search_root}")
            self._banner.pack(fill="x", pady=(4, 0), before=self._tframe)
        else:
            self._banner.pack_forget()
        self._goto_btn.configure(state="normal" if searching and not self._busy else "disabled")

    def _visible_entries(self) -> List[RemoteFile]:
        if self._mode == "search":
            return list(self._results)
        name_filter = self._var_name.get()
        if not name_filter.strip():
            return list(self._entries)
        return [e for e in self._entries if name_matches(e.name, name_filter)]

    def _render(self, select_name: Optional[str] = None) -> None:
        key, reverse = self._sort
        getter = {"name": lambda f: f.name.lower(), "folder": lambda f: f.path.lower(),
                  "size": lambda f: f.size, "mtime": lambda f: f.mtime}[key]
        rows = sorted(self._visible_entries(), key=getter, reverse=reverse)
        if self._mode == "browse":
            rows.sort(key=lambda f: not f.is_dir)       # stable: folders first
        self._rows = rows
        tree = self._tree
        tree.delete(*tree.get_children())
        if self._mode == "browse" and self._folder and self._folder != "/":
            tree.insert("", "end", iid=UP_IID, text="  ..", image=self._icon_folder,
                        values=("", "", ""))
        for i, f in enumerate(rows):
            label = "  " + f.name + ("  →" if f.is_link else "")
            tree.insert("", "end", iid=str(i), text=label,
                        image=self._icon_folder if f.is_dir else self._icon_file,
                        values=(f.folder, "" if f.is_dir else format_size(f.size),
                                format_time(f.mtime)))
        for col, title in (("#0", "Nome"), ("folder", "Cartella"),
                           ("size", "Dimensione"), ("mtime", "Modificato")):
            active = (col == "#0" and key == "name") or col == key
            tree.heading(col, text=title + ((" ▼" if reverse else " ▲") if active else ""))
        children = tree.get_children()
        target = None
        if select_name is not None:
            target = next((str(i) for i, f in enumerate(rows) if f.name == select_name), None)
        if target is None and children:
            target = children[1] if children[0] == UP_IID and len(children) > 1 else children[0]
        if target is not None:
            tree.selection_set(target)
            tree.focus(target)
            tree.see(target)

    def _sort_by(self, key: str) -> None:
        cur, reverse = self._sort
        self._sort = (key, not reverse if key == cur else key in ("size", "mtime"))
        self._render()

    def _selected(self) -> Optional[RemoteFile]:
        """Selected row; None for nothing or the '..' row."""
        sel = self._tree.selection()
        if not sel or sel[0] == UP_IID:
            return None
        try:
            return self._rows[int(sel[0])]
        except (ValueError, IndexError):
            return None

    def _selected_file(self) -> Optional[RemoteFile]:
        f = self._selected()
        if f is None or f.is_dir:
            self._status("Seleziona un file.", error=True)
            return None
        return f

    def _run_async(self, work: Callable[[RemoteSession], Callable[[], None]]) -> None:
        """Run `work(session)` on a worker thread. It returns a callable
        applied on the Tk thread; RemoteError messages end up in the status.
        A result of a session dropped meanwhile (host changed) is ignored."""
        session = self._session
        if session is None or self._busy:
            return
        self._set_busy(True, session)

        def status(text):
            self._ui.run_on_ui(lambda: self._session is session and self._status(text))

        def worker():
            try:
                session.connect(status)
                done = work(session)
            except RemoteError as e:
                msg = str(e)
                done = lambda: self._status(msg, error=True)
            except Exception as e:
                logging.error(f"File search operation failed: {e}", exc_info=True)
                msg = f"Errore: {e}"
                done = lambda: self._status(msg, error=True)

            def finish():
                if self._session is not session:    # host changed meanwhile
                    return
                self._set_busy(False)
                done()
            self._ui.run_on_ui(finish)

        threading.Thread(target=worker, daemon=True, name="FileSearch").start()

    # ------------------------------------------------------------------
    # Navigation (Tk thread)

    def _navigate(self, folder: str, push: bool = True,
                  select_name: Optional[str] = None) -> None:
        host = self._current_host
        if not host:
            self._status("Scegli un host.", error=True)
            return
        previous = self._folder
        self._status(f"Apro {folder} su {host}...")

        def work(session):
            path, entries = session.listdir(folder)

            def done():
                if push and previous and previous != path:
                    self._back.append(previous)
                self._folder, self._entries = path, entries
                self._mode, self._results = "browse", []
                self._var_path.set(path)
                AppSettings.add_file_search_path(host, path)
                self._path_combo["values"] = AppSettings.file_search_paths(host)
                AppSettings.add_recent(host)          # same "Recenti" as menu and popup
                self._recents = AppSettings.recents()
                self._apply_mode()
                self._render(select_name)
                n_dirs = sum(1 for e in entries if e.is_dir)
                self._status(f"{path}  -  {n_dirs} cartelle, {len(entries) - n_dirs} file  -  "
                             f"doppio clic: entra / apri  -  Backspace: su  -  "
                             f"Ctrl+C: copia percorso")
            return done

        self._run_async(work)

    def _path_entered(self) -> None:
        self._navigate(self._var_path.get().strip() or "~")
        self._tree.focus_set()

    def _refresh_folder(self) -> None:
        self._navigate(self._folder or self._var_path.get().strip() or "~", push=False)

    def _go_up(self) -> None:
        if self._folder and self._folder != "/":
            name = self._folder.rstrip("/").rsplit("/", 1)[-1]
            self._navigate(parent_path(self._folder), select_name=name)

    def _go_back(self) -> None:
        if self._back:
            self._navigate(self._back.pop(), push=False)

    def _go_home(self) -> None:
        self._navigate("~")

    def _activate(self) -> None:
        """Double click / Enter / Apri: '..' goes up, a folder is entered, a
        file is opened."""
        sel = self._tree.selection()
        if sel and sel[0] == UP_IID:
            self._go_up()
            return
        f = self._selected()
        if f is None:
            return
        if f.is_dir:
            self._navigate(f.path)
        else:
            self._open(f)

    def _on_name_filter(self) -> None:
        if self._top is not None and self._mode == "browse" and self._folder:
            self._render()

    # ------------------------------------------------------------------
    # Search (Tk thread)

    def _search_or_stop(self) -> None:
        if self._busy:
            if self._session is not None:
                self._session.cancel()
            return
        host = self._current_host
        root = self._folder or self._var_path.get().strip() or "~"
        name, text = self._var_name.get(), self._var_text.get()
        recursive = self._var_recursive.get()
        if not host:
            self._status("Scegli un host.", error=True)
            return
        self._status(f"Cerco in {root} su {host}...")
        started = time.monotonic()

        def work(session):
            files, truncated = session.find(root, name, text, recursive)
            elapsed = time.monotonic() - started

            def done():
                AppSettings.add_file_search_path(host, root)
                self._path_combo["values"] = AppSettings.file_search_paths(host)
                AppSettings.add_recent(host)
                self._recents = AppSettings.recents()
                self._mode, self._results, self._search_root = "search", files, root
                self._sort = ("mtime", True)
                self._apply_mode()
                self._render()
                more = f" (mostrati i primi {MAX_RESULTS}: restringi il filtro)" if truncated else ""
                self._status(f"{len(files)} file trovati in {root} su {host} in {elapsed:.1f} s"
                             f"{more}  -  doppio clic: apri  -  "
                             f"\"Vai alla cartella\" per sfogliare quella del file")
            return done

        self._run_async(work)

    def _back_to_folder(self) -> None:
        self._mode, self._results = "browse", []
        self._sort = ("name", False)
        self._apply_mode()
        self._render()
        self._status(self._folder)

    def _goto_result_folder(self) -> None:
        f = self._selected()
        if self._mode == "search" and f is not None:
            self._sort = ("name", False)
            self._navigate(f.folder, select_name=f.name)

    # ------------------------------------------------------------------
    # Files (Tk thread)

    def _open(self, f: Optional[RemoteFile] = None) -> None:
        f = f or self._selected_file()
        if f is None:
            return
        if f.size > BIG_FILE and not self._confirm_big(f):
            return
        # Unique folder per open: two files with the same name (e.g. two
        # app.log from different folders) never overwrite each other.
        local = OPEN_DIR / self._current_host / f"{time.time_ns():x}" / f.name
        self._download_to(f, local, open_after=True)

    def _download(self) -> None:
        from tkinter import filedialog
        f = self._selected_file()
        if f is None:
            return
        target = filedialog.asksaveasfilename(
            parent=self._top, title=f"Scarica {f.name}",
            initialdir=str(downloads_dir()), initialfile=f.name)
        if target:
            self._download_to(f, Path(target), open_after=False)

    def _confirm_big(self, f: RemoteFile) -> bool:
        from tkinter import messagebox
        return messagebox.askyesno(
            "Cerca file", f"{f.name} è di {format_size(f.size)}. Scaricarlo per aprirlo?",
            parent=self._top)

    def _download_to(self, f: RemoteFile, local: Path, open_after: bool) -> None:
        self._status(f"Scarico {f.name}...")
        last = [0.0]

        def progress(done, total):
            now = time.monotonic()
            if now - last[0] > 0.3 and total:
                last[0] = now
                pct = done * 100 // total
                self._ui.run_on_ui(lambda: self._status(
                    f"Scarico {f.name}: {pct}% di {format_size(total)}"))

        def work(session):
            session.download(f.path, local, progress)

            def done():
                if open_after:
                    if self._open_local(local, self._top):
                        self._status(f"Aperto {f.path} (copia temporanea, eliminata alla "
                                     f"chiusura dell'editor)")
                    else:
                        self._status(f"{f.name} non aperto: nessun programma scelto")
                else:
                    self._status(f"Salvato in {local}")
            return done
        self._run_async(work)

    @staticmethod
    def _open_local(path: Path, parent=None) -> bool:
        """Text (logs, .out, .log.1, configs...) in the chosen text editor
        (asked first if none is saved), deleting the temporary copy once it
        is closed; archives and documents with their associated program
        (cleaned up by purge_open_dir). False if the user cancelled the
        choice of the editor (the copy is removed at once)."""
        if path.suffix.lower() in BINARY_EXTENSIONS:
            try:
                os.startfile(str(path))
                return True
            except OSError:
                pass
        exe = text_editor.resolve_editor(parent, path.name)
        if exe is None:
            _remove_copy(path)              # "Apri con" cancelled
            return False
        proc = text_editor.launch(exe, path)
        if proc is None:                    # fell back to the association
            return True
        started = time.monotonic()

        def cleanup_when_closed():
            proc.wait()
            if time.monotonic() - started >= EDITOR_HANDOFF_SECONDS:
                _remove_copy(path)

        threading.Thread(target=cleanup_when_closed, daemon=True,
                         name="OpenCleanup").start()
        return True

    def _copy_path(self) -> str:
        sel = self._tree.selection()
        path = self._folder if sel and sel[0] == UP_IID else None
        f = self._selected()
        path = f.path if f is not None else path
        if path:
            self._top.clipboard_clear()
            self._top.clipboard_append(path)
            self._status(f"Percorso copiato: {path}")
        return "break"
