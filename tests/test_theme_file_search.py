#!/usr/bin/env python3
"""
Tests for the PROD console theme (console_themes + launcher preamble) and
the "Cerca file" window (remote_files helpers, FileSearchDialog with a fake
RemoteSession, Ctrl+F in the host popup).

No network: the remote side is faked. The generated `find` script is run
for real with Git's sh.exe when available (skipped otherwise).

Run:  py tests/test_theme_file_search.py
"""

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from ssh_connection.config.app_settings import MAX_FILE_PATHS, AppSettings
from ssh_connection.ssh import console_themes, remote_files
from ssh_connection.ssh.remote_files import (RemoteError, RemoteFile, RemoteSession,
                                             find_command, glob_for, parse_find_output,
                                             shell_path)
from ssh_connection.ssh.ssh_launcher import SshLauncher

TMP = Path(tempfile.mkdtemp(prefix="sshcm-test-"))
GIT_SH = Path(r"C:\Program Files\Git\usr\bin\sh.exe")

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  OK  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


# ----------------------------------------------------------------------

def test_console_themes():
    print("\n[1] Console colour schemes")
    seq = console_themes.osc_sequences("Ubuntu-ColorScheme")
    check("Ubuntu sets the background", "\x1b]11;rgb:30/0a/24\x07" in seq, repr(seq[:80]))
    check("Ubuntu sets the 16 palette entries", seq.count("\x1b]4;") == 16)
    check("Ubuntu palette red = C21A23", "\x1b]4;1;rgb:c2/1a/23\x07" in seq)
    check("'Nessuno' -> no sequences", console_themes.osc_sequences("Nessuno") is None)
    check("unknown scheme -> no sequences", console_themes.osc_sequences("Nope") is None)

    wt = TMP / "wt_settings.json"
    wt.write_text('{\n  // comment\n  "schemes": [{"name": "Mio", "background": "#102030",'
                  ' "foreground": "bad"}]\n}', encoding="utf-8")
    real = console_themes._wt_settings_path
    console_themes._wt_settings_path = lambda: wt
    try:
        names = console_themes.scheme_names()
        check("'Nessuno' listed first", names[0] == "Nessuno", str(names))
        check("Windows Terminal schemes offered", "Mio" in names and "Ubuntu-ColorScheme" in names,
              str(names))
        mine = console_themes.osc_sequences("Mio")
        check("invalid colours skipped", mine == "\x1b]11;rgb:10/20/30\x07", repr(mine))
    finally:
        console_themes._wt_settings_path = real


def test_console_preamble():
    print("\n[2] Launcher console preamble")
    envs = {"stlit1pf01": "PROD", "stlit1tf01": "TEST", "o'dd": "PROD"}
    real = SshLauncher.environment_of
    SshLauncher.environment_of = staticmethod(lambda n: envs.get(n))
    try:
        AppSettings.update(prod_console_theme="Ubuntu-ColorScheme")
        prod = SshLauncher._console_preamble("stlit1pf01")
        test = SshLauncher._console_preamble("stlit1tf01")
        check("PROD title", "WindowTitle='[PROD] stlit1pf01'" in prod, prod[:80])
        check("PROD applies the scheme", "]11;rgb:30/0a/24!" in prod)
        check("PROD banner", "PRODUZIONE - stlit1pf01" in prod)
        check("no control characters on the command line",
              not any(ord(c) < 32 for c in prod))
        check("TEST: title only", test == "$Host.UI.RawUI.WindowTitle='[TEST] stlit1tf01'; ", test)
        check("quotes escaped", "'[PROD] o''dd'" in SshLauncher._console_preamble("o'dd"))

        # PowerShell really turns the placeholders back into ESC/BEL.
        out = subprocess.run(["powershell", "-NoProfile", "-Command", prod],
                             capture_output=True, text=True, timeout=60,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        check("PowerShell emits the OSC sequences", "\x1b]11;rgb:30/0a/24\x07" in out
              and "\x1b[2J" in out and "|" not in out.split("PRODUZIONE")[0], repr(out[:120]))

        AppSettings.update(prod_console_theme="Nessuno")
        plain = SshLauncher._console_preamble("stlit1pf01")
        check("'Nessuno': banner but no scheme", "]11;" not in plain and "PRODUZIONE" in plain)
        AppSettings.update(prod_console_theme="Ubuntu-ColorScheme")
    finally:
        SshLauncher.environment_of = real
    check("hidden launches keep no preamble",
          "if not hidden:" in Path(sys.modules[SshLauncher.__module__].__file__).read_text(encoding="utf-8"))


def test_file_path_history():
    print("\n[3] Folder history per host")
    AppSettings.update(file_search_paths={})
    AppSettings.add_file_search_path("h1", "/var/log")
    AppSettings.add_file_search_path("h1", " /opt/app/logs ")
    AppSettings.add_file_search_path("h1", "/var/log")
    AppSettings.add_file_search_path("h2", "~")
    check("most recent first, no duplicates",
          AppSettings.file_search_paths("h1") == ["/var/log", "/opt/app/logs"],
          str(AppSettings.file_search_paths("h1")))
    check("per host", AppSettings.file_search_paths("h2") == ["~"])
    for i in range(MAX_FILE_PATHS + 5):
        AppSettings.add_file_search_path("h3", f"/p{i}")
    check("history capped", len(AppSettings.file_search_paths("h3")) == MAX_FILE_PATHS)
    AppSettings.update(file_search_paths={"x": "notalist", 3: ["/a"], "y": ["", "/b", 7]})
    check("garbage sanitized", AppSettings.get("file_search_paths") == {"y": ["/b"]},
          str(AppSettings.get("file_search_paths")))


def test_remote_helpers():
    print("\n[4] remote_files helpers")
    check("plain text = contains", glob_for("app") == "*app*")
    check("wildcards kept", glob_for("*.log") == "*.log")
    check("empty = everything", glob_for("  ") == "*")
    check("~ expands", shell_path("~") == '"$HOME"' and shell_path("~/a b") == "\"$HOME\"/'a b'")
    check("paths quoted", shell_path("/x/it's") == "'/x/it'\"'\"'s'")
    files = parse_find_output("1700000000.5\t42\t/a/b.log\ngarbage\nx\t1\t/c\n0\t0\t/d/e f.txt\n")
    check("output parsed, bad lines skipped",
          [(f.path, f.size) for f in files] == [("/a/b.log", 42), ("/d/e f.txt", 0)], str(files))
    check("name / folder", files[1].name == "e f.txt" and files[1].folder == "/d")
    cmd = find_command("/app/nets/batchcommon", "x")
    check("find follows symbolic links (-L everywhere)",
          cmd.count("find -L ") == 3 and cmd.count("find ") == 3, cmd)
    check("login hosts unsupported", not remote_files.is_supported_host("login_test")
          and remote_files.is_supported_host("stlit1tf01"))
    try:
        RemoteSession("login_prod").connect()
        check("jump host refused before any network", False)
    except RemoteError as e:
        check("jump host refused before any network", "token" in str(e), str(e))

    if not GIT_SH.exists():
        print("  --  sh.exe not found, find script not executed")
        return
    root = TMP / "remote dir's"
    (root / "sub").mkdir(parents=True)
    (root / "app.log").write_text("ERROR boom", encoding="utf-8")
    (root / "sub" / "App-2.LOG").write_text("ok", encoding="utf-8")
    (root / "other.txt").write_text("x", encoding="utf-8")
    posix = "/" + str(root).replace(":", "").replace("\\", "/")
    posix = posix[0] + posix[1].lower() + posix[2:]

    def run(*args, **kw):
        out = subprocess.run([str(GIT_SH), "-c", find_command(posix, *args, **kw)],
                             capture_output=True, text=True, timeout=60).stdout
        return out, sorted(f.name for f in parse_find_output(out))

    check("recursive, case-insensitive", run("log")[1] == ["App-2.LOG", "app.log"], str(run("log")))
    check("not recursive", run("log", recursive=False)[1] == ["app.log"])
    # Git for Windows ships GNU grep 3.0, which aborts (signal 6) on -i -F
    # together; real servers are fine (verified on grep 2.20). Skip there.
    probe = subprocess.run([str(GIT_SH), "-c", "grep -qiF -e error " + shlex.quote(posix + "/app.log")],
                           capture_output=True, timeout=30, cwd=str(TMP))  # its crash dump lands in TMP
    if probe.returncode == 0:
        check("content filter", run("", "error")[1] == ["app.log"])
    else:
        print(f"  --  local grep -qiF broken (rc={probe.returncode}), content filter not executed")
    check("glob", run("*.txt")[1] == ["other.txt"])
    out, names = run("log")
    check("size and mtime from -printf", any(f.size == 10 and f.mtime > 0
                                             for f in parse_find_output(out)), out)
    missing = subprocess.run([str(GIT_SH), "-c", find_command(posix + "/nope", "x")],
                             capture_output=True, text=True, timeout=60).stdout
    check("missing folder reported", missing.strip() == remote_files._NODIR, missing)


def _d(path):
    return RemoteFile(path, 4096, 50.0, is_dir=True)


def _f(path, size=10, mtime=100.0, link=False):
    return RemoteFile(path, size, mtime, is_link=link)


# Fake remote tree: logical path -> entries
FAKE_FS = {
    "/home/u": [_d("/home/u/logs"), _f("/home/u/notes.txt")],
    "/home/u/logs": [_f("/home/u/logs/app.log", 2048, 200.0), _f("/home/u/logs/old.log"),
                     _d("/home/u/logs/archive")],
    "/home/u/logs/archive": [],
    "/var/log": [_f("/var/log/messages", 5000, 300.0), _d("/var/log/audit"),
                 _f("/var/log/app.log", 20, 150.0, link=True)],
    "/var/log/audit": [],
    "/var": [_d("/var/log")],
    "/": [_d("/home"), _d("/var")],
}


class FakeSession:
    instances = []
    delay = 0.0

    def __init__(self, host):
        self.host = host
        self.closed = False
        self.calls = []
        FakeSession.instances.append(self)

    def connect(self, status=lambda s: None):
        status("fake connect")

    def listdir(self, folder):
        self.calls.append(("listdir", folder))
        if FakeSession.delay:
            time.sleep(FakeSession.delay)
        path = "/home/u" if folder in ("~", "") else folder.rstrip("/") or "/"
        if path not in FAKE_FS:
            raise RemoteError(f"La cartella {path} non esiste su {self.host}.")
        entries = sorted(FAKE_FS[path], key=lambda f: (not f.is_dir, f.name.lower()))
        return path, entries

    def find(self, folder, name, text="", recursive=True):
        self.calls.append(("find", folder, name, text, recursive))
        if folder == "/missing":
            raise RemoteError("La cartella /missing non esiste")
        return [_f("/home/u/logs/old.log", 10, 100.0),
                _f("/home/u/logs/app.log", 2048, 200.0)], False

    def download(self, path, local, progress=lambda d, t: None):
        self.calls.append(("download", path))
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text("content", encoding="utf-8")

    def cancel(self):
        pass

    def close(self):
        self.closed = True


def test_file_search_dialog():
    print("\n[5] Cerca file window: host panel, browsing, search, Ctrl+F")
    from ssh_connection.gui import file_search_dialog as fsd
    from ssh_connection.gui import theme
    from ssh_connection.gui.search_dialog import SearchPopup

    hosts = [("TEST", ["login_test", "stlit1tf01"]), ("PROD", ["login_prod", "stlit1pf01"])]
    opened = []
    popup = SearchPopup(host_provider=lambda: hosts, on_select=lambda h: None,
                        on_file_search=opened.append)
    popup.start()
    done = threading.Event()
    out = {}

    def on_ui(fn):
        def wrapped():
            try:
                fn()
            finally:
                done.set()
        done.clear()
        popup.run_on_ui(wrapped)
        return done.wait(10)

    def wait_idle(dlg, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            on_ui(lambda: out.update(busy=dlg._busy))
            if not out["busy"]:
                return True
            time.sleep(0.05)
        return False

    def wait_for(cond, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            on_ui(lambda: out.update(_c=bool(cond())))
            if out["_c"]:
                return True
            time.sleep(0.05)
        return False

    ready = {"value": True}
    real = (fsd.RemoteSession, fsd.FileSearchDialog._open_local, fsd.OPEN_DIR, fsd.route_ready)
    fsd.RemoteSession = FakeSession
    local_opened = []
    fsd.FileSearchDialog._open_local = staticmethod(local_opened.append)
    fsd.OPEN_DIR = TMP / "open"
    fsd.route_ready = lambda host: ready["value"]
    try:
        AppSettings.update(file_search_paths={"stlit1pf01": ["/var/log", "/opt"]}, recents=[],
                           favorites=["stlit1tf01"], search_env="TEST")
        dlg = fsd.FileSearchDialog(ui_host=popup, host_provider=lambda: hosts)

        def listing():
            return [(r["type"], r.get("label") or r["host"]) for r in dlg._host_rows]

        def selected_host_row():
            sel = dlg._host_list.curselection()
            return dlg._host_rows[sel[0]]["host"] if sel else None

        def tree_rows():
            t = dlg._tree
            return [t.item(i, "text").strip() for i in t.get_children()]

        def selected_name():
            sel = dlg._tree.selection()
            return dlg._tree.item(sel[0], "text").strip() if sel else None

        def host_index(name):
            idx = next((i for i, r in enumerate(dlg._host_rows)
                        if r["type"] == "host" and r["host"] == name), None)
            if idx is None:
                print("   host rows:", dlg._host_rows)
            return idx

        def select(name):
            t = dlg._tree
            iid = next(i for i in t.get_children() if t.item(i, "text").strip() == name)
            t.selection_set(iid)

        # --- opening: host panel + automatic listing of the last folder ----
        dlg.show("stlit1pf01")
        wait_for(lambda: dlg._folder)          # AUTO_LIST_DELAY_MS + probe + listing
        on_ui(lambda: out.update(rows=listing(), host=dlg._current_host, sel=selected_host_row(),
                                 env_filter=dlg._var_env.get(), title=dlg._host_title.cget("text"),
                                 env=dlg._env_label.cget("text"), path=dlg._var_path.get(),
                                 folder=dlg._folder, tree=tree_rows(),
                                 hist=list(dlg._path_combo["values"]),
                                 cols=tuple(dlg._tree.cget("displaycolumns"))))
        check("list has Preferiti, TEST, PROD sections without jump hosts",
              out["rows"] == [("sep", "★ Preferiti"), ("host", "stlit1tf01"), ("sep", "TEST"),
                              ("host", "stlit1tf01"), ("sep", "PROD"), ("host", "stlit1pf01")],
              str(out["rows"]))
        check("env filter widened to Tutti so the requested PROD host is visible",
              out["env_filter"] == "Tutti", out["env_filter"])
        check("requested host selected and highlighted",
              out["host"] == out["sel"] == out["title"] == "stlit1pf01", str(out))
        check("environment shown", out["env"] == "PROD")
        check("last folder listed automatically when the tunnel is up",
              out["folder"] == out["path"] == "/var/log", str(out))
        check("'..' first, then folders, then files (links marked)",
              out["tree"] == ["..", "audit", "app.log  →", "messages"], str(out["tree"]))
        check("browsing hides the Cartella column", "folder" not in out["cols"], str(out["cols"]))
        sess = FakeSession.instances[-1]

        # --- local filter: no server round trip -----------------------------
        n_calls = len(sess.calls)
        on_ui(lambda: dlg._var_name.set("mess"))
        on_ui(lambda: out.update(tree=tree_rows()))
        check("typing in Nome filters the folder locally",
              out["tree"] == ["..", "messages"] and len(sess.calls) == n_calls, str(out["tree"]))
        on_ui(lambda: dlg._var_name.set(""))

        # --- navigation: enter, up, back, typed path ------------------------
        on_ui(lambda: (select("audit"), dlg._activate()))
        wait_idle(dlg)
        on_ui(lambda: out.update(folder=dlg._folder, back=list(dlg._back), tree=tree_rows()))
        check("double click on a folder enters it", out["folder"] == "/var/log/audit"
              and out["tree"] == [".."] and out["back"] == ["/var/log"], str(out))
        on_ui(dlg._go_up)
        wait_idle(dlg)
        on_ui(lambda: out.update(folder=dlg._folder, sel=selected_name()))
        check("up returns to the parent with the folder selected",
              out["folder"] == "/var/log" and out["sel"] == "audit", str(out))
        on_ui(dlg._go_back)
        wait_idle(dlg)
        on_ui(lambda: out.update(folder=dlg._folder))
        check("back returns to the previous folder", out["folder"] == "/var/log/audit", str(out))
        on_ui(lambda: (dlg._var_path.set("/home/u/logs"), dlg._path_entered()))
        wait_idle(dlg)
        on_ui(lambda: out.update(folder=dlg._folder, tree=tree_rows(), status=dlg._var_status.get()))
        check("typed path is listed", out["folder"] == "/home/u/logs"
              and out["tree"] == ["..", "archive", "app.log", "old.log"], str(out))
        check("status counts folders and files", "1 cartelle, 2 file" in out["status"], out["status"])
        check("visited folders go to the history",
              AppSettings.file_search_paths("stlit1pf01")[:2] == ["/home/u/logs", "/var/log/audit"],
              str(AppSettings.file_search_paths("stlit1pf01")))
        check("browsing adds the host to Recenti", AppSettings.recents() == ["stlit1pf01"],
              str(AppSettings.recents()))

        # --- open a file from the listing ----------------------------------
        on_ui(lambda: (select("app.log"), dlg._activate()))
        wait_idle(dlg)
        check("double click on a file downloads it to a unique temp folder and opens it",
              len(local_opened) == 1 and local_opened[0].name == "app.log"
              and local_opened[0].parent.parent == fsd.OPEN_DIR / "stlit1pf01"
              and local_opened[0].exists()
              and sess.calls[-1] == ("download", "/home/u/logs/app.log"), str(sess.calls[-3:]))

        # --- search from the current folder, then "Vai alla cartella" ------
        on_ui(lambda: (dlg._var_name.set("log"), dlg._var_text.set("ERR"), dlg._search_or_stop()))
        check("search completes", wait_idle(dlg))
        on_ui(lambda: out.update(tree=tree_rows(), status=dlg._var_status.get(),
                                 banner=dlg._banner.winfo_ismapped(),
                                 cols=tuple(dlg._tree.cget("displaycolumns")),
                                 first=[dlg._tree.item(i, "values")
                                        for i in dlg._tree.get_children()][0]))
        check("search starts from the current folder",
              sess.calls[-1] == ("find", "/home/u/logs", "log", "ERR", True), str(sess.calls[-1]))
        check("results newest first, no '..'", out["tree"] == ["app.log", "old.log"], str(out["tree"]))
        check("results show their folder and the banner",
              "folder" in out["cols"] and out["first"][0] == "/home/u/logs" and out["banner"],
              str(out))
        check("size formatted", out["first"][1] == "2.0 KB", str(out["first"]))
        check("status reports the count", out["status"].startswith("2 file trovati"), out["status"])
        on_ui(lambda: (dlg._var_name.set(""), dlg._var_text.set(""),
                       select("old.log"), dlg._goto_result_folder()))
        wait_idle(dlg)
        on_ui(lambda: out.update(folder=dlg._folder, sel=selected_name(), mode=dlg._mode,
                                 banner=dlg._banner.winfo_ismapped()))
        check("'Vai alla cartella' browses to the result's folder with the file selected",
              out == {**out, "folder": "/home/u/logs", "sel": "old.log", "mode": "browse",
                      "banner": 0}, str(out))

        # --- errors ------------------------------------------------------------
        on_ui(lambda: (dlg._var_path.set("/nope"), dlg._path_entered()))
        wait_idle(dlg)
        on_ui(lambda: out.update(status=dlg._var_status.get(), folder=dlg._folder,
                                 color=dlg._status_label.cget("fg")))
        check("missing folder: red message, stays where it was",
              "non esiste" in out["status"] and out["color"] == theme.color("error")
              and out["folder"] == "/home/u/logs", str(out))

        # --- dark theme: switching repaints the open window -------------------
        def colors():
            sep = next(i for i, r in enumerate(dlg._host_rows) if r["type"] == "sep")
            out.update(status=dlg._status_label.cget("fg"), bg=dlg._top.cget("bg"),
                       sep=dlg._host_list.itemcget(sep, "bg"))
        on_ui(lambda: (theme.use(True), colors()))
        dark = theme.PALETTES["dark"]
        check("dark theme repaints window, error status and separator rows",
              (out["bg"], out["status"], out["sep"]) == (dark["bg"], dark["error"], dark["sep_bg"]),
              str(out))
        on_ui(lambda: (theme.use(False), colors()))
        light = theme.PALETTES["light"]
        check("back to the light theme",
              (out["bg"], out["status"], out["sep"]) == (light["bg"], light["error"], light["sep_bg"]),
              str(out))

        # --- host panel: typing, env filter, arrows, Ctrl+D -----------------
        ready["value"] = False             # tunnel down: nothing may connect by itself
        on_ui(lambda: dlg._var_host_filter.set("TF01"))
        wait_for(lambda: "tunnel" in dlg._var_status.get())
        on_ui(lambda: out.update(rows=listing(), host=dlg._current_host, folder=dlg._folder,
                                 status=dlg._var_status.get(), path=dlg._var_path.get()))
        new_sess = FakeSession.instances[-1]
        check("typing filters the host list", out["rows"] == [("sep", "TEST"), ("host", "stlit1tf01")],
              str(out["rows"]))
        check("best match becomes the host", out["host"] == "stlit1tf01", out["host"])
        check("changing host closes the old connection", sess.closed)
        check("tunnel down: no automatic listing, the status says how to connect",
              out["folder"] == "" and new_sess.calls == [] and "tunnel" in out["status"]
              and out["path"] == "~", str(out))
        on_ui(dlg._host_confirmed)
        wait_idle(dlg)
        on_ui(lambda: out.update(folder=dlg._folder))
        check("Enter on the host lists its folder (explicit connect)",
              out["folder"] == "/home/u" and new_sess.calls == [("listdir", "~")], str(new_sess.calls))
        ready["value"] = True

        on_ui(lambda: dlg._var_host_filter.set("zzz"))
        on_ui(lambda: out.update(host=dlg._current_host, status=dlg._var_status.get()))
        check("no match keeps the host and says so", out["host"] == "stlit1tf01"
              and "Nessun host" in out["status"], str(out))
        on_ui(lambda: (dlg._var_host_filter.set(""), dlg._var_env.set("PROD"), dlg._on_env_changed()))
        on_ui(lambda: out.update(rows=listing()))
        check("PROD filter shows only PROD hosts",
              {r[1] for r in out["rows"] if r[0] == "host"} == {"stlit1pf01"}
              and ("sep", "TEST") not in out["rows"], str(out["rows"]))
        on_ui(lambda: (dlg._var_env.set("Tutti"), dlg._on_env_changed()))
        on_ui(lambda: (dlg._move_host(1), out.update(host=dlg._current_host, sel=selected_host_row())))
        check("arrow moves to the next host row", out["sel"] == out["host"] and out["host"], str(out))
        on_ui(lambda: (dlg._select_host_row(next(i for i, r in enumerate(dlg._host_rows)
                                                 if r["host"] == "stlit1pf01")),
                       dlg._toggle_favorite()))
        check("Ctrl+D adds to favorites", AppSettings.favorites() == ["stlit1tf01", "stlit1pf01"],
              str(AppSettings.favorites()))
        on_ui(lambda: out.update(rows=listing()))
        check("favorites section updated", out["rows"][:3] == [
            ("sep", "★ Preferiti"), ("host", "stlit1tf01"), ("host", "stlit1pf01")], str(out["rows"]))

        # --- switching host while a listing is still running ----------------
        wait_for(lambda: dlg._folder)          # stlit1pf01 auto-listed again
        wait_idle(dlg)
        FakeSession.delay = 1.0
        on_ui(dlg._refresh_folder)                        # slow listing on stlit1pf01
        time.sleep(0.2)
        on_ui(lambda: out.update(busy_before=dlg._busy))
        FakeSession.delay = 0.0
        on_ui(lambda: dlg._select_host_row(host_index("stlit1tf01")))
        on_ui(lambda: out.update(busy_after=dlg._busy, host=dlg._current_host))
        time.sleep(1.2)                                   # old listing ends
        wait_for(lambda: dlg._folder)                     # new host auto-lists
        on_ui(lambda: out.update(folder=dlg._folder, host2=dlg._current_host))
        check("another host can be picked during a slow operation",
              out["busy_before"] and not out["busy_after"] and out["host"] == "stlit1tf01", str(out))
        check("the late result of the old host is discarded",
              out["host2"] == "stlit1tf01" and out["folder"] == "/home/u", str(out))
        on_ui(dlg._hide)

        # --- Ctrl+F in the host popup -------------------------------------
        popup.show("Tutti")
        time.sleep(0.4)
        on_ui(lambda: (popup._var_filter.set("stlit1pf"), popup._file_search()))
        check("Ctrl+F opens file search on the selected host", opened == ["stlit1pf01"], str(opened))
        on_ui(lambda: out.update(status=popup._var_status.get()))
        check("popup hints Ctrl+F", "Ctrl+F" in out["status"])
    finally:
        FakeSession.delay = 0.0
        (fsd.RemoteSession, fsd.FileSearchDialog._open_local, fsd.OPEN_DIR, fsd.route_ready) = real
        popup.stop()


class _Attr:
    def __init__(self, name, mode, size=0, mtime=0):
        self.filename, self.st_mode, self.st_size, self.st_mtime = name, mode, size, mtime


class _FakeSftp:
    """listdir_attr returns lstat-like entries; stat follows links."""

    def __init__(self):
        import stat as st
        self.dir_mode, self.file_mode, self.link_mode = (st.S_IFDIR | 0o755, st.S_IFREG | 0o644,
                                                         st.S_IFLNK | 0o777)
        self.stat_calls = []

    def normalize(self, path):
        return "/home/u"

    def listdir_attr(self, path):
        if path == "/secret":
            raise PermissionError(13, "Permission denied")
        if path == "/app/nets":
            return [_Attr("batchcommon", self.link_mode, 19, 1.0),
                    _Attr("broken", self.link_mode, 5, 2.0),
                    _Attr("z.txt", self.file_mode, 7, 3.0),
                    _Attr("Adir", self.dir_mode, 4096, 4.0)]
        raise FileNotFoundError(2, "No such file")

    def stat(self, path):
        self.stat_calls.append(path)
        if path == "/app/nets/batchcommon":
            return _Attr("batchcommon", self.dir_mode, 4096, 9.0)
        raise FileNotFoundError(2, "dangling")


def test_listdir():
    print("\n[4b] RemoteSession.listdir / path helpers")
    from ssh_connection.ssh.remote_files import join_path, parent_path
    check("join/parent", join_path("/a/b", "c") == "/a/b/c" and join_path("/", "c") == "/c"
          and join_path("/a/b", "..") == "/a" and parent_path("/a") == "/"
          and parent_path("/") == "/" and parent_path("/a/b/") == "/a")
    s = RemoteSession("stlit1tf01")
    s._sftp = _FakeSftp()
    check("~ resolves to the remote home", s.resolve_folder("~") == "/home/u")
    check("~/x and relative paths are anchored to home",
          s.resolve_folder("~/logs") == "/home/u/logs" and s.resolve_folder("logs/a") == "/home/u/logs/a")
    check("'..' collapsed textually, links not resolved",
          s.resolve_folder("/app/nets/batchcommon/files/..") == "/app/nets/batchcommon")
    path, entries = s.listdir("/app/nets/")
    summary = [(e.name, e.is_dir, e.is_link) for e in entries]
    check("folders first (links to folders count as folders), broken link as file",
          summary == [("Adir", True, False), ("batchcommon", True, True),
                      ("broken", False, True), ("z.txt", False, False)], str(summary))
    check("logical paths kept", path == "/app/nets"
          and entries[1].path == "/app/nets/batchcommon" and entries[1].mtime == 9.0, str(entries[1]))
    check("stat only for links", s._sftp.stat_calls == ["/app/nets/batchcommon", "/app/nets/broken"],
          str(s._sftp.stat_calls))
    for folder, word in (("/secret", "Permesso negato"), ("/nope", "non esiste")):
        try:
            s.listdir(folder)
            check(f"{folder}: error", False)
        except RemoteError as e:
            check(f"{folder}: '{word}'", word in str(e), str(e))
    s.close()
    try:
        s.connect()
        check("a closed session never reconnects", False)
    except RemoteError as e:
        check("a closed session never reconnects", "annullata" in str(e), str(e))



def test_formatting():
    print("\n[6] Formatting / opening")
    from ssh_connection.gui import file_search_dialog as fsd
    check("bytes", fsd.format_size(512) == "512 B")
    check("megabytes", fsd.format_size(5 * 1024 * 1024) == "5.0 MB")
    real_dir, real_handoff = fsd.OPEN_DIR, fsd.NOTEPAD_HANDOFF_SECONDS
    fsd.OPEN_DIR = TMP / "open2"

    def copy(host, name):
        p = fsd.OPEN_DIR / host / f"{time.time_ns():x}" / name
        p.parent.mkdir(parents=True)
        p.write_text("x", encoding="utf-8")
        return p

    class FakeNotepad:
        def __init__(self, args):
            launched.append(args)

        def wait(self):
            time.sleep(0.3)

    launched, started = [], []
    real_popen, real_start = fsd.subprocess.Popen, fsd.os.startfile
    fsd.subprocess.Popen, fsd.os.startfile = FakeNotepad, started.append
    try:
        fsd.NOTEPAD_HANDOFF_SECONDS = 0.1
        log = copy("h", "app.log.1")
        fsd.FileSearchDialog._open_local(log)
        time.sleep(1)
        check("text files open in Notepad", launched == [["notepad.exe", str(log)]], str(launched))
        check("copy deleted when Notepad closes", not log.parent.exists()
              and not (fsd.OPEN_DIR / "h").exists())

        fsd.NOTEPAD_HANDOFF_SECONDS = 60
        handed = copy("h", "b.log")
        fsd.FileSearchDialog._open_local(handed)
        time.sleep(1)
        check("copy kept when Notepad hands off to another window", handed.exists())

        gz = copy("h", "app.log.gz")
        fsd.FileSearchDialog._open_local(gz)
        check("archives open with their program", started == [str(gz)], str(started))
        fsd.purge_open_dir(3600)
        check("purge with max age keeps recent copies", handed.exists() and gz.exists())
        fsd.purge_open_dir()
        check("purge removes everything", not any(fsd.OPEN_DIR.iterdir()), str(list(fsd.OPEN_DIR.iterdir())))
    finally:
        fsd.subprocess.Popen, fsd.os.startfile = real_popen, real_start
        fsd.OPEN_DIR, fsd.NOTEPAD_HANDOFF_SECONDS = real_dir, real_handoff


def test_host_order():
    print("\n[7] Hosts in alphabetical order")
    from ssh_connection.ssh.ssh_config_parser import SshConfigParser
    check("jump host first, then alphabetical, no duplicates",
          SshConfigParser.sort_hosts(["stl02", "login_test", "Axe01", "gw01", "stl02", "bkn01"])
          == ["login_test", "Axe01", "bkn01", "gw01", "stl02"])
    cfg = TMP / "ssh_config"
    cfg.write_text("# TEST\nHost zeta\nHost login_test\nHost alfa\nHost zeta\n"
                   "# PROD\nHost prod_b\nHost login_prod\nHost prod_a\n", encoding="utf-8")
    check("parser returns sorted sections", SshConfigParser.parse_ssh_config(cfg)
          == {"TEST": ["login_test", "alfa", "zeta"], "PROD": ["login_prod", "prod_a", "prod_b"]},
          str(SshConfigParser.parse_ssh_config(cfg)))


def main():
    AppSettings.path = TMP / "prefs.json"
    try:
        test_host_order()
        test_console_themes()
        test_console_preamble()
        test_file_path_history()
        test_remote_helpers()
        test_listdir()
        test_file_search_dialog()
        test_formatting()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
