# SSH Connection Manager

A Python application that provides a system tray interface for managing SSH connections. Automatically parses SSH configuration files and organizes connections into TEST and PROD environments.

## Features

- **System Tray Integration**: Runs in the background with a system tray icon
- **SSH Config Parsing**: Automatically reads `~/.ssh/config` and organizes hosts
- **Environment Separation**: Separates hosts into TEST and PROD sections based on comments
- **One-Click Connections**: Connect to any configured SSH host with a single click
- **Jump Host Support**: Connect through bastion/jump servers (login servers) seamlessly
- **Automated Authentication**: Automatically inputs passwords for SSH connections
- **Persistent Sessions**: Once connected to a jump host, maintains the session so you don't need to re-enter passwords for subsequent connections through the same tunnel
- **Auto-Password Input**: Automatically enters stored passwords when prompted, eliminating manual password entry for each connection
- **Automatic Database Tunnels**: Automatically creates SSH tunnels to test databases based on hostname patterns (e.g., `*it1tf*` → Finance DB, `*it1te*` → Enterprise DB)
- **Configuration Management**: YAML-based configuration with encryption support and Maven integration

## Requirements

- Python 3.8 or higher
- Windows OS (uses PowerShell for SSH connections)

## Installation

1. Clone or download this project
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Build and Development

### Development Environment Setup

Before starting, make sure you have Python 3.8+ installed.

### Installing Dependencies

```bash
# Install base dependencies
pip install -r requirements.txt

# For creating the executable, also install PyInstaller
pip install pyinstaller
```

### Local Development

**Method 1 - Quick start:**
```bash
python run.py
```

**Method 2 - Package installation:**
```bash
# Install the package in development mode
pip install -e .

# Run the command
ssh-connection
```

### Creating Executable (.exe)

The project includes automated scripts for creating the executable:

**Method 1 - Automated Script (Recommended):**
```bash
python build_release.py
```
This script:
- Automatically installs all necessary dependencies (PyInstaller, etc.)
- Creates the application icon
- Generates the executable in `dist/SSH-Connection-Manager.exe`
- Configures Windows auto-startup

**Method 2 - Manual PyInstaller:**
```bash
# Using the existing .spec file
pyinstaller SSH-Connection-Manager.spec

# Or direct command
pyinstaller --onefile --windowed --name SSH-Connection-Manager --icon resources/icon.ico run.py
```

**Generated Files:**
- `dist/SSH-Connection-Manager.exe` - The final executable
- `build/` - Temporary build files (can be deleted)

## Usage

### Running the Application

### SSH Configuration

The application reads your SSH configuration from `~/.ssh/config`. Here's how to set up a complete configuration:

#### Global Settings (Common Configuration)

Add these global settings at the top of your `~/.ssh/config` file:

```ssh
# Common configuration for all hosts
Host *
    Ciphers aes128-ctr
    MACs hmac-sha2-256
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

#### Automatic Database Tunnels

Define database tunnels using hostname patterns. These will be automatically applied:

```ssh
# DB - Test - Finance	
Host *it1tf*
    LocalForward 1524 fdb02x:1524	

# DB - Test - Enterprise
Host *it1te*
    LocalForward 1523 db01x:1523

# DB - Prod - Finance
Host *it1pf*
    LocalForward 31524 fdb04x:1524

# DB - Prod - Enterprise
Host *it1pe*
    LocalForward 31523 db03x:1523
```

#### Environment Sections

Organize your hosts into TEST and PROD sections using section headers:

```ssh
############################################
#                TEST                      #
############################################

Host login_test
    HostName 10.180.22.2
    LocalForward 2222 stlit1tf01:22
    LocalForward 2223 sellait1tf02:22
    LocalForward 2224 stlit1te01:22
    LocalForward 2225 sellait1tf01:22

# Settlement - Finance - Test
Host stlit1tf01
    HostName localhost
    Port 2222
    LocalForward 3050 localhost:3050
    LocalForward 3007 localhost:3007

############################################
#                PROD                      #
############################################

Host login_prod
    HostName 10.101.22.12
    LocalForward 3222 stlit1pf01:22
    LocalForward 3223 stlit1pe01:22
    LocalForward 3224 bknit1pf01:22

Host stlit1pf01
    HostName localhost
    Port 3222
    LocalForward 3073 localhost:3073
```

#### Configuration Structure

- **Jump Hosts**: `login_test` and `login_prod` are your bastion servers
- **Port Forwarding**: Use `LocalForward` to create tunnels to target machines
- **Naming Convention**: Use descriptive names with environment indicators (tf=test-finance, te=test-enterprise, pf=prod-finance, pe=prod-enterprise)
- **Port Ranges**: Test environment uses 2xxx ports, Production uses 3xxx ports
- **Database Patterns**: Hosts matching `*it1tf*`, `*it1te*`, etc. automatically get database tunnels

### Configuration

The application supports multiple credential sources:

#### 1. Maven Settings (Recommended)

Place your credentials in `~/.m2/settings.xml`:

```xml
<settings>
  <servers>
    <server>
      <id>server-id</id> <!--  is optional not used by this app -->
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
