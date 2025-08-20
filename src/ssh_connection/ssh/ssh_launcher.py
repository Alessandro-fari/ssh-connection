import subprocess
import time
import pyautogui
from typing import Optional

from ..config.config_loader import ConfigLoader, ConnectionConfig


class SshLauncher:
    """SSH connection launcher with automated credential input"""
    
    @staticmethod
    def connect_old(name: str) -> None:
        """
        Legacy connection method using jump server approach
        
        Args:
            name: Connection name to look up in configuration
        """
        config = ConfigLoader.load()
        connection = config.get_connection_by_name(name)
        
        if connection:
            user = config.get_username()
            if user:
                command = (
                    f'powershell.exe -NoExit ssh {user}@{connection.login_server} '
                    f'-t ssh {user}@{connection.dest_server}'
                )
            try:
                subprocess.Popen(['cmd', '/c', command])
            except Exception as e:
                print(f"Error launching SSH connection: {e}")
    
    @staticmethod
    def connect(name: str) -> None:
        """
        Connect to SSH host by name using direct SSH command
        
        Args:
            name: SSH host name as defined in SSH config
        """
        try:
            print(f"Launching SSH command for: {name}")
            
            # Use direct batch launcher for maximum speed (like Java executable)
            from pathlib import Path
            script_dir = Path(__file__).parent.parent.parent.parent
            batch_file = script_dir / "quick_ssh.bat"
            
            if batch_file.exists():
                # Launch using batch file for native speed
                process = subprocess.Popen([
                    str(batch_file), name
                ], 
                shell=False,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
                print(f"SSH launched via batch file - maximum speed")
                
                # Auto-input password after 3 seconds (async)
                import threading
                config = ConfigLoader.load()
                password = config.get_password()
                password_thread = threading.Thread(target=SshLauncher._input_password, args=(password,), daemon=True)
                password_thread.start()
            else:
                # Fallback to Python method
                SshLauncher._connect_python_method(name)
            
        except Exception as e:
            print(f"Error launching SSH connection: {e}")
            # Fallback to Python method
            SshLauncher._connect_python_method(name)
    
    @staticmethod
    def _connect_python_method(name: str) -> None:
        """
        Fallback Python method for SSH connection
        
        Args:
            name: SSH host name as defined in SSH config
        """
        config = ConfigLoader.load()
        username = config.get_username()
        
        # Build SSH command with explicit username if available
        if username:
            command = f"ssh {username}@{name}"
        else:
            command = f"ssh {name}"
        
        print(f"Using Python fallback method for: {command}")
        
        # Launch PowerShell exactly like Java with inheritIO equivalent
        process = subprocess.Popen([
            'cmd', '/c', 'start', 'powershell', '-NoExit', '-Command', command
        ], 
        shell=False, 
        stdin=None, 
        stdout=None, 
        stderr=None,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
        
        print(f"SSH process started")
        
        # Automatically input password after delay (async to not block)
        import threading
        password = config.get_password()
        password_thread = threading.Thread(target=SshLauncher._input_password, args=(password,), daemon=True)
        password_thread.start()
    
    @staticmethod
    def _input_password(password: Optional[str] = None) -> None:
        """
        Automatically input password using GUI automation
        Now only inputs password since username is passed in SSH command
        
        Args:
            password: Password to input. If None, loads from config
        """
        if password is None:
            config = ConfigLoader.load()
            password = config.get_password()
        
        if password is None:
            print("No password available - skipping credential input")
            return
        
        try:
            print("Inserting credentials...")
            
            # Wait for PowerShell to open and SSH to start (optimized to 4 seconds)
            time.sleep(4)
            
            # Type password and press Enter
            # Username is now passed directly in SSH command, so we only input password
            pyautogui.typewrite(password)
            pyautogui.press('enter')
            
            print("Credentials inserted.")
            
        except Exception as e:
            print(f"Error inputting password: {e}")
    
    @staticmethod
    def get_current_password() -> Optional[str]:
        """
        Get the current password from configuration
        
        Returns:
            Current password from Maven settings or None
        """
        config = ConfigLoader.load()
        return config.get_password()


if __name__ == "__main__":
    # Test SSH launcher
    print("Testing SSH launcher...")
    
    # Test configuration loading
    config = ConfigLoader.load()
    if config.connections:
        print(f"Available connections: {[conn.name for conn in config.connections]}")
        
        # Uncomment to test actual connection
        # SshLauncher.connect("login_test")
    else:
        print("No connections configured")