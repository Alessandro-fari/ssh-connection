#!/usr/bin/env python3
"""
Build release executable and set up auto-startup
"""

import os
import sys
import subprocess
from pathlib import Path

# Define fallback Python path
FALLBACK_PYTHON = r"C:\Users\a.farina\AppData\Local\Programs\Python\Python313\python.exe"

def get_python_executable():
    """Get the Python executable path"""
    current_python = sys.executable
    if current_python and os.path.exists(current_python):
        return current_python
    
    if os.path.exists(FALLBACK_PYTHON):
        print(f"Using fallback Python: {FALLBACK_PYTHON}")
        return FALLBACK_PYTHON
    
    return 'python'

def install_dependencies():
    """Install required dependencies"""
    python_cmd = get_python_executable()
    
    dependencies = ["pyinstaller", "pywin32", "pystray", "pillow", "pyyaml", "cryptography", "pyautogui", "psutil"]
    
    print("Installing dependencies...")
    for dep in dependencies:
        try:
            subprocess.run([python_cmd, "-m", "pip", "install", dep], check=True, capture_output=True)
            print(f"✓ {dep}")
        except subprocess.CalledProcessError:
            print(f"✗ Failed to install {dep}")

def create_icon():
    """Create app icon"""
    try:
        from PIL import Image, ImageDraw
        
        resources_path = Path("resources")
        resources_path.mkdir(exist_ok=True)
        
        img = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rectangle([4, 6, 28, 26], fill=(0, 100, 200), outline=(0, 50, 150))
        draw.rectangle([6, 8, 26, 12], fill=(255, 255, 255))
        draw.text((8, 14), "SSH", fill=(255, 255, 255))
        
        icon_path = resources_path / "icon.ico"
        img.save(icon_path, format='ICO')
        return icon_path
    except:
        return None

def build_exe():
    """Build release executable"""
    python_cmd = get_python_executable()
    
    print("Building release executable...")
    
    create_icon()
    src_path = Path("src").absolute()
    resources_path = Path("resources").absolute()
    
    # PyInstaller needs platform-specific separator for --add-data
    # Windows uses ';', Unix uses ':'
    separator = ';' if os.name == 'nt' else ':'
    
    cmd = [
        python_cmd, "-m", "PyInstaller",
        "--onefile", "--windowed",  # No console window
        "--name", "SSH-Connection-Manager",
        "--distpath", "dist",
        "--workpath", "build",
        "--paths", str(src_path),
        "--add-data", f"{resources_path}{separator}resources",
        "--hiddenimport", "ssh_connection",
        "--hiddenimport", "ssh_connection.main",
        "--hiddenimport", "ssh_connection.gui.tray_icon_manager",
        "--hiddenimport", "ssh_connection.ssh.ssh_config_parser",
        "--hiddenimport", "ssh_connection.ssh.ssh_launcher",
        "--hiddenimport", "ssh_connection.config.config_loader",
        "--hiddenimport", "ssh_connection.security.crypto_util",
        "--hiddenimport", "pystray._win32",  # Fix for pystray on Windows
        "--hiddenimport", "PIL._tkinter_finder",  # Fix for Pillow
        "--icon", "resources/icon.ico",
        "--clean",
        "--noconfirm",  # Don't ask for confirmation
        "run.py"
    ]
    
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        exe_path = Path("dist/SSH-Connection-Manager.exe")
        if exe_path.exists():
            print(f"✓ Executable created: {exe_path}")
            return exe_path
    
    print("✗ Build failed!")
    return None

def create_startup_shortcut(exe_path):
    """Create startup shortcut"""
    try:
        import win32com.client
        
        startup_folder = Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
        shortcut_path = startup_folder / "SSH Connection Manager.lnk"
        
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(shortcut_path))
        shortcut.Targetpath = str(exe_path.absolute())
        shortcut.WorkingDirectory = str(exe_path.parent.absolute())
        shortcut.IconLocation = str(exe_path.absolute())
        shortcut.Description = "SSH Connection Manager - System Tray SSH Tool"
        shortcut.save()
        
        print(f"✓ Auto-startup configured: {shortcut_path}")
        return True
    except Exception as e:
        print(f"✗ Could not create startup shortcut: {e}")
        return False

def main():
    """Main build process"""
    print("SSH Connection Manager - Release Builder")
    print("=" * 50)
    
    if not Path("src/ssh_connection").exists():
        print("Error: Run from project root directory")
        sys.exit(1)
    
    try:
        install_dependencies()
        
        exe_path = build_exe()
        if not exe_path:
            sys.exit(1)
        
        print("\nSetting up auto-startup...")
        create_startup_shortcut(exe_path)
        
        print("\n" + "=" * 50)
        print("✓ Release build completed!")
        print(f"Executable: {exe_path}")
        print("- Runs silently in system tray")
        print("- Starts automatically with Windows")
        print("- Look for SSH icon in system tray")
        
    except Exception as e:
        print(f"Build failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()