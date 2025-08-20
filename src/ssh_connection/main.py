#!/usr/bin/env python3
"""
SSH Connection Manager

A Python application that provides a system tray interface for managing SSH connections.
Reads SSH configuration and organizes connections into TEST and PROD environments.

Author: DevAgent
"""

import sys
import argparse
import os
from pathlib import Path

from .gui.tray_icon_manager import TrayIconManager
from .ssh.ssh_config_parser import SshConfigParser
from .ssh.ssh_launcher import SshLauncher
from .config.config_loader import ConfigLoader


class SshConnectionApp:
    """Main SSH Connection Manager application"""
    
    def __init__(self):
        self.tray_manager = TrayIconManager()
    
    def run(self) -> None:
        """Run the application with system tray interface"""
        import logging
        
        # Setup logging for debugging
        log_file = Path.home() / "ssh_connection_debug.log"
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler() if not getattr(sys, 'frozen', False) else logging.NullHandler()
            ]
        )
        
        logging.info("SSH Connection Manager starting...")
        
        # Hide console window when running as executable (after logging setup)
        if os.name == 'nt' and getattr(sys, 'frozen', False):  # Windows and running as exe
            import ctypes
            ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        elif os.name == 'nt':  # Windows but not exe
            os.system('title SSH Connection Manager')
        
        # Show startup notification
        if getattr(sys, 'frozen', False):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, 
                    "SSH Connection Manager is starting...\n\nLook for the icon in the system tray (bottom-right corner).", 
                    "SSH Connection Manager", 
                    0x40 | 0x1000)  # MB_ICONINFORMATION | MB_SYSTEMMODAL
            except:
                pass
        
        try:
            # Test configuration loading
            logging.info("Loading configuration...")
            config = ConfigLoader.load()
            logging.info(f"Loaded configuration with {len(config.connections)} connections")
            
            # Test SSH config parsing
            logging.info("Parsing SSH configuration...")
            host_map = SshConfigParser.parse_ssh_config()
            total_hosts = sum(len(hosts) for hosts in host_map.values())
            logging.info(f"Parsed SSH config with {total_hosts} hosts in {len(host_map)} sections")
            
            # Start tray icon
            logging.info("Initializing system tray...")
            self.tray_manager.init_tray()
            
        except Exception as e:
            error_msg = f"Error starting application: {e}"
            logging.error(error_msg, exc_info=True)
            
            # Show error message in popup if running as exe
            if getattr(sys, 'frozen', False):
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(0, 
                        f"SSH Connection Manager failed to start:\n\n{error_msg}\n\nCheck the log file at:\n{log_file}", 
                        "SSH Connection Manager - Error", 
                        0x10)  # MB_ICONERROR
                except:
                    pass
            else:
                print(error_msg)
            
            sys.exit(1)
    
    def test_connection(self, host: str) -> None:
        """
        Test SSH connection to specified host
        
        Args:
            host: SSH hostname to test
        """
        print(f"Testing connection to {host}...")
        SshLauncher.connect(host)


def main() -> None:
    """Main entry point for the application"""
    parser = argparse.ArgumentParser(
        description="SSH Connection Manager with System Tray Interface"
    )
    parser.add_argument(
        "--test-host",
        type=str,
        help="Test connection to specified SSH host"
    )
    parser.add_argument(
        "--list-hosts",
        action="store_true",
        help="List all available SSH hosts from config"
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run as daemon with system tray (default)"
    )
    
    args = parser.parse_args()
    
    app = SshConnectionApp()
    
    # Handle command line options
    if args.list_hosts:
        host_map = SshConfigParser.parse_ssh_config()
        print("Available SSH hosts:")
        for section, hosts in host_map.items():
            print(f"\n{section}:")
            for host in hosts:
                print(f"  - {host}")
    
    elif args.test_host:
        app.test_connection(args.test_host)
    
    else:
        # Default: run with system tray
        app.run()


if __name__ == "__main__":
    main()