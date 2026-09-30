#!/usr/bin/env python3
"""
Tests for editing the configuration from the settings dialog:
- SshConfigDocument (~/.ssh/config): hosts model, add / edit / delete a
  host behind the jump host, jump host address, validation, untouched
  lines, CRLF / tabs / BOM kept, backup and "changed on disk" refusal;
- ConfigLoader.save_maven_credentials (settings.xml): only the text of the
  first <server>'s username / password changes;
- the settings dialog: host edits and credentials are written on Salva
  only, Annulla discards them.

Everything works on temporary files: the real ~/.ssh/config and
~/.m2/settings.xml are never touched.

Run:  py tests/test_config_editor.py
"""

import difflib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from ssh_connection.config.app_settings import AppSettings
from ssh_connection.config.config_loader import _MAVEN_TEMPLATE, ConfigLoader
from ssh_connection.ssh.ssh_config_editor import ConfigError, SshConfigDocument
from ssh_connection.ssh.ssh_config_parser import SshConfigParser

TMP = Path(tempfile.mkdtemp(prefix="sshcm-edit-"))
WIN_SSH = Path(r"C:\Windows\System32\OpenSSH\ssh.exe")

# Shaped like the real config: wildcard DB tunnels, tabs, comments, notes
# inside blocks, blank lines with a tab, a duplicated host.
SAMPLE = """# common
Host *
    ServerAliveInterval 60
\t
# DB - Test - Finance
Host *it1tf*
LocalForward 1524 fdb02x:1524

############################################
#                TEST                      #
############################################

Host login_test
    HostName 10.180.22.2
    LocalForward 2222 stlit1tf01:22
\tLocalForward 2223 gwit1te01:22



# Settlement - Finance - Test
Host stlit1tf01
    HostName localhost
    Port 2222
\tLocalForward 3050 localhost:3050
\t# HSM
\tLocalForward 9868 hsmgwloc:9868

# GW 01 - Ent - Test
Host gwit1te01
    HostName localhost
    Port 2223
\t# note at the end of the block

Host direct1
    HostName 10.1.2.3

############################################
#                PROD                      #
############################################

Host login_prod
    HostName 10.101.22.12
    LocalForward 3222 stlit1pf01:22
    LocalForward 3223 dup1pe01:22
    LocalForward 3224 dup1pe01:22

Host stlit1pf01
    HostName localhost
    Port 3222

Host dup1pe01
    HostName localhost
    Port 3223

Host dup1pe01
    HostName localhost
    Port 3224
"""

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  {'OK' if condition else 'FAIL'}  {name}  {'' if condition else detail}")


def raises(fn, needle=""):
    try:
        fn()
    except ConfigError as e:
        return needle.lower() in str(e).lower()
    return False


def is_subsequence(small, big):
    it = iter(big)
    return all(line in it for line in small)


def ssh_g(config: Path, host: str) -> dict:
    out = subprocess.run([str(WIN_SSH), "-G", "-F", str(config), host],
                         capture_output=True, text=True).stdout
    values = {}
    for line in out.splitlines():
        k, _, v = line.partition(" ")
        values.setdefault(k, []).append(v)
    return values


def test_model():
    print("\n[1] Hosts model")
    doc = SshConfigDocument(SAMPLE)
    check("unchanged text round-trips", doc.text() == SAMPLE)
    hosts = {h.name: h for h in doc.hosts()}
    check("wildcards and the global block are not hosts",
          not any("*" in n for n in hosts), str(list(hosts)))
    check("jump hosts", doc.jump_host("TEST") == "login_test" and doc.jump_host("PROD") == "login_prod")
    h = hosts["stlit1tf01"]
    check("tunnel host: destination, ports, description",
          (h.kind, h.env, h.dest, h.dest_port, h.port, h.description)
          == ("tunnel", "TEST", "stlit1tf01", 22, 2222, "Settlement - Finance - Test"), str(h))
    check("direct host", hosts["direct1"].kind == "direct" and hosts["direct1"].hostname == "10.1.2.3")
    check("jump host kind", hosts["login_prod"].kind == "jump" and hosts["login_prod"].env == "PROD")
    check("duplicated host flagged, not editable",
          hosts["dup1pe01"].duplicate and not hosts["dup1pe01"].editable)
    check("next free port after the jump host's tunnels (skips ports used anywhere)",
          doc.next_free_port("TEST") == 2224 and doc.next_free_port("PROD") == 3225,
          f"{doc.next_free_port('TEST')} {doc.next_free_port('PROD')}")


def test_add_edit_delete():
    print("\n[2] Add / edit / delete")
    doc = SshConfigDocument(SAMPLE)
    original = SAMPLE.splitlines()
    doc.add_host("TEST", "newit1te01", description="Nuovo - Ent - Test")
    lines = doc.lines
    check("add keeps every original line, in order", is_subsequence(original, lines))
    check("tunnel appended to the jump host with its indentation",
          "\tLocalForward 2224 newit1te01:22" in lines, "\n".join(lines[12:18]))
    i = lines.index("Host newit1te01")
    check("host block at the end of the TEST section, before the PROD header",
          lines[i - 1] == "# Nuovo - Ent - Test" and lines[i + 1:i + 3] ==
          ["    HostName localhost", "    Port 2224"] and lines[i + 3] == ""
          and lines[i + 4].startswith("####"), "\n".join(lines[i - 2:i + 6]))
    check("new host is in TEST for the parser",
          "newit1te01" in SshConfigParser.parse_ssh_config(_write(doc, "added"))["TEST"])
    check("added host reads back as a tunnel host",
          doc.host("newit1te01").kind == "tunnel" and doc.host("newit1te01").port == 2224)

    check("duplicate name refused", raises(lambda: doc.add_host("TEST", "STLIT1TF01"), "esiste già"))
    check("name with spaces refused", raises(lambda: doc.add_host("TEST", "a b"), "non valido"))
    check("wildcard refused", raises(lambda: doc.add_host("TEST", "a*"), "non valido"))
    check("used local port refused (also the wildcard DB tunnel)",
          raises(lambda: doc.add_host("TEST", "x1", local_port=1524), "*it1tf*"))
    check("description naming the other environment refused (would switch section)",
          raises(lambda: doc.add_host("TEST", "x2", description="copia di prod"), "PROD"))

    doc.update_host("gwit1te01", new_name="gwit1te09", dest_port=2022, local_port=2230,
                    description="GW 09")
    lines = doc.lines
    check("edit rewrites the jump host tunnel", "\tLocalForward 2230 gwit1te01:2022" in lines,
          str([l for l in lines if "2230" in l]))
    check("edit renames, moves the port and the description",
          "Host gwit1te09" in lines and "    Port 2230" in lines and "# GW 09" in lines
          and "# GW 01 - Ent - Test" not in lines)
    check("note inside the block kept", "\t# note at the end of the block" in lines)
    doc.update_host("direct1", hostname="10.9.9.9", port=2200)
    check("direct host: HostName changed, Port added",
          doc.host("direct1").hostname == "10.9.9.9" and doc.host("direct1").port == 2200)
    check("jump host cannot be renamed",
          raises(lambda: doc.update_host("login_test", new_name="login_x"), "jump host"))
    check("duplicated host cannot be edited",
          raises(lambda: doc.update_host("dup1pe01", dest="x"), "più volte"))

    before = list(doc.lines)
    doc.delete_host("stlit1tf01")
    lines = doc.lines
    check("delete removes the block, its description and its tunnel",
          "Host stlit1tf01" not in lines and "# Settlement - Finance - Test" not in lines
          and "    LocalForward 2222 stlit1tf01:22" not in lines
          and "\tLocalForward 9868 hsmgwloc:9868" not in lines)
    diff = list(difflib.ndiff(before, lines))
    removed = [d[2:] for d in diff if d.startswith("- ")]
    added = [d for d in diff if d.startswith("+ ")]
    check("delete leaves the rest alone (only the block, its tunnel and one blank line)",
          not added and sorted(removed) == sorted([
              "# Settlement - Finance - Test", "Host stlit1tf01", "    HostName localhost",
              "    Port 2222", "	LocalForward 3050 localhost:3050", "	# HSM",
              "	LocalForward 9868 hsmgwloc:9868", "    LocalForward 2222 stlit1tf01:22", ""]),
          str(removed))
    check("jump host cannot be deleted", raises(lambda: doc.delete_host("login_test"), "jump"))

    doc.set_jump_hostname("PROD", "10.0.0.9")
    check("jump host address", doc.host("login_prod").hostname == "10.0.0.9")

    if WIN_SSH.exists():
        cfg = _write(doc, "edited")
        g = ssh_g(cfg, "newit1te01")
        check("ssh -G: new host goes through the tunnel port",
              g.get("hostname") == ["localhost"] and g.get("port") == ["2224"], str(g.get("port")))
        g = ssh_g(cfg, "login_test")
        check("ssh -G: jump host has the new tunnels",
              "2224 [newit1te01]:22" in g.get("localforward", [])
              and "2230 [gwit1te01]:2022" in g.get("localforward", []),
              str(g.get("localforward")))
    else:
        print("  (skipped ssh -G checks: Windows OpenSSH not found)")


def _write(doc, name) -> Path:
    path = TMP / name
    path.write_bytes(doc.text().encode("utf-8"))
    return path


def test_file_format_and_save():
    print("\n[3] File format, backup, concurrent edit")
    path = TMP / "config_crlf"
    text = "\ufeff" + SAMPLE.replace("\n", "\r\n")
    path.write_bytes(text.encode("utf-8"))
    doc = SshConfigDocument.load(path)
    check("CRLF + BOM round-trip", doc.text() == text)
    doc.add_host("PROD", "newit1pe01")
    backup = doc.save()
    raw = path.read_bytes().decode("utf-8")
    check("saved file keeps BOM and CRLF only",
          raw.startswith("\ufeff") and "\n" not in raw.replace("\r\n", ""))
    check("backup holds the previous content", backup.read_bytes().decode("utf-8") == text)
    check("document clean after save", not doc.dirty)

    doc.add_host("PROD", "newit1pe02")
    path.write_bytes(path.read_bytes() + b"# edited by hand\r\n")
    check("refuses to overwrite a file changed on disk", raises(doc.save, "modificato"))
    check("the hand edit survives", path.read_bytes().endswith(b"# edited by hand\r\n"))

    missing = SshConfigDocument.load(TMP / "nope")
    check("missing file = empty document", missing.hosts() == [])
    check("adding without a jump host explains why",
          raises(lambda: missing.add_host("TEST", "a1"), "jump host"))


def test_credentials():
    print("\n[4] Credentials in settings.xml")
    p = TMP / "m2" / "settings.xml"
    check("missing file is created from the template",
          ConfigLoader.save_maven_credentials("DOM\\u.ser", "p&ss<>", p))
    c = ConfigLoader._load_maven_credentials(p)
    check("values escaped and read back",
          c is not None and (c.username, c.password) == ("DOM\\u.ser", "p&ss<>"), str(c))
    check("raw read keeps the domain", ConfigLoader.read_maven_credentials_raw(p) == ("DOM\\u.ser", "p&ss<>"))

    maven = TMP / "maven.xml"
    original = ("<?xml version=\"1.0\"?>\n<settings xmlns=\"http://maven.apache.org/SETTINGS/1.1.0\">\n"
                "  <!-- <server><username>no</username></server> -->\n"
                "  <mirrors><mirror><id>m</id></mirror></mirrors>\n"
                "  <servers>\n    <server>\n      <id>nexus</id>\n      <password>old</password>\n"
                "    </server>\n    <server><id>two</id><username>x</username><password>y</password>"
                "</server>\n  </servers>\n</settings>\n")
    maven.write_text(original, encoding="utf-8")
    ConfigLoader.save_maven_credentials("new.user", "nuova", maven)
    out = maven.read_text(encoding="utf-8")
    check("first server updated, missing <username> added after <id>",
          "<id>nexus</id>\n      <username>new.user</username>\n      <password>nuova</password>"
          in out, out)
    check("comment, mirrors and second server untouched",
          "<!-- <server><username>no</username></server> -->" in out and "<mirrors>" in out
          and "<username>x</username><password>y</password>" in out)
    template = TMP / "tpl.xml"
    template.write_text(_MAVEN_TEMPLATE, encoding="utf-8")
    check("template placeholders read as empty",
          ConfigLoader.read_maven_credentials_raw(template) == ("", ""))


def test_dialog_save_and_cancel():
    print("\n[5] Settings dialog: Salva writes, Annulla discards")
    from ssh_connection.gui.search_dialog import SearchPopup
    from ssh_connection.gui.settings_dialog import SettingsDialog
    cfg = TMP / "dlg_config"
    cfg.write_text(SAMPLE, encoding="utf-8")
    creds = TMP / "dlg_settings.xml"
    ConfigLoader.save_maven_credentials("u1", "p1", creds)
    real_path = ConfigLoader.maven_settings_path
    ConfigLoader.maven_settings_path = staticmethod(lambda: creds)
    popup = SearchPopup(host_provider=lambda: list(SshConfigParser.parse_ssh_config(cfg).items()),
                        on_select=lambda h: None)
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

    saved = {}
    dlg = SettingsDialog(ui_host=popup, host_provider=lambda: [], version="9",
                         on_save=lambda v: saved.update(v) or None, ssh_config_path=cfg)
    try:
        dlg.prewarm()
        dlg.show()
        time.sleep(0.8)
        on_ui(lambda: out.update(rows=dlg._host_tree.get_children(), user=dlg._var_user.get(),
                                 jump=dlg._var_jump["TEST"].get()))
        check("hosts table filled (jump hosts excluded)",
              "stlit1tf01" in out["rows"] and "login_test" not in out["rows"], str(out["rows"]))
        check("credentials and jump address loaded", (out["user"], out["jump"]) == ("u1", "10.180.22.2"))

        # Annulla: nothing written
        on_ui(lambda: (dlg._doc.add_host("TEST", "cancelled1"), dlg._var_user.set("zzz"),
                       dlg._hide()))
        check("Annulla leaves the config as it was", cfg.read_text(encoding="utf-8") == SAMPLE)
        check("Annulla leaves the credentials", ConfigLoader.read_maven_credentials_raw(creds) == ("u1", "p1"))

        dlg.show()
        time.sleep(0.5)

        def edit_and_save():
            out["reloaded"] = dlg._doc.host("cancelled1") is None and not dlg._doc.dirty
            dlg._doc.add_host("TEST", "saved1")
            dlg._var_jump["PROD"].set("10.0.0.7")
            dlg._var_user.set("DOM\\u2")
            dlg._var_password.set("p2")
            dlg._save()
            out["visible"] = dlg._top.winfo_viewable()
            out["error"] = dlg._var_error.get()
        on_ui(edit_and_save)
        check("reopening reloads the file (cancelled edit gone)", out["reloaded"])
        text = cfg.read_text(encoding="utf-8")
        check("Salva writes the new host and the jump address",
              "Host saved1" in text and "HostName 10.0.0.7" in text, out["error"])
        check("backup written next to the config",
              (TMP / "dlg_config.bak").read_text(encoding="utf-8") == SAMPLE)
        check("Salva writes the credentials", ConfigLoader.read_maven_credentials_raw(creds) == ("DOM\\u2", "p2"))
        check("preferences still go through on_save", saved.get("dark_theme") is False, str(saved))
        check("dialog closed after save", out["visible"] == 0)

        dlg.show()
        time.sleep(0.5)

        def empty_password():
            dlg._var_password.set("")
            dlg._save()
            out["error"] = dlg._var_error.get()
            out["visible"] = dlg._top.winfo_viewable()
            dlg._hide()
        on_ui(empty_password)
        check("empty password refused, dialog stays open",
              "password" in out["error"].lower() and out["visible"] == 1, out["error"])
    finally:
        ConfigLoader.maven_settings_path = real_path
        popup.stop()


def main():
    AppSettings.path = TMP / "prefs.json"
    try:
        test_model()
        test_add_edit_delete()
        test_file_format_and_save()
        test_credentials()
        test_dialog_save_and_cancel()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
