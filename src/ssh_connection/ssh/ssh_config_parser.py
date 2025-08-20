import os
from pathlib import Path
from typing import Dict, List


class SshConfigParser:
    """Parser for SSH configuration files that organizes hosts into TEST and PROD sections"""
    
    @staticmethod
    def parse_ssh_config() -> Dict[str, List[str]]:
        """
        Parse SSH config file and extract hosts organized by TEST/PROD sections
        
        Returns:
            Dict mapping section names (TEST, PROD) to lists of hostnames
        """
        host_map = {"TEST": [], "PROD": []}
        current_section = None
        
        ssh_config_path = Path.home() / ".ssh" / "config"
        
        try:
            with open(ssh_config_path, 'r', encoding='utf-8') as file:
                for line in file:
                    line = line.strip()
                    
                    # Handle comment lines for section detection
                    if line.startswith("#"):
                        line_upper = line.upper()
                        if "TEST" in line_upper:
                            current_section = "TEST"
                        elif "PROD" in line_upper:
                            current_section = "PROD"
                    
                    # Handle Host lines
                    elif line.lower().startswith("host "):
                        parts = line.split()
                        if len(parts) > 1:
                            for i in range(1, len(parts)):
                                host = parts[i]
                                # Skip wildcard hosts and add to current section if defined
                                if host != "*" and "*" not in host and current_section:
                                    host_map[current_section].append(host)
        
        except FileNotFoundError:
            print(f"SSH config file not found: {ssh_config_path}")
        except Exception as e:
            print(f"Error parsing SSH config: {e}")
        
        return host_map


if __name__ == "__main__":
    host_map = SshConfigParser.parse_ssh_config()
    print(host_map)