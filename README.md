# SSH Connection Manager

A Python application that provides a system tray interface for managing SSH connections. Automatically parses SSH configuration files and organizes connections into TEST and PROD environments.

## Features

- **System Tray Integration**: Runs in the background with a system tray icon
- **SSH Config Parsing**: Automatically reads `~/.ssh/config` and organizes hosts
- **Environment Separation**: Separates hosts into TEST and PROD sections based on comments
- **One-Click Connections**: Connect to any configured SSH host with a single click
- **Automated Authentication**: Automatically inputs passwords for SSH connections
- **Configuration Management**: YAML-based configuration with encryption support and Maven integration

## Requirements

- Python 3.8 or higher
- Windows OS (uses PowerShell for SSH connections)

## Installation

1. Clone or download this project
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

## Usage

### Running the Application

**Quick start:**
```bash
python run.py
```

**Or using batch file:**
```bash
run.bat
```

**Command line options:**
```bash
python -m ssh_connection.main --help
python -m ssh_connection.main --list-hosts
python -m ssh_connection.main --test-host hostname
```

### SSH Configuration

The application reads your SSH configuration from `~/.ssh/config` and looks for section comments:

```
############################################
#                TEST                      #
############################################

Host test-server1
    HostName 192.168.1.10
    User myuser

Host test-server2
    HostName 192.168.1.11
    User myuser

############################################
#                PROD                      #
############################################

Host prod-server1
    HostName 10.0.1.10
    User myuser
```

### Configuration

The application supports multiple credential sources:

#### 1. Maven Settings (Recommended)

Place your credentials in `~/.m2/settings.xml`:

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

#### 2. YAML Configuration File

The application also uses `resources/config.yml` for additional configuration:

```yaml
encryptedUser: "base64-encoded-encrypted-username"  # Optional if using Maven
connections:
  - name: "Test"
    loginServer: "login-test"
    destServer: "server-test"
```

**Note**: When Maven credentials are available, they take precedence and you can remove user configuration from your SSH config.

## Project Structure

```
ssh-connection/
├── src/ssh_connection/
│   ├── config/          # Configuration management
│   ├── security/        # Cryptographic utilities
│   ├── ssh/            # SSH parsing and launching
│   ├── gui/            # System tray interface
│   └── main.py         # Main application entry point
├── resources/          # Configuration files
├── tests/             # Test files
├── requirements.txt   # Python dependencies
└── setup.py          # Package setup
```

## Components

- **SshConfigParser**: Parses SSH config files and organizes hosts by environment
- **SshLauncher**: Launches SSH connections with automated credential input
- **CryptoUtil**: Provides encryption/decryption for sensitive configuration data
- **ConfigLoader**: Loads and manages YAML configuration files and Maven credentials
- **TrayIconManager**: Creates and manages the system tray interface

## Security Notes

- **Credential Sources**: Supports both Maven settings and encrypted YAML configuration
- **Maven Integration**: Reads credentials from `~/.m2/settings.xml` (plaintext storage)
- **Encrypted Storage**: Alternative encrypted storage using computer-name-based keys
- **SSH Keys**: SSH connections use your existing SSH configuration and keys
- **Automatic Input**: Passwords are automatically input via GUI automation

## License

This project is a Python port of the original Java DevAgent project.