#!/usr/bin/env python3
"""
Build debug executable with console window
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

def build_debug_exe():
    """Build debug executable with console"""
    python_cmd = get_python_executable()
    
    print("Building debug executable (with console)...")
    
    create_icon()
    src_path = Path("src").absolute()
    resources_path = Path("resources").absolute()
    
    cmd = [
        python_cmd, "-m", "PyInstaller",
        "--onefile",  # NO --windowed = console shows
        "--name", "SSH-Connection-Manager-DEBUG",
        "--distpath", "dist",
        "--workpath", "build",
        "--paths", str(src_path),
        "--add-data", f"{resources_path};resources",
        "--hiddenimport", "ssh_connection",
        "--hiddenimport", "ssh_connection.main",
        "--hiddenimport", "ssh_connection.gui.tray_icon_manager",
        "--hiddenimport", "ssh_connection.ssh.ssh_config_parser",
        "--hiddenimport", "ssh_connection.ssh.ssh_launcher",
        "--hiddenimport", "ssh_connection.config.config_loader",
        "--hiddenimport", "ssh_connection.security.crypto_util",
        "--icon", "resources/icon.ico",
        "--clean",
        "run.py"
    ]
    
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        exe_path = Path("dist/SSH-Connection-Manager-DEBUG.exe")
        if exe_path.exists():
            print(f"✓ Debug executable created: {exe_path}")
            return exe_path
        else:
            # Look for any debug exe
            debug_files = list(Path("dist").glob("*DEBUG*.exe"))
            if debug_files:
                print(f"✓ Debug executable created: {debug_files[0]}")
                return debug_files[0]
    
    print("✗ Build failed!")
    return None

def main():
    """Main build process"""
    print("SSH Connection Manager - Debug Builder")
    print("=" * 50)
    
    if not Path("src/ssh_connection").exists():
        print("Error: Run from project root directory")
        sys.exit(1)
    
    try:
        install_dependencies()
        
        exe_path = build_debug_exe()
        if not exe_path:
            sys.exit(1)
        
        print("\n" + "=" * 50)
        print("✓ Debug build completed!")
        print(f"Executable: {exe_path}")
        print("- Shows console window with debug messages")
        print("- Creates log file in: %USERPROFILE%\\ssh_connection_debug.log")
        print("- Shows startup notifications")
        print("- Use this to troubleshoot issues")
        
    except Exception as e:
        print(f"Build failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()