import pystray
from PIL import Image, ImageDraw
import threading
from typing import Dict, List
import os
from pathlib import Path
import sys

from ..ssh.ssh_config_parser import SshConfigParser
from ..ssh.ssh_launcher import SshLauncher


class TrayIconManager:
    """System tray icon manager for SSH connection management"""
    
    def __init__(self):
        self.icon = None
        self.host_map = {}
    
    def create_icon_image(self) -> Image.Image:
        """
        Create a simple icon image for the system tray
        
        Returns:
            PIL Image for the tray icon
        """
        # Create a simple 64x64 icon with SSH text
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Draw a simple circle background
        draw.ellipse([8, 8, 56, 56], fill=(70, 130, 180), outline=(25, 25, 112), width=2)
        
        # Draw SSH text
        draw.text((18, 25), "SSH", fill=(255, 255, 255))
        
        return image
    
    def create_menu(self) -> pystray.Menu:
        """
        Create the context menu for the tray icon
        
        Returns:
            pystray.Menu with SSH connections organized by environment
        """
        menu_items = []
        
        # Parse SSH config to get host mapping
        self.host_map = SshConfigParser.parse_ssh_config()
        
        # Create TEST section
        if self.host_map.get("TEST"):
            test_items = []
            for host in self.host_map["TEST"]:
                # Fix lambda closure issue by using partial function
                def make_connect_callback(hostname):
                    return lambda icon, item: self.connect_to_host(hostname)
                
                test_items.append(
                    pystray.MenuItem(host, make_connect_callback(host))
                )
            menu_items.append(pystray.Menu.SEPARATOR)
            menu_items.append(pystray.MenuItem("TEST", pystray.Menu(*test_items)))
        
        # Create PROD section
        if self.host_map.get("PROD"):
            prod_items = []
            for host in self.host_map["PROD"]:
                # Fix lambda closure issue by using partial function
                def make_connect_callback(hostname):
                    return lambda icon, item: self.connect_to_host(hostname)
                
                prod_items.append(
                    pystray.MenuItem(host, make_connect_callback(host))
                )
            menu_items.append(pystray.MenuItem("PROD", pystray.Menu(*prod_items)))
        
        # Add separator and exit option
        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(pystray.MenuItem("Settings", self.open_settings))
        menu_items.append(pystray.MenuItem("Reboot", self.reboot_application))
        menu_items.append(pystray.MenuItem("Exit", self.quit_application))
        
        return pystray.Menu(*menu_items)
    
    def open_settings(self, icon: pystray.Icon, item) -> None:
        """Open the SSH config file in the default editor"""
        try:
            ssh_config_path = Path.home() / ".ssh" / "config"
            if ssh_config_path.exists():
                os.startfile(ssh_config_path)
            else:
                print(f"SSH config file not found: {ssh_config_path}")
        except Exception as e:
            print(f"Error opening SSH config: {e}")

    def connect_to_host(self, host: str) -> None:
        """
        Connect to specified SSH host
        
        Args:
            host: SSH hostname to connect to
        """
        print(f"Connecting to {host}...")
        SshLauncher.connect(host)
    
    def reboot_application(self, icon: pystray.Icon, item) -> None:
        """Restart the application"""
        print("Rebooting application...")
        try:
            self.icon.stop()
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            print(f"Error rebooting application: {e}")

    def quit_application(self, icon: pystray.Icon, item) -> None:
        """
        Quit the application
        
        Args:
            icon: The tray icon instance
            item: The menu item that was clicked
        """
        print("Quitting application...")
        icon.stop()
    
    def init_tray(self) -> None:
        """Initialize and start the system tray icon"""
        import logging
        
        try:
            logging.info("Creating tray icon image...")
            # Create icon image
            icon_image = self.create_icon_image()
            
            logging.info("Creating tray menu...")
            # Create menu
            menu = self.create_menu()
            
            logging.info("Creating pystray icon...")
            # Create and start tray icon
            self.icon = pystray.Icon(
                "SSH Connection Manager",
                icon_image,
                menu=menu
            )
            
            test_count = len(self.host_map.get('TEST', []))
            prod_count = len(self.host_map.get('PROD', []))
            
            logging.info(f"Starting SSH Connection Manager with {test_count} TEST hosts and {prod_count} PROD hosts")
            print(f"Starting SSH Connection Manager with {test_count} TEST hosts and {prod_count} PROD hosts")
            
            # Run the tray icon (this blocks)
            logging.info("Running tray icon (this will block)...")
            self.icon.run()
            
        except Exception as e:
            error_msg = f"Error initializing tray icon: {e}"
            logging.error(error_msg, exc_info=True)
            print(error_msg)
            
            # Show error notification if possible
            try:
                import sys
                if getattr(sys, 'frozen', False):
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(0, 
                        f"System tray initialization failed:\n\n{error_msg}", 
                        "SSH Connection Manager - Tray Error", 
                        0x10)  # MB_ICONERROR
            except:
                pass
            raise
    
    def start_in_background(self) -> threading.Thread:
        """
        Start the tray icon in a background thread
        
        Returns:
            Thread running the tray icon
        """
        thread = threading.Thread(target=self.init_tray, daemon=True)
        thread.start()
        return thread
    
    def stop(self) -> None:
        """Stop the tray icon"""
        if self.icon:
            self.icon.stop()


if __name__ == "__main__":
    # Test tray icon manager
    manager = TrayIconManager()
    manager.init_tray()