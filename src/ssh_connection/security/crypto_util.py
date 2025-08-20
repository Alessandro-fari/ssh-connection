import os
import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend


class CryptoUtil:
    """Cryptographic utilities for encrypting and decrypting sensitive data"""
    
    @staticmethod
    def _get_key() -> bytes:
        """
        Get the encryption key based on computer name (first 16 characters)
        Mimics the Java implementation that uses COMPUTERNAME
        """
        computer_name = os.environ.get('COMPUTERNAME', 'default_computer')
        # Ensure key is exactly 16 bytes for AES-128
        key = computer_name[:16].ljust(16, '0')
        return key.encode('utf-8')
    
    @staticmethod
    def encrypt(data: str) -> str:
        """
        Encrypt a string using AES encryption
        
        Args:
            data: String to encrypt
            
        Returns:
            Base64 encoded encrypted string
        """
        try:
            key = CryptoUtil._get_key()
            
            # Create cipher with ECB mode (to match Java implementation)
            cipher = Cipher(
                algorithms.AES(key),
                modes.ECB(),
                backend=default_backend()
            )
            encryptor = cipher.encryptor()
            
            # Pad data to 16-byte boundary
            data_bytes = data.encode('utf-8')
            padding_length = 16 - (len(data_bytes) % 16)
            padded_data = data_bytes + bytes([padding_length]) * padding_length
            
            # Encrypt and encode to base64
            encrypted = encryptor.update(padded_data) + encryptor.finalize()
            return base64.b64encode(encrypted).decode('utf-8')
            
        except Exception as e:
            raise RuntimeError(f"Encryption failed: {e}")
    
    @staticmethod
    def decrypt(encrypted: str) -> str:
        """
        Decrypt a base64 encoded encrypted string
        
        Args:
            encrypted: Base64 encoded encrypted string
            
        Returns:
            Decrypted string
        """
        try:
            key = CryptoUtil._get_key()
            
            # Decode from base64
            encrypted_bytes = base64.b64decode(encrypted)
            
            # Create cipher with ECB mode
            cipher = Cipher(
                algorithms.AES(key),
                modes.ECB(),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            
            # Decrypt
            decrypted_padded = decryptor.update(encrypted_bytes) + decryptor.finalize()
            
            # Remove padding
            padding_length = decrypted_padded[-1]
            decrypted = decrypted_padded[:-padding_length]
            
            return decrypted.decode('utf-8')
            
        except Exception as e:
            raise RuntimeError(f"Decryption failed: {e}")


if __name__ == "__main__":
    # Test encryption/decryption
    test_data = "test_user"
    encrypted = CryptoUtil.encrypt(test_data)
    decrypted = CryptoUtil.decrypt(encrypted)
    print(f"Original: {test_data}")
    print(f"Encrypted: {encrypted}")
    print(f"Decrypted: {decrypted}")
    print(f"Match: {test_data == decrypted}")