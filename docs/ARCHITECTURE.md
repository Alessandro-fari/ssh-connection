# Architettura — SSH Connection Manager

Documento di riferimento sull'architettura interna e sul funzionamento dell'applicazione.
Per funzionalità, installazione e configurazione utente vedere il [README](../README.md).
Per la cronologia delle modifiche vedere il [CHANGELOG](../CHANGELOG.md).

> **Regola**: ogni modifica al codice va registrata nel `CHANGELOG.md`; se cambia
> l'architettura (nuovi moduli, nuovi flussi, cambi di responsabilità), va aggiornato
> anche questo documento.

## Panoramica

Applicazione Python per Windows che vive nella system tray e gestisce connessioni SSH
verso ambienti TEST e PROD, con jump host (bastion), tunnel automatici, inserimento
mirato della password nel terminale e stato live delle connessioni.

```
                    ┌─────────────────────────┐
                    │        main.py          │  entry point, logging,
                    │    SshConnectionApp     │  CLI (--test-host, --list-hosts)
                    └───────────┬─────────────┘
                                │
                ┌───────────────┴────────────────┐
                │      gui/tray_icon_manager     │  icona tray + menu dinamico
                │  (pystray + refresh loop 2s)   │  hot-reload di ~/.ssh/config
                └──┬─────────┬──────────┬────────┘
                   │         │          │
     ┌─────────────┴──┐ ┌────┴────────┐ │  ┌──────────────────────┐
     │ gui/           │ │ gui/        │ │  │ ssh/ssh_launcher     │
     │ win32_menu_    │ │ hotkey_     │ │  │  (jump host auto,    │
     │ bitmaps        │ │ manager     │ │  │   attesa tunnel,     │
     │ (icone native  │ │ (Ctrl+Shift │ │  │   keepalive)         │
     │  HMENU)        │ │  +Space)    │ │  └───┬────────┬─────────┘
     └────────────────┘ └──────┬──────┘ │      │        │
                               │        │ ┌────┴────┐ ┌─┴──────────────┐
                               ▼        │ │ ssh/     │ │ config/        │
                    ┌──────────────────┐│ │ connec-  │ │ config_loader  │
                    │ gui/search_dialog││ │ tion_    │ │ (+ security/   │
                    │ (WinForms in PS  ││ │ tracker  │ │  crypto_util)  │
                    │  processo separ.)││ │ (psutil) │ └────────────────┘
                    └────────┬─────────┘│ └──────────┘
                             │          │
                             └──────────┼──→ SshLauncher.connect (riusa)
                                        │
                          ┌─────────────┴────────────┐
                          │ ssh/ssh_config_parser    │  parsing ~/.ssh/config
                          │                          │  sezioni TEST / PROD
                          └──────────────────────────┘
```

## Moduli

### `main.py` — entry point
- Classe `SshConnectionApp`: configura il logging su `~/ssh_connection_debug.log`,
  nasconde la console quando gira come exe (PyInstaller, `sys.frozen`), mostra
  notifiche/errori con `MessageBoxW`, carica la configurazione e avvia la tray.
- CLI: `--list-hosts` (elenca gli host per sezione), `--test-host <nome>` (test di
  connessione), default = daemon con system tray.

### `ssh/ssh_config_parser.py` — parsing di `~/.ssh/config`
- Legge il file e classifica gli host in sezioni **TEST** e **PROD** in base ai
  commenti di intestazione (`# ... TEST ...` / `# ... PROD ...`).
- Ignora gli host wildcard (`*`). Nessuna cache: viene ri-parsato a ogni richiesta,
  ed è questo che rende possibile l'hot-reload della configurazione.

### `ssh/ssh_launcher.py` — apertura delle connessioni
Flusso di `connect(name)` (eseguito in un thread in background per non bloccare il menu):
1. **Jump host automatico**: determina il jump host (`login_*`) della sezione a cui
   appartiene l'host; se non risulta attivo nel tracker (e il tunnel non è già aperto
   da un'altra istanza, verificato con un probe TCP sulla porta locale), lo lancia prima.
2. **Attesa tunnel**: se l'host passa per un tunnel `LocalForward` del jump host, fa
   polling sulla porta locale finché accetta connessioni (timeout 120 s, generoso per
   permettere l'inserimento manuale del token 2FA).
3. **Lancio**: apre un nuovo terminale con `powershell -NoExit -Command ssh user@host`
   (`CREATE_NEW_CONSOLE`), così il PID del processo che possiede la console è noto —
   requisito sia per l'iniezione mirata della password sia per il tracking dello stato.
   Usa preferibilmente l'OpenSSH di Windows (`C:\Windows\System32\OpenSSH\ssh.exe`)
   perché risolve `~/.ssh/config` via `%USERPROFILE%` (l'ssh di Git usa `HOME`, che in
   questo ambiente punta altrove).
4. **Password**: delega a `ConsoleInjector.inject_password(pid, password)`.
5. **Keepalive**: sui jump host, dopo il login (incluso il token 2FA) invia
   `watch -n 240 date` al prompt della shell per tenere vivi sessione e tunnel.

`_launch(name, ...)` è parametrico e riusato sia dai click normali sia dal flusso Init:
- `hidden=True` apre la console con finestra nascosta (`STARTUPINFO` con `SW_HIDE`, più
  `ConsoleInjector.hide_console_window` come rete di sicurezza quando il terminale
  predefinito è Windows Terminal, che ignora `wShowWindow`). Resta una console reale,
  quindi l'iniezione via `AttachConsole` continua a funzionare.
- `secrets` è la sequenza di segreti da iniettare (default `[password]`; i login di Init
  usano `[password, token]`); `register=False` salta il tracker (host invisibili nel
  menu); `keepalive` forza l'invio del comando keepalive.
- `launch_for_init(name, secrets, register)` è il wrapper usato dall'orchestratore Init.

### `ssh/init_orchestrator.py` — flusso "Init" (per ambiente)
Orchestrazione delle voci di menu **Init TEST** e **Init PROD**: ogni voce, dal proprio
token 2FA, apre un ambiente. Config in costante `INIT_ENVS = {TEST: {login, targets}, PROD: {...}}`
(login = jump host; targets = i due `stli*` settlement/DB dell'ambiente).
1. `start(env, notify)` avvia un thread worker con single-flight **per ambiente**
   (`_running` è un set di ambienti in corso).
2. Chiede il token in una dialog **Windows Forms eseguita come processo PowerShell
   separato** (STA): ha un proprio message loop (non interferisce con la tray), si forza
   in primo piano (`SetForegroundWindow`/`BringWindowToTop`) e mostra titolo/host
   dell'ambiente. Stampa il token su stdout su OK, niente su Annulla.
3. Apre il jump host `login_*` iniettando `[password, token]` (`inject_secrets` con gating
   posizionale del cursore per distinguere prompt password → prompt token). Registrato nel
   tracker (menu lo mostra attivo, i click normali riusano i suoi tunnel).
4. Attende i tunnel e apre i due host `stli*` iniettando solo la password, con
   `register=False` → invisibili nel menu. Tutte le console sono **nascoste** e ricevono
   il keepalive. Notifica finale via balloon tray.
- **Perché due bottoni**: un singolo token SecurID autentica un solo login (il server
  rifiuta il riuso: *"Invalid username or password"* in sequenza, *"Session not started
  or timedout"* in contemporanea). Serve un token fresco per ambiente.
- **Deadlock evitato**: `_lock` è un `RLock` — `start()` lo tiene e chiama `_live_procs()`
  che lo riacquisisce; con un `Lock` semplice si bloccava il thread principale della tray.
- `InitOrchestrator` tiene la lista dei PID spawnati per terminarli all'uscita dell'app
  (`shutdown()` da `quit_application`), dato che le console nascoste non hanno finestra.

### `ssh/console_injector.py` — iniezione mirata nella console (Win32)
Il componente più delicato. Scrive il testo **direttamente nel buffer di input della
console del PID target** (`AttachConsole` + `WriteConsoleInputW`), quindi la password
finisce solo in quel terminale, indipendentemente dal focus:
- Legge lo screen buffer (`ReadConsoleOutputCharacterW`) per attendere il vero prompt
  (`password/passphrase/passcode`) invece di dormire un tempo fisso; risponde `yes`
  automaticamente al prompt host-key (`yes/no`).
- `inject_secrets(pid, [s1, s2, ...])` risponde a una sequenza di prompt, un segreto per
  prompt (es. password poi token 2FA); `inject_password` ne è il caso a un solo segreto.
  I prompt successivi al primo sono riconosciuti richiedendo che il cursore sia avanzato
  a una **nuova riga** rispetto al prompt precedente (gate posizionale), così lo stesso
  prompt non viene mai risposto due volte anche se il testo del prompt 2FA è variabile.
- L'attach alla console è stato **process-wide**: un lock globale serializza le
  iniezioni; l'attesa del prompt shell per il keepalive attacca solo a brevi raffiche
  per non bloccare iniezioni concorrenti.
- Dopo ogni operazione ri-attacca la console originale (via PID di un processo fratello).

### `ssh/connection_tracker.py` — stato delle connessioni
- Registro `host -> [(pid, create_time)]` dei processi PowerShell che ospitano le
  sessioni ssh. Una connessione è attiva finché il processo è vivo (psutil).
- Il `create_time` protegge dal riuso dei PID. Istanza condivisa `tracker` usata da
  launcher e tray.

### `gui/tray_icon_manager.py` — interfaccia tray
- Icona pystray: blu quando idle, verde con badge contatore quando ci sono connessioni.
- **Menu dinamico**: a ogni rebuild ri-parsa `~/.ssh/config` e legge lo stato dal
  tracker → hot-reload della config senza riavvio. Ogni sottomenu TEST/PROD ha ora
  una voce **"Cerca..."** in cima (senza bitmap di stato) che apre il `SearchPopup`
  pre-filtrato a quell'ambiente. La voce di primo livello **"Cerca host..."**
  espone la scorciatoia: il testo contiene un `	` e Windows allinea a destra
  in grigio ciò che segue (hint acceleratore nativo). pystray passa `text`
  dritto a `MENUITEMINFO.dwTypeData`, quindi nessuna chiamata Win32 aggiuntiva;
  il label sta in `HotkeyManager.SHORTCUT_LABEL`, accanto alla `RegisterHotKey`,
  per non divergere dai tasti registrati.
- **Ricerca host**: il `SearchPopup` è costruito **una sola volta** in
  `init_tray` (`_ensure_search_popup`, finestra nascosta) e riusato per sempre;
  `_open_search(initial_env)` si limita a mettere una richiesta sulla coda del
  thread Tk e ritorna subito, quindi né la tray né l'hotkey si bloccano mai.
  `host_provider=_search_hosts` ri-parsa `~/.ssh/config` a ogni apertura
  (hot-reload). Alla conferma, `_on_search_selected` lancia
  `connect_to_host(host)` su un thread separato — bloccante, non deve girare
  sul thread Tk — riusando jump host automatico, attesa tunnel, iniezione
  password e keepalive: nessuna logica di lancio nuova.
- **Hotkey globale**: `HotkeyManager` avviato in `init_tray`, fermato in
  `quit_application`/`stop` (che chiudono anche il popup). Il callback
  `_on_global_hotkey` mostra il popup con l'ambiente salvato nelle preferenze.
- **Refresh loop** (2 s): ridisegna icona e menu solo quando cambia lo stato
  (mtime del config + set degli host attivi); non tocca mai il menu mentre è aperto
  (rilevato via `GetGUIThreadInfo`, per evitare flicker).
- Linguaggio visivo: TEST = cerchi (bianco freddo idle / verde connesso),
  PROD = quadrati (ambra idle / verde connesso).

### `gui/hotkey_manager.py` — hotkey globale (Win32)
Registra **Ctrl+Shift+Space** come hotkey globale e notifica la tray quando
viene premuto. pystray possiede il message loop della tray sul thread principale
e non espone hook, quindi l'hotkey gira su un **thread dedicato** con il suo
message loop Win32 (`GetMessage`):
- `RegisterHotKey(NULL, id, MOD_CONTROL|MOD_SHIFT|MOD_NOREPEAT, VK_SPACE)`:
  handle NULL = Windows instrada `WM_HOTKEY` al thread che ha registrato, anche
  in background. `MOD_NOREPEAT` evita la ripetizione se l'utente tiene premuto.
- Loop `GetMessage`: su `WM_HOTKEY` invoca il callback (sul thread hotkey);
  `PostThreadMessageW(WM_USER_STOP)` da `stop()` lo interrompe.
- `UnregisterHotKey` in uscita. Se la registrazione fallisce (es. altro app
  ha già quel binding, errore 1409) lo logga e non va in crash.

### `gui/search_dialog.py` — popup di ricerca host
Popup **tkinter pre-caricato su un thread dedicato** (`SearchPopup`).

*Perché non più WinForms/PowerShell*: la prima versione lanciava un processo
PowerShell STA con `Add-Type -AssemblyName System.Windows.Forms`. Avvio di
`powershell.exe` + caricamento degli assembly = **~1.5-3 s a ogni pressione
dell'hotkey**, inaccettabile per un popup "digita e salta all'host"; in più la
`ListBox` owner-drawn si rompeva perché PowerShell spacchettava le hashtable
delle righe diversamente dal previsto (il dialog di fatto non funzionava).

- **Pre-warm**: un unico `Tk()` nascosto viene creato all'avvio dell'app sul
  thread `SearchPopup` con il suo `mainloop`. Aprire = `deiconify()` +
  `SetForegroundWindow()`: **istantaneo**, nessun processo, nessun assembly,
  nessun widget da costruire. Chiudere fa solo `withdraw()`, così la seconda
  apertura è veloce quanto la prima.
- **Threading**: Tcl non è thread-safe, quindi solo il thread del popup tocca
  Tk. Gli altri thread (menu tray, thread hotkey Win32) mettono la richiesta in
  una `queue.Queue` drenata da un poll `after(50 ms)`. `show()`/`stop()` sono
  quindi chiamabili da qualsiasi thread e ritornano subito.
- **Foreground**: un processo non in foreground non può alzare una finestra.
  Sul percorso hotkey Windows concede il diritto a chi riceve `WM_HOTKEY`; per
  il percorso menu si usa comunque il trucco `AttachThreadInput` prima di
  `SetForegroundWindow` (helper `_force_foreground`).
- **UI**: banda header blu + `Entry` filtro + `ttk.Combobox` Tutti/TEST/PROD +
  `Listbox` risultati con separatori TEST/PROD resi non selezionabili via
  `itemconfig` (sfondo/selezione identici) e saltati dalla navigazione + riga
  di stato con il conteggio host e i tasti disponibili.
- **Filtro**: sottostringa case-insensitive, riapplicata a ogni keystroke
  (`trace_add("write")`) e a ogni cambio combo, con ordinamento per rilevanza
  (`_rank`: esatto < prefisso < confine di parola < sottostringa).
- **Tastiera**: ↑/↓ (e PagSu/PagGiù, 10 righe) saltano i separatori via
  `_next_host`, `Invio` connette, `Esc` chiude; i binding stanno su root,
  entry, listbox e combo, così funzionano senza mai lasciare il campo di
  ricerca. Doppio click sulla lista connette.
- **Persistenza**: `~/.ssh_connection_prefs.json` salva l'ultimo ambiente
  scelto **esplicitamente** dall'utente. Aprire "Cerca..." dal sottomenu TEST
  (env forzato) senza toccare la combo **non** sovrascrive il default: il flag
  `_env_touched` è alzato solo da `<<ComboboxSelected>>`.
- **Smoke test manuale**: `py -m ssh_connection.gui.search_dialog` apre il
  popup con una lista host fittizia.

### `gui/win32_menu_bitmaps.py` — icone colorate nel menu
Windows disegna il testo dei menu (emoji incluse) in monocromia, quindi i pallini di
stato colorati non si possono rendere via testo. Questo modulo genera bitmap ARGB con
PIL e le applica agli item del HMENU nativo di pystray (`MENUITEMINFO.hbmpItem`).
Va ri-applicato a ogni tick perché `update_menu()` ricrea l'handle del menu.

### `config/config_loader.py` — configurazione applicativa
- Carica `resources/config.yml` (con fallback multipli per lo scenario exe/PyInstaller
  via `sys._MEIPASS`).
- **Credenziali**: preferisce `~/.m2/settings.xml` (primo `<server>`: username e
  password); in alternativa `encryptedUser` nel YAML, decifrato con `CryptoUtil`.
  Il prefisso dominio (`netsgroup\`) viene rimosso dallo username.

### `security/crypto_util.py` — cifratura
AES-128 ECB con chiave derivata da `%COMPUTERNAME%` (primi 16 caratteri, padded),
compatibile con la preesistente implementazione Java. Output Base64.

## Build e distribuzione

- `build_release.py` / `build_debug.py`: script PyInstaller automatizzati; generano
  `dist/SSH-Connection-Manager.exe` (spec: `SSH-Connection-Manager.spec`) e
  configurano l'avvio automatico di Windows.
- Attenzione noto: `win32_menu_bitmaps` va incluso come hidden import nello spec,
  altrimenti le icone del menu spariscono nell'exe (fix nel commit `4cabd09`).

## Dipendenze principali

`pystray` (tray icon), `Pillow` (immagini icona/bitmap), `psutil` (tracking processi),
`PyYAML` (config), `cryptography` (AES). Le API Win32 sono usate via `ctypes`
(nessuna dipendenza pywin32).
