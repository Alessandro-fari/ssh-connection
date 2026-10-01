"""
Edit ~/.ssh/config from the settings dialog without opening it by hand.

The file is hand-written and full of things that must survive an edit:
comments, blank lines, tabs, CRLF, wildcard blocks with the DB tunnels,
commented-out directives. So it is never regenerated: `SshConfigDocument`
keeps the lines as they are and every operation touches only the lines it
must (a Host line, a HostName/Port directive, one LocalForward of the jump
host), copying the indentation of the neighbouring lines.

Model (the same the launcher relies on):
- sections TEST / PROD come from the header comments, exactly as in
  SshConfigParser (a comment containing TEST or PROD switches section);
- the jump host of a section is its first `login*` host (alphabetically,
  like SshConfigParser.sort_hosts + SshLauncher._required_jump_host);
- a host "behind the jump host" has `HostName localhost` + `Port P`, and
  the jump host has `LocalForward P <destination>:<port>`. Adding one
  creates both, on the first free local port after the jump host's ones.
Hosts reached directly (a real HostName) can be edited too (HostName, Port).

Besides its tunnel, a host can forward more ports with `LocalForward` lines
in its own block (DB, HSM, JMX...): `forwards()` lists them with the comment
right above each line as description, `set_forwards()` replaces the list,
touching only the lines that changed. A local port can be shared with
another host (the two sessions just cannot be open together: a warning,
not an error), but not with a jump host tunnel nor with a block that also
applies to this host (wildcards): ssh would fail to bind it.

A host's description is the comment block right above its `Host` line.
Deleting a host removes that comment, the block and the jump host tunnel.

Saving writes a backup (`config.bak`) and replaces the file atomically; it
refuses if the file changed on disk since it was loaded (edited by hand in
the meantime), so nothing is overwritten silently.
"""

import fnmatch
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ENVS = ("TEST", "PROD")
_LOCAL_HOSTS = ("localhost", "127.0.0.1")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")   # hostname / IPv4
_DEFAULT_INDENT = "    "
_FIRST_TUNNEL_PORT = {"TEST": 2222, "PROD": 3222}
# A comment starting with one of these is a commented-out directive, not
# the description of the LocalForward below it.
_COMMENTED_DIRECTIVE_RE = re.compile(
    r"^(localforward|remoteforward|dynamicforward|hostname|port|user|proxyjump|"
    r"identityfile|host)\b", re.IGNORECASE)


class ConfigError(Exception):
    """Invalid edit or unwritable file; the message is for the user (Italian)."""


@dataclass
class HostEntry:
    name: str
    env: str
    description: str
    is_jump: bool
    via_jump: bool            # reached through a LocalForward of the jump host
    hostname: str             # HostName directive ("" if missing)
    port: Optional[int]       # Port directive
    dest: str                 # via_jump: tunnel destination, else = hostname
    dest_port: Optional[int]  # via_jump: destination port of the tunnel, else = port
    editable: bool            # False: several names on the Host line, or duplicated
    duplicate: bool = False   # the name has more than one Host block (ssh uses the first)

    @property
    def kind(self) -> str:
        return "jump" if self.is_jump else ("tunnel" if self.via_jump else "direct")


@dataclass
class PortForward:
    """A LocalForward of a host's own block, as shown in the host form."""
    local_port: int
    dest: str = "localhost"
    dest_port: Optional[int] = None   # None = same as local_port
    description: str = ""
    bind: str = ""                    # "" or "addr:" in front of the local port
    origin: Optional[int] = None      # index in forwards() it comes from; None = new


@dataclass
class _Block:
    names: List[str]
    section: Optional[str]
    lead: int        # first line of the description comment (== host if none)
    host: int        # the "Host ..." line
    end: int         # exclusive, trailing blank lines / headers excluded


@dataclass
class _Forward:
    line: int
    bind: str        # "" or "addr:" in front of the local port
    local_port: int
    dest: str
    dest_port: int


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _comment_text(line: str) -> str:
    return line.lstrip().lstrip("#").strip()


def _is_header(line: str) -> bool:
    """Section header decoration: '#####' or '#   TEST   #'."""
    text = line.strip().strip("#").strip()
    return text == "" or text.upper() in ENVS


def _directive(line: str) -> Tuple[str, str]:
    """('hostname', 'localhost') for '  HostName localhost' / 'HostName=localhost'."""
    s = line.strip()
    if not s or s.startswith("#"):
        return "", ""
    m = re.match(r"^(\S+?)\s*(?:=\s*|\s+)(.*)$", s)
    if not m:
        return s.lower(), ""
    return m.group(1).lower(), m.group(2).strip()


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def _section_switch(line: str) -> Optional[str]:
    """Same rule as SshConfigParser: a comment mentioning TEST/PROD."""
    if not line.strip().startswith("#"):
        return None
    up = line.upper()
    if "TEST" in up:
        return "TEST"
    if "PROD" in up:
        return "PROD"
    return None


class SshConfigDocument:
    def __init__(self, text: str, path: Optional[Path] = None):
        self.path = path
        self._bom = text.startswith("﻿")
        if self._bom:
            text = text[1:]
        self._eol = "\r\n" if "\r\n" in text else "\n"
        self._final_eol = text.endswith(("\n", "\r"))
        self.lines: List[str] = text.splitlines()
        self._loaded_text = self.text()
        self.dirty = False

    # ------------------------------------------------------------------
    # File

    @classmethod
    def load(cls, path: Path) -> "SshConfigDocument":
        path = Path(path)
        try:
            raw = path.read_bytes().decode("utf-8")
        except FileNotFoundError:
            raw = ""
        except (OSError, UnicodeDecodeError) as e:
            raise ConfigError(f"Impossibile leggere {path}: {e}")
        return cls(raw, path)

    def text(self) -> str:
        body = self._eol.join(self.lines)
        if self._final_eol and self.lines:
            body += self._eol
        return ("﻿" if self._bom else "") + body

    def save(self) -> Optional[Path]:
        """Write the file (backup first). Returns the backup path."""
        if self.path is None:
            raise ConfigError("Nessun file associato.")
        path = self.path
        try:
            current = path.read_bytes().decode("utf-8") if path.exists() else ""
        except (OSError, UnicodeDecodeError) as e:
            raise ConfigError(f"Impossibile leggere {path}: {e}")
        if current != self._loaded_text:
            raise ConfigError(f"{path.name} è stato modificato da un altro programma dopo "
                              f"l'apertura delle Impostazioni: chiudi e riapri per ricaricarlo "
                              f"(le modifiche agli host non sono state salvate).")
        backup = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                backup = path.with_name(path.name + ".bak")
                backup.write_bytes(current.encode("utf-8"))
            tmp = path.with_name(path.name + ".tmp")
            new_text = self.text()
            tmp.write_bytes(new_text.encode("utf-8"))
            os.replace(tmp, path)
        except OSError as e:
            raise ConfigError(f"Impossibile scrivere {path}: {e}")
        self._loaded_text = new_text
        self.dirty = False
        return backup

    # ------------------------------------------------------------------
    # Scanning

    def _blocks(self) -> List[_Block]:
        lines = self.lines
        found: List[Tuple[int, List[str], Optional[str]]] = []
        section = None
        for i, line in enumerate(lines):
            switch = _section_switch(line)
            if switch:
                section = switch
                continue
            key, value = _directive(line)
            if key in ("host", "match"):
                found.append((i, value.split() if key == "host" else [], section))
        blocks: List[_Block] = []
        for n, (h, names, sec) in enumerate(found):
            lead = h
            floor = blocks[-1].host + 1 if blocks else 0
            while lead - 1 >= floor and _is_comment(lines[lead - 1]) \
                    and not _is_header(lines[lead - 1]):
                lead -= 1
            blocks.append(_Block(names, sec, lead, h, 0))
        for n, b in enumerate(blocks):
            end = blocks[n + 1].lead if n + 1 < len(blocks) else len(lines)
            # trailing blank lines and unindented comments (section headers,
            # notes between blocks) are not part of the block
            while end > b.host + 1 and (not lines[end - 1].strip()
                                        or (_is_comment(lines[end - 1])
                                            and not _indent(lines[end - 1]))):
                end -= 1
            b.end = end
        return blocks

    def _block_of(self, name: str) -> Optional[_Block]:
        low = name.lower()
        for b in self._blocks():
            if any(n.lower() == low for n in b.names):
                return b
        return None

    def _body(self, b: _Block):
        """(line index, key, value) of the directives of a block."""
        for i in range(b.host + 1, b.end):
            key, value = _directive(self.lines[i])
            if key:
                yield i, key, value

    def _forwards(self, b: _Block) -> List[_Forward]:
        out = []
        for i, key, value in self._body(b):
            if key != "localforward":
                continue
            parts = value.split()
            if len(parts) != 2:
                continue
            local, remote = parts
            bind, _, port = local.rpartition(":")
            dest, _, dport = remote.rpartition(":")
            try:
                out.append(_Forward(i, bind + ":" if bind else "", int(port), dest, int(dport)))
            except ValueError:
                continue
        return out

    def _all_local_ports(self) -> Dict[int, str]:
        """Every LocalForward local port of the file -> Host line (for messages)."""
        ports = {}
        for b in self._blocks():
            for f in self._forwards(b):
                ports.setdefault(f.local_port, " ".join(b.names) or "Match")
        return ports

    def _description(self, b: _Block) -> str:
        return " · ".join(_comment_text(self.lines[i]) for i in range(b.lead, b.host))

    def _forward_comment(self, b: _Block, line: int) -> int:
        """First line of the comment block describing the LocalForward at
        `line` (== line if none): comments right above it, inside the block."""
        top = line
        while top - 1 > b.host and _is_comment(self.lines[top - 1]) \
                and not _COMMENTED_DIRECTIVE_RE.match(_comment_text(self.lines[top - 1])):
            top -= 1
        return top

    def _blocks_applying_to(self, name: str) -> List[_Block]:
        """Wildcard / multi-name blocks (not `name`'s own) whose Host
        patterns match `name`: their LocalForwards open with its session."""
        low = name.lower()
        out = []
        for b in self._blocks():
            if not b.names or any(n.lower() == low for n in b.names):
                continue
            pos = [n.lower() for n in b.names if not n.startswith("!")]
            neg = [n[1:].lower() for n in b.names if n.startswith("!")]
            if any(fnmatch.fnmatchcase(low, p) for p in pos) and \
                    not any(fnmatch.fnmatchcase(low, p) for p in neg):
                out.append(b)
        return out

    def jump_host(self, env: str) -> Optional[str]:
        names = sorted((b.names[0] for b in self._blocks()
                        if b.section == env and len(b.names) == 1
                        and b.names[0].lower().startswith("login")), key=str.lower)
        return names[0] if names else None

    def hosts(self) -> List[HostEntry]:
        """Hosts of the TEST / PROD sections (no wildcards), in file order."""
        entries = []
        jumps = {env: self.jump_host(env) for env in ENVS}
        jump_fw = {}
        for env, j in jumps.items():
            jb = self._block_of(j) if j else None
            jump_fw[env] = {f.local_port: f for f in self._forwards(jb)} if jb else {}
        blocks = self._blocks()
        count: Dict[str, int] = {}
        for b in blocks:
            for n in b.names:
                count[n.lower()] = count.get(n.lower(), 0) + 1
        for b in blocks:
            if b.section not in ENVS:
                continue
            names = [n for n in b.names if "*" not in n and "?" not in n and not n.startswith("!")]
            if not names:
                continue
            hostname, port = "", None
            for _, key, value in self._body(b):
                if key == "hostname" and not hostname:
                    hostname = value
                elif key == "port" and port is None:
                    try:
                        port = int(value)
                    except ValueError:
                        pass
            for name in names:
                is_jump = name == jumps[b.section]
                fw = jump_fw[b.section].get(port) if (
                    not is_jump and hostname.lower() in _LOCAL_HOSTS and port) else None
                entries.append(HostEntry(
                    name=name, env=b.section, description=self._description(b),
                    is_jump=is_jump, via_jump=fw is not None, hostname=hostname, port=port,
                    dest=fw.dest if fw else hostname, dest_port=fw.dest_port if fw else port,
                    editable=len(b.names) == 1 and count[name.lower()] == 1,
                    duplicate=count[name.lower()] > 1))
        return entries

    def host(self, name: str) -> Optional[HostEntry]:
        return next((h for h in self.hosts() if h.name == name), None)

    def next_free_port(self, env: str) -> int:
        used = self._all_local_ports()
        j = self.jump_host(env)
        jb = self._block_of(j) if j else None
        mine = [f.local_port for f in self._forwards(jb)] if jb else []
        port = max(mine) + 1 if mine else _FIRST_TUNNEL_PORT.get(env, 2222)
        while port in used:
            port += 1
        return port

    # ------------------------------------------------------------------
    # Validation

    def _check_name(self, name: str, current: Optional[str] = None) -> None:
        if not _NAME_RE.match(name or ""):
            raise ConfigError("Nome host non valido: solo lettere, cifre, '.', '_' e '-', "
                              "senza spazi né caratteri jolly.")
        if name.lower() != (current or "").lower() and self._block_of(name) is not None:
            raise ConfigError(f"Esiste già un host {name} nel config.")

    @staticmethod
    def _check_dest(dest: str) -> None:
        if not _DEST_RE.match(dest or ""):
            raise ConfigError("Server di destinazione non valido (nome o indirizzo IP, senza spazi).")

    @staticmethod
    def _check_port(port, label: str) -> int:
        try:
            port = int(port)
        except (TypeError, ValueError):
            raise ConfigError(f"{label}: inserire un numero.")
        if not 1 <= port <= 65535:
            raise ConfigError(f"{label}: valore ammesso tra 1 e 65535.")
        return port

    @staticmethod
    def _check_description(description: str, env: str) -> str:
        description = " ".join((description or "").split())
        other = [e for e in ENVS if e != env]
        # The parser switches section on any comment mentioning TEST/PROD:
        # a description naming the other environment would move every
        # following host there.
        if any(e in description.upper() for e in other):
            raise ConfigError(f"La descrizione non può contenere \"{other[0]}\": il config "
                              f"userebbe quel commento come inizio della sezione {other[0]}.")
        return description

    @staticmethod
    def _check_editable(entry: HostEntry) -> None:
        if entry.duplicate:
            raise ConfigError(f"{entry.name} è definito più volte nel config (ssh usa solo il "
                              f"primo blocco): correggi a mano il file.")
        if not entry.editable:
            raise ConfigError(f"{entry.name} è definito insieme ad altri nomi sulla stessa riga "
                              f"Host: modificalo a mano nel file.")

    def _check_local_port(self, port: int, own: Optional[int] = None) -> None:
        used = self._all_local_ports()
        if port != own and port in used:
            raise ConfigError(f"La porta locale {port} è già usata da un LocalForward "
                              f"di \"{used[port]}\".")

    # ------------------------------------------------------------------
    # Editing helpers

    def _body_indent(self, b: _Block) -> str:
        for i, _, _ in self._body(b):
            ind = _indent(self.lines[i])
            if ind:
                return ind
        return _DEFAULT_INDENT

    def _set_directive(self, b: _Block, key: str, value: str) -> None:
        for i, k, _ in self._body(b):
            if k == key.lower():
                line = self.lines[i]
                self.lines[i] = _indent(line) + line.strip().split(None, 1)[0].split("=")[0] \
                    + " " + value
                return
        self.lines.insert(b.host + 1, self._body_indent(b) + key + " " + value)

    def _set_description(self, b: _Block, description: str) -> None:
        new = ["# " + description] if description else []
        if self._description(b) == description:
            return
        self.lines[b.lead:b.host] = new

    def _rename_in_host_line(self, b: _Block, old: str, new: str) -> None:
        line = self.lines[b.host]
        self.lines[b.host] = re.sub(r"(?<!\S)" + re.escape(old) + r"(?!\S)", new, line, count=1)

    def _forward_line(self, jb: _Block, template: Optional[_Forward], bind: str,
                      local: int, dest: str, dest_port: int) -> str:
        if template is not None:
            line = self.lines[template.line]
            keyword = line.strip().split(None, 1)[0]
            ind = _indent(line)
        else:
            keyword, ind = "LocalForward", self._body_indent(jb)
        return f"{ind}{keyword} {bind}{local} {dest}:{dest_port}"

    def _jump_block(self, env: str) -> _Block:
        j = self.jump_host(env)
        jb = self._block_of(j) if j else None
        if jb is None:
            raise ConfigError(f"Nella sezione {env} non c'è un jump host (login_...).")
        return jb

    # ------------------------------------------------------------------
    # Operations

    def add_host(self, env: str, name: str, dest: str = "", dest_port=22,
                 local_port=None, description: str = "") -> HostEntry:
        """New host of `env` behind its jump host: tunnel + Host block."""
        if env not in ENVS:
            raise ConfigError("Ambiente non valido.")
        name = (name or "").strip()
        self._check_name(name)
        dest = (dest or "").strip() or name
        self._check_dest(dest)
        dest_port = self._check_port(dest_port, "Porta del server")
        description = self._check_description(description, env)
        jb = self._jump_block(env)
        local = (self._check_port(local_port, "Porta locale") if local_port not in (None, "")
                 else self.next_free_port(env))
        self._check_local_port(local)

        # 1) the Host block, after the last block of the section
        last = max((b for b in self._blocks() if b.section == env), key=lambda b: b.host)
        ind = self._body_indent(last) if last is not jb else _DEFAULT_INDENT
        block = ([""] + (["# " + description] if description else [])
                 + [f"Host {name}", f"{ind}HostName localhost", f"{ind}Port {local}"])
        if last.end < len(self.lines) and self.lines[last.end].strip():
            block.append("")                  # keep a blank line before what follows
        self.lines[last.end:last.end] = block
        # 2) the tunnel on the jump host (earlier in the file: indices unchanged)
        fws = self._forwards(jb)
        tpl = fws[-1] if fws else None
        at = tpl.line + 1 if tpl else max([i for i, _, _ in self._body(jb)] + [jb.host]) + 1
        self.lines.insert(at, self._forward_line(jb, tpl, tpl.bind if tpl else "",
                                                 local, dest, dest_port))
        self.dirty = True
        return self.host(name)

    def update_host(self, name: str, new_name: Optional[str] = None, dest: Optional[str] = None,
                    dest_port=None, local_port=None, description: Optional[str] = None,
                    hostname: Optional[str] = None, port=None) -> HostEntry:
        """Edit a host. Tunnel hosts: name, dest, dest_port, local_port,
        description. Direct hosts: name, hostname, port, description. Jump
        hosts: hostname and description only (their name is referenced by
        Init and by the launcher)."""
        entry = self.host(name)
        if entry is None:
            raise ConfigError(f"Host {name} non trovato nel config.")
        self._check_editable(entry)
        new_name = (new_name or name).strip()
        if entry.is_jump and new_name != name:
            raise ConfigError("Il nome del jump host non si può cambiare: lo usano Init e i tunnel.")
        self._check_name(new_name, current=name)
        if description is not None:
            description = self._check_description(description, entry.env)

        if entry.kind == "tunnel":
            dest = (dest if dest is not None else entry.dest).strip()
            self._check_dest(dest)
            dest_port = self._check_port(dest_port if dest_port not in (None, "")
                                         else entry.dest_port, "Porta del server")
            local = self._check_port(local_port if local_port not in (None, "")
                                     else entry.port, "Porta locale")
            self._check_local_port(local, own=entry.port)
            jb = self._jump_block(entry.env)
            fw = next(f for f in self._forwards(jb) if f.local_port == entry.port)
            self.lines[fw.line] = self._forward_line(jb, fw, fw.bind, local, dest, dest_port)
            if local != entry.port:
                self._set_directive(self._block_of(name), "Port", str(local))
        else:
            if hostname is not None:
                hostname = hostname.strip()
                self._check_dest(hostname)
                self._set_directive(self._block_of(name), "HostName", hostname)
            if port not in (None, ""):
                self._set_directive(self._block_of(name), "Port",
                                    str(self._check_port(port, "Porta")))
        if description is not None:
            self._set_description(self._block_of(name), description)
        if new_name != name:
            self._rename_in_host_line(self._block_of(name), name, new_name)
        self.dirty = True
        return self.host(new_name)

    def delete_host(self, name: str) -> None:
        entry = self.host(name)
        if entry is None:
            raise ConfigError(f"Host {name} non trovato nel config.")
        if entry.is_jump:
            raise ConfigError("Il jump host non si può eliminare da qui.")
        self._check_editable(entry)
        b = self._block_of(name)
        start, end = b.lead, b.end
        fw_line = None
        if entry.via_jump:
            jb = self._jump_block(entry.env)
            fw_line = next(f.line for f in self._forwards(jb) if f.local_port == entry.port)
        if fw_line is not None and fw_line > start:    # jump host after the host
            del self.lines[fw_line]
            fw_line = None
        del self.lines[start:end]
        # the blank lines around the removed block: keep one
        while (0 < start < len(self.lines) and not self.lines[start].strip()
               and not self.lines[start - 1].strip()):
            del self.lines[start]
        if start == len(self.lines):
            while self.lines and not self.lines[-1].strip():
                self.lines.pop()
        if fw_line is not None:            # the jump block is before the host
            del self.lines[fw_line]
        self.dirty = True

    def set_jump_hostname(self, env: str, hostname: str) -> None:
        j = self.jump_host(env)
        if j is None:
            raise ConfigError(f"Nella sezione {env} non c'è un jump host (login_...).")
        self.update_host(j, hostname=hostname)

    # ------------------------------------------------------------------
    # Port forwards of a host (LocalForward lines in its own block)

    def forwards(self, name: str) -> List[PortForward]:
        b = self._block_of(name)
        if b is None:
            return []
        out = []
        for n, f in enumerate(self._forwards(b)):
            top = self._forward_comment(b, f.line)
            desc = " · ".join(_comment_text(self.lines[i]) for i in range(top, f.line))
            out.append(PortForward(f.local_port, f.dest, f.dest_port, desc, f.bind, origin=n))
        return out

    def check_forwards(self, name: str, env: str, forwards: List[PortForward],
                       current: Optional[str] = None) -> Tuple[List[PortForward], List[str]]:
        """Validate the forwards host `name` would have (`current`: the
        existing host they belong to, None for a new one). Returns
        (normalized forwards, warnings); raises ConfigError for what would
        not work. Ports already in the file and left unchanged are not
        checked again: the file is the user's."""
        cur_block = self._block_of(current) if current else None
        old = self._forwards(cur_block) if cur_block else []
        old_desc = [f.description for f in self.forwards(current)] if cur_block else []
        # the editor's text is compared verbatim: an untouched description
        # is neither re-validated nor rewritten
        out: List[PortForward] = []
        seen: Set[int] = set()
        for f in forwards:
            local = self._check_port(f.local_port, "Porta locale")
            dest = (f.dest or "").strip() or "localhost"
            self._check_dest(dest)
            dport = self._check_port(f.dest_port if f.dest_port not in (None, "") else local,
                                     "Porta remota")
            desc = f.description or ""
            if f.origin is None or f.origin >= len(old_desc) or desc != old_desc[f.origin]:
                desc = self._check_description(desc, env)
            if local in seen:
                raise ConfigError(f"La porta locale {local} è inoltrata due volte da questo host.")
            seen.add(local)
            out.append(replace(f, local_port=local, dest=dest, dest_port=dport, description=desc))

        jumps = {j for j in (self.jump_host(e) for e in ENVS) if j}
        jump_ports: Dict[int, str] = {}
        for j in sorted(jumps):
            for f in self._forwards(self._block_of(j)):
                jump_ports.setdefault(f.local_port, f"del tunnel di {j} verso {f.dest}:{f.dest_port}")
        applying = self._blocks_applying_to(name)
        applying_ports: Dict[int, str] = {}
        for b in applying:
            for f in self._forwards(b):
                applying_ports.setdefault(f.local_port, " ".join(b.names))
        skip = {b.host for b in applying} | ({cur_block.host} if cur_block else set())
        others: Dict[int, List[str]] = {}
        for b in self._blocks():
            if b.host in skip or not b.names or b.names[0] in jumps or any(
                    "*" in n or "?" in n or n.startswith("!") for n in b.names):
                continue
            for f in self._forwards(b):
                others.setdefault(f.local_port, []).append(" ".join(b.names))

        warnings = []
        for f in out:
            if f.origin is not None and f.origin < len(old) \
                    and old[f.origin].local_port == f.local_port:
                continue                       # already in the file, untouched
            p = f.local_port
            if p in jump_ports:
                raise ConfigError(f"La porta locale {p} è già usata: è la porta {jump_ports[p]}, "
                                  f"sempre aperta insieme al jump host.")
            if p in applying_ports:
                raise ConfigError(f"La porta locale {p} è già inoltrata a questo host dal "
                                  f"blocco \"Host {applying_ports[p]}\".")
            if p in others:
                warnings.append(f"La porta locale {p} è inoltrata anche da "
                                f"{', '.join(others[p])}: le due sessioni non potranno essere "
                                f"aperte insieme.")
        return out, warnings

    def set_forwards(self, name: str, forwards: List[PortForward]) -> None:
        """Make `forwards` the LocalForwards of `name`'s block. Entries keep
        `origin` from forwards(): unchanged ones are left as they are,
        edited ones rewritten in place, missing ones removed with their
        comment, new ones appended at the end of the block."""
        entry = self.host(name)
        if entry is None:
            raise ConfigError(f"Host {name} non trovato nel config.")
        if entry.is_jump:
            raise ConfigError("Le porte del jump host sono i tunnel degli host: si gestiscono "
                              "dai singoli host.")
        self._check_editable(entry)
        forwards, _ = self.check_forwards(name, entry.env, forwards, current=name)
        b = self._block_of(name)
        old = self._forwards(b)
        before = list(self.lines)
        kept = {f.origin: f for f in forwards if f.origin is not None and f.origin < len(old)}

        # 1) new ones at the end of the block body (below every old line)
        tpl = old[-1] if old else None
        ind = _indent(self.lines[tpl.line]) if tpl else self._body_indent(b)
        at = max([i for i, _, _ in self._body(b)] + [b.host]) + 1
        new_lines = []
        for f in forwards:
            if f.origin is None or f.origin >= len(old):
                if f.description:
                    new_lines.append(f"{ind}# {f.description}")
                new_lines.append(self._forward_line(b, tpl, f.bind, f.local_port,
                                                    f.dest, f.dest_port))
        self.lines[at:at] = new_lines
        # 2) the old ones bottom-up, so the indices above stay valid
        for n in range(len(old) - 1, -1, -1):
            o = old[n]
            top = self._forward_comment(b, o.line)
            desc = " · ".join(_comment_text(self.lines[i]) for i in range(top, o.line))
            f = kept.get(n)
            if f is None:
                del self.lines[top:o.line + 1]
                continue
            if (f.local_port, f.dest, f.dest_port, f.bind) != \
                    (o.local_port, o.dest, o.dest_port, o.bind):
                self.lines[o.line] = self._forward_line(b, o, f.bind, f.local_port,
                                                        f.dest, f.dest_port)
            if f.description != desc:
                cind = _indent(self.lines[top]) if top < o.line else _indent(self.lines[o.line])
                self.lines[top:o.line] = [f"{cind}# {f.description}"] if f.description else []
        if self.lines != before:
            self.dirty = True

    def host_names(self) -> Set[str]:
        return {h.name for h in self.hosts()}
