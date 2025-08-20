#!/usr/bin/env python3
"""
Quick launcher for SSH Connection Manager
"""

import sys
from pathlib import Path

# Add src to Python path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

from ssh_connection.main import main

if __name__ == "__main__":
    main()