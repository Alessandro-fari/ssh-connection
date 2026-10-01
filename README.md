# SSH Connection Manager

A Python application that provides a system tray interface for managing SSH connections. Automatically parses SSH configuration files and organizes connections into TEST and PROD environments.

## Features

- **System Tray Integration**: Runs in the background with a system tray icon
- **SSH Config Parsing**: Automatically reads `~/.ssh/config` and organizes hosts
- **Environment Separation**: Separates hosts into TEST and PROD sections based on comments; within each section the jump host comes first and the other hosts are listed alphabetically
- **One-Click Connections**: Connect to any configured SSH host with a single click
- **Host Search (Ctrl+Shift+Space, configurable)**: A global hotkey opens a search popup (pre-loaded at startup, so it appears instantly) to find and connect to any host by typing — arrow keys navigate (skipping the TEST/PROD separators), Enter connects, Esc cancels. A Tutti/TEST/PROD combo narrows the scope and the last choice is remembered for next time. Each TEST/PROD submenu also has a **"Cerca..."** entry that opens the same dialog pre-filtered to that environment.
- **Favorites & Recents**: favorite hosts sit at the top of the tray menu (with their live status icon), followed by a "Recenti" submenu with the last 5 hosts. The search popup opens on the same "★ Preferiti" / "Recenti" sections; press **Ctrl+D** on a host to add or remove it from the favorites
- **Settings dialog** ("Impostazioni..." in the tray menu, Windows 11 style with a navigation pane): search shortcut, start with Windows, keepalive interval, tunnel wait timeout, dark theme (One Half Dark, as in Windows Terminal), notification switches, favorites, version and paths. Preferences live in `~/.ssh_connection_prefs.json`
- **Edit hosts and credentials without opening files**: *Impostazioni → Host* lists the hosts of `~/.ssh/config`; *Aggiungi host* creates the tunnel on the jump host (first free local port) and the `Host` entry, *Modifica* / *Elimina* update or remove both; each host also has its list of extra **forwarded ports** (the `LocalForward` lines of its own block — DB, HSM, JMX… — with the comment above as description), which can be added, edited and removed (a port already used by another host is allowed with a warning, one that ssh could not open — a jump host tunnel or a wildcard block of the same host — is refused); the jump host addresses are editable too. Only the lines involved change (comments, tabs and the DB tunnel blocks stay as they are) and a backup is written to `~/.ssh/config.bak`. *Impostazioni → Utente e password* edits the username and password stored in `~/.m2/settings.xml` (first `<server>`, the rest of the file is left alone). Everything is written only when you press **Salva**
- **Recognisable PROD consoles**: every terminal is titled `[TEST] host` / `[PROD] host`; PROD terminals use the Windows Terminal *Ubuntu-ColorScheme* colours and open with a `PRODUZIONE - host` banner. The scheme is chosen in *Impostazioni → Generale* ("Nessuno", built-in schemes or the ones defined in Windows Terminal)
- **File browser and search ("Cerca file...", or Ctrl+F on a host in the search popup)**: browse the folders of a host like WinSCP (double-click to enter, `..`/Backspace/▲ to go up, ◀ back, ⌂ home, editable path with history; symbolic links followed, logical paths kept). Typing in "Nome" filters the current folder; Enter/"Cerca" finds files by name (text or wildcards) and optionally by content from the current folder, with or without subfolders, and "Vai alla cartella" jumps to a result's folder. A host's folder is listed automatically only when its tunnel is already up. The host panel works like the search popup: type to filter, Tutti/TEST/PROD combo, ★ Preferiti and Recenti sections, arrows to pick, Ctrl+D to toggle a favorite. Double-click / Enter opens the file in your text editor from a temporary copy that is deleted when the editor closes (read-only, like WinSCP's open, but edits are never uploaded); "Scarica..." saves it, "Copia percorso" copies the remote path. Folders are remembered per host. Uses the same jump host/tunnel as the terminals; jump hosts themselves are not supported (2FA token)
- **Choose the text editor ("Apri con")**: the "Apri" buttons of the settings (SSH config, credentials, preferences, log) and the files opened from "Cerca file" use one text editor. Until one is saved, a chooser lists the editors found (Notepad, the Windows default for `.txt` and `.log` — e.g. Notepad++, klogg — VS Code…, or *Sfoglia...* for any .exe) with **Solo questa volta** / **Sempre**; *Impostazioni → Generale → Editor dei file di testo* changes it or goes back to "Chiedi ogni volta"
- **Session notifications**: tray balloons when a session drops unexpectedly (network/VPN loss, including hidden Init sessions), when a LocalForward tunnel port stops listening or never comes up (e.g. port already in use), and when the keepalive `watch` could not be started. Each kind can be turned off in the settings
- **Init retry**: if an Init fails (wrong token, VPN down) the failed login is cleaned up automatically, so you can simply run Init again without restarting the application
- **Jump Host Support**: Connect through bastion/jump servers (login servers) seamlessly
- **Targeted Password Input**: Injects the password directly into the input buffer of the SSH terminal it opened (Windows console APIs), as soon as the password prompt appears — it never types into another window, even if you switch focus. Host-key confirmation prompts (`yes/no`) are answered automatically
- **Live Connection Status**: menu items show the connection state per host — TEST uses circles (⚪ idle / 🟢 connected), PROD uses squares (⬜ idle / 🟩 connected) — and the tray icon turns green with a counter while connections are open
- **Automatic Jump Host Startup**: connecting to a machine whose jump host (`login_test` / `login_prod`) is not open yet launches the jump host first, waits for its tunnels, then opens the machine
- **Config Hot-Reload**: edits to `~/.ssh/config` (new machines, new forwarded ports) are picked up automatically — no application restart needed; changes apply to the next tunnel you open
- **Automatic Database Tunnels**: Automatically creates SSH tunnels to test databases based on hostname patterns (e.g., `*it1tf*` → Finance DB, `*it1te*` → Enterprise DB)
- **Configuration Management**: YAML-based configuration with encryption support and Maven integration

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — internal architecture and how the application works
- [CHANGELOG.md](CHANGELOG.md) — history of changes, improvements and notes (**must be updated with every change**)

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


### Local Development

**Method 1 - Quick start:**
```bash
python run.py
```


### Creating Executable (.exe)

The project includes automated scripts for creating the executable:

**Automated Script:**
```bash
python build_release.py
```
This script:
- Automatically installs all necessary dependencies (PyInstaller, etc.)
- Creates the application icon
- Generates the executable in `dist/SSH-Connection-Manager.exe`
- Configures Windows auto-startup


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

Tip: *Impostazioni... → Info → "Apri utente e password"* opens this file in your text editor and creates it from a template if it does not exist yet.

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
