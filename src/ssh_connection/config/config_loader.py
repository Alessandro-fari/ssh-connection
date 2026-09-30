import yaml
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from xml.sax.saxutils import escape as xml_escape
import os

from ..security.crypto_util import CryptoUtil


@dataclass
class ConnectionConfig:
    """Configuration for a single connection"""
    name: str
    login_server: str
    dest_server: str


@dataclass
class MavenCredentials:
    """Maven server credentials from settings.xml"""
    server_id: str
    username: str
    password: str


_MAVEN_NS = "http://maven.apache.org/SETTINGS/1.1.0"

# Written by ensure_maven_settings() when ~/.m2/settings.xml does not exist.
# Only the FIRST <server> is read (username + password for every SSH login).
_MAVEN_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<settings xmlns="http://maven.apache.org/SETTINGS/1.1.0"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://maven.apache.org/SETTINGS/1.1.0 https://maven.apache.org/xsd/settings-1.1.0.xsd">
  <!--
    SSH Connection Manager legge nome utente e password dal PRIMO <server>.
    Il prefisso di dominio (es. DOMINIO\\nome.cognome) viene tolto dal nome utente.
    Le modifiche valgono dalla prossima connessione, senza riavviare l'app.
  -->
  <servers>
    <server>
      <id>ssh-connection</id>
      <username>INSERISCI_UTENTE</username>
      <password>INSERISCI_PASSWORD</password>
    </server>
  </servers>
</settings>
"""


def _mask_comments(text: str) -> str:
    """`text` with the inside of <!-- --> replaced by spaces (same length),
    so a search never matches the '<server>' mentioned in a comment."""
    return re.sub(r"<!--.*?-->", lambda m: " " * len(m.group(0)), text, flags=re.S)


def _set_server_credentials(text: str, username: str, password: str) -> str:
    masked = _mask_comments(text)
    servers = re.search(r"<servers\b[^>]*>(.*?)</servers>", masked, re.S)
    if servers is None:
        closing = masked.rfind("</settings>")
        if closing < 0:
            raise ValueError("settings.xml senza <settings>: correggilo a mano.")
        block = (f"  <servers>\n    <server>\n      <id>ssh-connection</id>\n"
                 f"      <username>{xml_escape(username)}</username>\n"
                 f"      <password>{xml_escape(password)}</password>\n    </server>\n"
                 f"  </servers>\n")
        return text[:closing] + block + text[closing:]
    server = re.search(r"<server\b[^>]*>(.*?)</server>", masked[servers.start(1):servers.end(1)],
                       re.S)
    if server is None:
        at = servers.end(1)
        block = (f"  <server>\n      <id>ssh-connection</id>\n"
                 f"      <username>{xml_escape(username)}</username>\n"
                 f"      <password>{xml_escape(password)}</password>\n    </server>\n  ")
        return text[:at] + block + text[at:]
    start, end = servers.start(1) + server.start(1), servers.start(1) + server.end(1)
    inner, inner_masked = text[start:end], masked[start:end]
    for tag, value in (("username", username), ("password", password)):
        m = re.search(rf"<{tag}\s*>(.*?)</{tag}\s*>|<{tag}\s*/>", inner_masked, re.S)
        element = f"<{tag}>{xml_escape(value)}</{tag}>"
        if m:
            inner = inner[:m.start()] + element + inner[m.end():]
        else:                               # missing: after <id>, same indentation
            indent = re.search(r"\n([ \t]*)<", inner)
            ind = indent.group(1) if indent else "      "
            after_id = re.search(r"</id\s*>", inner_masked)
            at = after_id.end() if after_id else 0
            inner = inner[:at] + f"\n{ind}{element}" + inner[at:]
        inner_masked = _mask_comments(inner)
    return text[:start] + inner + text[end:]


class ConfigLoader:
    """Loader for application configuration from YAML files and Maven settings"""

    @staticmethod
    def maven_settings_path() -> Path:
        """File holding the SSH username/password (Maven settings.xml)."""
        return Path.home() / ".m2" / "settings.xml"

    @staticmethod
    def ensure_maven_settings() -> bool:
        """Create ~/.m2/settings.xml from a template if it does not exist.
        Returns True if the file was created now. An existing file is never
        touched: it may also be the user's real Maven configuration."""
        path = ConfigLoader.maven_settings_path()
        if path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_MAVEN_TEMPLATE, encoding="utf-8")
        return True

    @staticmethod
    def read_maven_credentials_raw(path: Optional[Path] = None) -> Tuple[str, str]:
        """Username and password as written in the first <server> (domain
        prefix kept, template placeholders shown as empty), for the settings
        dialog. ('', '') when the file is missing or unreadable."""
        path = path or ConfigLoader.maven_settings_path()
        try:
            server = ConfigLoader._find(ConfigLoader._find(ET.parse(path).getroot(),
                                                           './/servers'), './/server')
        except Exception:
            return "", ""
        values = []
        for tag in ("username", "password"):
            elem = ConfigLoader._find(server, tag) if server is not None else None
            text = (elem.text or "").strip() if elem is not None else ""
            values.append("" if text.startswith("INSERISCI_") else text)
        return values[0], values[1]

    @staticmethod
    def save_maven_credentials(username: str, password: str,
                               path: Optional[Path] = None) -> bool:
        """Write username/password into the first <server> of settings.xml,
        creating the file from the template if missing. Only the text of the
        two elements changes: comments, other servers and the rest of a real
        Maven configuration stay as they are. Returns True if the file was
        created. Raises ValueError with a message for the user."""
        path = path or ConfigLoader.maven_settings_path()
        created = False
        if not path.exists():
            if path == ConfigLoader.maven_settings_path():
                created = ConfigLoader.ensure_maven_settings()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_MAVEN_TEMPLATE, encoding="utf-8")
                created = True
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"Impossibile leggere {path}: {e}")
        new_text = _set_server_credentials(text, username.strip(), password)
        try:
            ET.fromstring(new_text.lstrip("﻿").encode("utf-8"))
        except ET.ParseError as e:
            raise ValueError(f"{path.name} non è un XML valido ({e}): correggilo a mano.")
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_bytes(new_text.encode("utf-8"))
            os.replace(tmp, path)
        except OSError as e:
            raise ValueError(f"Impossibile scrivere {path}: {e}")
        return created

    @staticmethod
    def _find(elem, path: str):
        """find() with and without the Maven namespace.

        Explicit `is not None`: an Element with no children is falsy, so the
        previous `a.find(x) or a.find(ns_x)` discarded a found <id>/<username>
        in a settings.xml without namespace and returned no credentials."""
        found = elem.find(path)
        if found is None:
            # './/servers' -> './/m:servers', 'id' -> 'm:id'
            ns_path = f".//m:{path[3:]}" if path.startswith(".//") else f"m:{path}"
            found = elem.find(ns_path, {"m": _MAVEN_NS})
        return found
    
    def __init__(self, encrypted_user: Optional[str], connections: List[Dict[str, Any]], maven_credentials: Optional[MavenCredentials] = None):
        self.encrypted_user = encrypted_user
        self.maven_credentials = maven_credentials
        self.connections = [
            ConnectionConfig(
                name=conn["name"],
                login_server=conn["loginServer"],
                dest_server=conn["destServer"]
            )
            for conn in connections
        ]
    
    @staticmethod
    def _load_maven_credentials(maven_settings_path: Optional[Path] = None) -> Optional[MavenCredentials]:
        """
        Load credentials from Maven settings.xml file
        
        Args:
            maven_settings_path: Path to Maven settings.xml. If None, uses default ~/.m2/settings.xml
            
        Returns:
            MavenCredentials if found, None otherwise
        """
        if maven_settings_path is None:
            maven_settings_path = ConfigLoader.maven_settings_path()
        
        if not maven_settings_path.exists():
            return None
        
        try:
            tree = ET.parse(maven_settings_path)
            root = tree.getroot()
            
            # Handles settings.xml both with and without the Maven namespace.
            servers = ConfigLoader._find(root, './/servers')
            if servers is None:
                return None

            # Get first server (assuming single server configuration)
            server = ConfigLoader._find(servers, './/server')
            if server is None:
                return None

            server_id_elem = ConfigLoader._find(server, 'id')
            username_elem = ConfigLoader._find(server, 'username')
            password_elem = ConfigLoader._find(server, 'password')

            if (server_id_elem is not None and username_elem is not None and password_elem is not None
                    and server_id_elem.text and username_elem.text and password_elem.text
                    # Template not filled in yet: no credentials rather than
                    # typing "INSERISCI_PASSWORD" into every login.
                    and not username_elem.text.strip().startswith("INSERISCI_")
                    and not password_elem.text.strip().startswith("INSERISCI_")):
                return MavenCredentials(
                    server_id=server_id_elem.text.strip(),
                    username=username_elem.text.strip(),
                    password=password_elem.text.strip()
                )
        
        except Exception:
            # Silently fail if Maven settings can't be parsed
            pass
        
        return None
    
    @staticmethod
    def load(config_path: Optional[Path] = None) -> 'ConfigLoader':
        """
        Load configuration from YAML file
        
        Args:
            config_path: Path to config file. If None, uses default resources/config.yml
            
        Returns:
            ConfigLoader instance
        """
        import sys
        import os
        
        if config_path is None:
            # Handle both development and frozen executable environments
            if getattr(sys, 'frozen', False):
                # When running as compiled executable
                if hasattr(sys, '_MEIPASS'):
                    # PyInstaller's temporary folder
                    base_path = Path(sys._MEIPASS)
                    config_path = base_path / "resources" / "config.yml"
                else:
                    # Fallback: exe directory
                    exe_dir = Path(sys.executable).parent
                    config_path = exe_dir / "resources" / "config.yml"
            else:
                # When running as Python script (development)
                project_root = Path(__file__).parent.parent.parent.parent
                config_path = project_root / "resources" / "config.yml"
        
        # Try multiple fallback locations
        possible_paths = [
            config_path,
            Path("resources/config.yml"),  # Current directory
            Path(__file__).parent.parent.parent.parent / "resources" / "config.yml",  # Project root
            Path(os.getcwd()) / "resources" / "config.yml",  # Working directory
        ]
        
        config_data = None
        used_path = None
        
        for path in possible_paths:
            try:
                if path.exists():
                    with open(path, 'r', encoding='utf-8') as file:
                        config_data = yaml.safe_load(file)
                    used_path = path
                    break
            except Exception:
                continue
        
        if config_data is None:
            # List all attempted paths for debugging
            attempted_paths = [str(p) for p in possible_paths]
            raise RuntimeError(f"Failed to load configuration from any of these paths: {attempted_paths}")
        
        # Load Maven credentials
        maven_credentials = ConfigLoader._load_maven_credentials()
        
        try:
            # Support both old format (with encryptedUser) and new format (with Maven credentials)
            encrypted_user = config_data.get("encryptedUser")
            
            return ConfigLoader(
                encrypted_user=encrypted_user,
                connections=config_data["connections"],
                maven_credentials=maven_credentials
            )
        except Exception as e:
            raise RuntimeError(f"Failed to parse configuration from {used_path}: {e}")
    
    def get_connection_by_name(self, name: str) -> Optional[ConnectionConfig]:
        """
        Find connection configuration by name (case insensitive)
        
        Args:
            name: Connection name to search for
            
        Returns:
            ConnectionConfig if found, None otherwise
        """
        for conn in self.connections:
            if conn.name.lower() == name.lower():
                return conn
        return None
    
    def get_encrypted_user(self) -> Optional[str]:
        """Get the encrypted user string"""
        return self.encrypted_user
    
    def get_username(self) -> Optional[str]:
        """
        Get username from Maven credentials or decrypted config
        Strips domain prefix (netsgroup\\) if present
        
        Returns:
            Username string if available, None otherwise
        """
        username = None
        if self.maven_credentials:
            username = self.maven_credentials.username
        elif self.encrypted_user:
            username = CryptoUtil.decrypt(self.encrypted_user)
        
        if username and '\\' in username:
            # Extract username after domain prefix (e.g., netsgroup\a.farina -> a.farina)
            username = username.split('\\')[-1]
        
        return username
    
    def get_password(self) -> Optional[str]:
        """
        Get password from Maven credentials
        
        Returns:
            Password string if available from Maven, None otherwise
        """
        if self.maven_credentials:
            return self.maven_credentials.password
        return None
    
    def get_maven_credentials(self) -> Optional[MavenCredentials]:
        """Get Maven credentials if available"""
        return self.maven_credentials
    
    @staticmethod
    def decrypt(encrypted_user: str) -> str:
        """
        Decrypt encrypted user string
        
        Args:
            encrypted_user: Encrypted user string
            
        Returns:
            Decrypted user string
        """
        return CryptoUtil.decrypt(encrypted_user)


if __name__ == "__main__":
    # Test configuration loading
    config = ConfigLoader.load()
    print(f"Encrypted user: {config.get_encrypted_user()}")
    print(f"Username: {config.get_username()}")
    print(f"Password: {'*' * len(config.get_password()) if config.get_password() else 'None'}")
    print(f"Maven credentials: {config.get_maven_credentials().server_id if config.get_maven_credentials() else 'None'}")
    print(f"Connections: {[conn.name for conn in config.connections]}")
    
    test_conn = config.get_connection_by_name("Test")
    if test_conn:
        print(f"Test connection: {test_conn}")