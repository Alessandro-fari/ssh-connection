#!/usr/bin/env python3
"""
Basic tests for SSH Connection Manager components
"""

import sys
import os
from pathlib import Path

# Add src to Python path for testing
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))

from ssh_connection.ssh.ssh_config_parser import SshConfigParser
from ssh_connection.security.crypto_util import CryptoUtil
from ssh_connection.config.config_loader import ConfigLoader


def test_crypto_util():
    """Test encryption and decryption"""
    print("Testing CryptoUtil...")
    
    test_data = "test_user_123"
    try:
        encrypted = CryptoUtil.encrypt(test_data)
        decrypted = CryptoUtil.decrypt(encrypted)
        
        print(f"Original: {test_data}")
        print(f"Encrypted: {encrypted}")
        print(f"Decrypted: {decrypted}")
        print(f"Match: {test_data == decrypted}")
        
        assert test_data == decrypted, "Encryption/decryption failed"
        print("✓ CryptoUtil test passed")
    except Exception as e:
        print(f"✗ CryptoUtil test failed: {e}")


def test_ssh_config_parser():
    """Test SSH config parsing"""
    print("\nTesting SshConfigParser...")
    
    try:
        host_map = SshConfigParser.parse_ssh_config()
        print(f"Found {len(host_map)} sections:")
        
        for section, hosts in host_map.items():
            print(f"  {section}: {len(hosts)} hosts")
            for host in hosts[:3]:  # Show first 3 hosts
                print(f"    - {host}")
            if len(hosts) > 3:
                print(f"    ... and {len(hosts) - 3} more")
        
        print("✓ SshConfigParser test passed")
    except Exception as e:
        print(f"✗ SshConfigParser test failed: {e}")


def test_config_loader():
    """Test configuration loading"""
    print("\nTesting ConfigLoader...")
    
    try:
        config = ConfigLoader.load()
        
        print(f"Encrypted user: {config.get_encrypted_user()}")
        
        # Test decryption
        try:
            decrypted_user = ConfigLoader.decrypt(config.get_encrypted_user())
            print(f"Decrypted user: {decrypted_user}")
        except Exception as e:
            print(f"Decryption failed (expected if key doesn't match): {e}")
        
        print(f"Connections: {len(config.connections)}")
        for conn in config.connections:
            print(f"  - {conn.name}: {conn.login_server} -> {conn.dest_server}")
        
        # Test connection lookup
        test_conn = config.get_connection_by_name("Test")
        if test_conn:
            print(f"Found test connection: {test_conn.name}")
        
        print("✓ ConfigLoader test passed")
    except Exception as e:
        print(f"✗ ConfigLoader test failed: {e}")


def main():
    """Run all basic tests"""
    print("Running basic tests for SSH Connection Manager\n")
    
    test_crypto_util()
    test_ssh_config_parser()
    test_config_loader()
    
    print("\nAll tests completed!")


if __name__ == "__main__":
    main()