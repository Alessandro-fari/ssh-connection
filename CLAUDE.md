# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

**Run the application:**
```bash
python run.py
# or
run.bat
```

**Run with command line options:**
```bash
python -m ssh_connection.main --list-hosts
python -m ssh_connection.main --test-host hostname
python -m ssh_connection.main --help
```

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Install in development mode:**
```bash
pip install -e .
```

**Run tests:**
```bash
python tests/test_basic.py
```

## Architecture Overview

This is a Windows-based SSH connection manager with system tray integration. The application parses SSH config files and provides a GUI interface for launching SSH connections with automated authentication.

**Core Components:**

- **SshConfigParser** (`src/ssh_connection/ssh/ssh_config_parser.py`): Parses `~/.ssh/config` and organizes hosts into TEST and PROD sections based on comment blocks
- **SshLauncher** (`src/ssh_connection/ssh/ssh_launcher.py`): Launches PowerShell SSH connections with automated credential input
- **TrayIconManager** (`src/ssh_connection/gui/tray_icon_manager.py`): Creates system tray interface with dynamic menus
- **ConfigLoader** (`src/ssh_connection/config/config_loader.py`): Loads YAML configuration from `resources/config.yml` and Maven credentials from `~/.m2/settings.xml`
- **CryptoUtil** (`src/ssh_connection/security/crypto_util.py`): Provides encryption/decryption for sensitive config data

**Key Architecture Patterns:**

1. **SSH Config Parsing**: The parser looks for comment blocks like `# TEST #` and `# PROD #` to categorize hosts into environments
2. **Credential Management**: Supports both encrypted credentials in `resources/config.yml` and Maven credentials from `~/.m2/settings.xml`
3. **Windows Integration**: Uses PowerShell for SSH connections and pyautogui for automated credential input
4. **System Tray UI**: Creates dynamic menus based on parsed SSH configurations

**Data Flow:**
1. Application starts → loads YAML config and Maven credentials → parses SSH config file
2. Creates system tray icon with menus organized by TEST/PROD sections
3. User clicks menu item → SshLauncher opens PowerShell SSH connection
4. Automated credential input using pyautogui with credentials from Maven settings

## Project Structure

```
src/ssh_connection/
├── main.py              # Application entry point and CLI handling
├── config/              # Configuration management
│   ├── config_loader.py # YAML config loading and Connection data classes
├── security/            # Cryptographic utilities  
│   ├── crypto_util.py   # Encryption/decryption using computer name as key
├── ssh/                 # SSH functionality
│   ├── ssh_config_parser.py # Parses ~/.ssh/config into TEST/PROD sections
│   ├── ssh_launcher.py      # PowerShell SSH connection launcher
└── gui/                 # System tray interface
    └── tray_icon_manager.py # Creates and manages system tray menus
```

## Security Considerations

- The application handles SSH credentials and uses automated input
- Supports dual credential sources: encrypted config file and Maven settings
- Maven credentials are read from `~/.m2/settings.xml` (username/password in plaintext)
- Encryption keys are derived from the computer name for config file credentials
- SSH connections rely on existing SSH configuration and key files

## Credential Configuration

The application supports two methods for credential management:

1. **Maven Settings (Preferred)**: Place credentials in `~/.m2/settings.xml`:
   ```xml
   <settings>
     <servers>
       <server>
         <id>server-id</id>
         <username>your-username</username>
         <password>your-password</password>
       </server>
     </servers>
   </settings>
   ```

2. **Encrypted Config**: Legacy support for encrypted credentials in `resources/config.yml`

If Maven credentials are available, they take precedence. This allows you to remove the `Host *` user configuration from your SSH config file.