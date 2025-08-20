import yaml
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
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


class ConfigLoader:
    """Loader for application configuration from YAML files and Maven settings"""
    
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
            # Default Maven settings location
            home_dir = Path.home()
            maven_settings_path = home_dir / ".m2" / "settings.xml"
        
        if not maven_settings_path.exists():
            return None
        
        try:
            tree = ET.parse(maven_settings_path)
            root = tree.getroot()
            
            # Maven XML namespace
            ns = {'maven': 'http://maven.apache.org/SETTINGS/1.1.0'}
            
            # Try to find servers section - handle both with and without namespace
            servers = root.find('.//servers') or root.find('.//maven:servers', ns)
            if servers is None:
                return None
            
            # Get first server (assuming single server configuration)
            server = servers.find('.//server') or servers.find('.//maven:server', ns)
            if server is None:
                return None
            
            # Extract server details
            server_id_elem = server.find('id') or server.find('maven:id', ns)
            username_elem = server.find('username') or server.find('maven:username', ns)
            password_elem = server.find('password') or server.find('maven:password', ns)
            
            if server_id_elem is not None and username_elem is not None and password_elem is not None:
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