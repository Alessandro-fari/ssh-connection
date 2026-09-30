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
- Restituisce ogni sezione ordinata (`sort_hosts`): jump host `login*` in cima, poi
  gli altri in ordine alfabetico senza distinguere maiuscole e minuscole, senza
  doppioni. È l'ordine di menu, popup, Impostazioni e "Cerca file".
- Ignora gli host wildcard (`*`). Nessuna cache: viene ri-parsato a ogni richiesta,
  ed è questo che rende possibile l'hot-reload della configurazione.

### `ssh/ssh_config_editor.py` — modifica di `~/.ssh/config` dalle Impostazioni
`SshConfigDocument` tiene le **righe del file così come sono** e ogni operazione tocca solo
le righe necessarie (riga `Host`, direttive `HostName`/`Port`, un `LocalForward` del jump
host), copiando l'indentazione delle righe vicine: commenti, tab, CRLF, BOM, blocchi
wildcard e direttive commentate restano intatti. Il file non viene mai rigenerato.
- **Modello** (lo stesso che usa il launcher): sezioni TEST/PROD dai commenti, con la
  stessa regola di `SshConfigParser`; jump host = primo `login*` della sezione. Un host
  *tunnel* ha `HostName localhost` + `Port P`, e sul jump host c'è
  `LocalForward P destinazione:porta`. Gli host *diretti* hanno un HostName proprio. La
  descrizione è il blocco di commenti subito sopra `Host`.
- **Blocchi**: da `Host`/`Match` al successivo, escluse righe vuote finali e commenti non
  indentati (intestazioni di sezione, note tra blocchi).
- `hosts()` → `HostEntry` (kind `jump`/`tunnel`/`direct`, `duplicate` se il nome ha più
  blocchi: ssh usa il primo, quindi non è modificabile da qui).
- `add_host(env, …)`: prima porta libera dopo i tunnel del jump host
  (`next_free_port`, salta le porte locali usate ovunque nel file), blocco Host in fondo
  alla sezione, tunnel dopo l'ultimo `LocalForward` del jump host.
- `update_host` / `delete_host` / `set_jump_hostname`. Validazioni con `ConfigError`
  (messaggio in italiano): nome unico e senza spazi o jolly, porte 1-65535 e libere, una
  descrizione non può nominare l'altro ambiente (il parser la leggerebbe come intestazione
  di sezione), il jump host non si rinomina né si elimina.
- `save()`: rifiuta se il file è cambiato su disco dopo `load()`, scrive `config.bak`
  e poi sostituisce il file in modo atomico (`.tmp` + `os.replace`).

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
2. Chiede il token con `InitOrchestrator.token_prompt`, installato dalla tray: è
   `TokenDialog.ask` (`gui/token_dialog.py`), una finestra Tk pre-caricata sul thread del
   popup, quindi istantanea e a tema. Senza tray (uso da riga di comando) resta il vecchio
   dialog **Windows Forms in un processo PowerShell separato**, che però impiega circa 3 s
   ad apparire.
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
  (`shutdown()` da `quit_application`, con `kill_console` che uccide anche `ssh.exe`),
  dato che le console nascoste non hanno finestra.
- **Login fallito e retry**: la console è `powershell -NoExit` e sopravvive a ssh. Per
  questo:
  - il riuso del login usa `tracker.session_alive()`, che richiede un `ssh.exe` figlio
    vivo;
  - dopo l'iniezione `_await_login()` attende la conferma (porta tunnel aperta o prompt
    shell), fallendo su `ConsoleInjector.FAILURE_MARKERS`, sull'uscita di ssh o dopo 45 s;
  - in caso di fallimento la console viene uccisa e deregistrata (`SshLauncher.discard`)
    e la notifica invita a riprovare;
  - `_discard_stale()` ripulisce le console nascoste rimaste prima di un nuovo login;
  - `_alive_sessions()` fa sì che un retry riapra solo i target mancanti o morti.
  - `_launch(hidden=True)` scarta da sé la console quando l'iniezione fallisce.

### `ssh/console_themes.py` — console PROD riconoscibili
`SshLauncher._console_preamble(name)` antepone a `ssh` (solo console visibili) il titolo
`[TEST|PROD] host` e, per PROD, lo schema colori scelto in `prod_console_theme`
(default **Ubuntu-ColorScheme**) più un banner `PRODUZIONE - host`.
`console_themes.osc_sequences()` traduce uno schema in formato Windows Terminal in
OSC 4 (palette 0-15) e OSC 10/11/12 (testo, sfondo e cursore *predefiniti*). Così
`SGR 0` e `clear` remoti restano nel tema, in Windows Terminal come in conhost.
ESC/BEL viaggiano sulla riga di comando come `|`/`!` e PowerShell li ripristina. Gli
schemi offerti sono quelli integrati più quelli del `settings.json` di Windows Terminal.

### `ssh/remote_files.py` — ricerca e download file remoti
`RemoteSession(host)`: una connessione **paramiko** per host, pigra e riusata,
serializzata da un lock e usata solo da thread worker.
- `connect()`: `SshLauncher.ensure_route()` (jump host + attesa tunnel, lo stesso
  percorso dei terminali), poi `ssh -G` per HostName/Port, credenziali da
  `ConfigLoader`. `known_hosts` è caricato come "system host keys": in sola lettura,
  mai riscritto. Chiave diversa → rifiuto; host sconosciuto → accettato e loggato.
  `login_*` escluso perché richiede il token.
- `find()`: script POSIX `sh` (`find_command`) con `find -L -iname` (`-L`: le cartelle
  applicative sui server sono catene di link simbolici), `-maxdepth 1`
  opzionale e `-exec grep -qiF` per il contenuto. Con `-printf` GNU ottiene mtime e
  dimensione, con fallback `-print`; massimo 2000 risultati; cartella inesistente
  segnalata con un marker. `cancel()` chiude il canale in corso.
- `listdir()` / `resolve_folder()`: elenco SFTP (`listdir_attr`, `stat` solo per i link
  per distinguere cartelle e link rotti). Il percorso resta **logico** (`~`, relativi e
  `..` risolti testualmente, link non risolti); cartelle prima.
- `route_ready(host)`: probe TCP della porta del tunnel, senza aprire nulla.
- `close()` è definitivo (`_closed`): un `connect()` ancora in corso scarta la
  connessione; `_close_client()` è la pulizia interna.
- `download()`: SFTP su file `.part`, rinominato a fine trasferimento.
- Gli errori per l'utente sono `RemoteError`, con messaggio in italiano.

### `gui/file_search_dialog.py` — finestra "Cerca file" (navigazione + ricerca)
**Navigazione come WinSCP** (modalità `browse`): `_navigate(folder)` elenca via
`RemoteSession.listdir`, con la pila `_back` per ◀; ▲/Backspace salgono
(`parent_path`), ⌂ va a `~`, la barra del percorso è editabile (cronologia in
`file_search_paths`). Nome digitato → filtro locale (`name_matches`, stesse regole di
`glob_for`); Invio/Cerca → `find` dalla cartella corrente (modalità `search`, colonna
Cartella e banner visibili, "Vai alla cartella"). L'elenco automatico di un host appena
scelto parte dopo `AUTO_LIST_DELAY_MS` e solo se `route_ready(host)`; altrimenti serve
un'azione esplicita, che può aprire il login come un terminale. Lo stato occupato è per
sessione (`_busy_session`): cambiare host chiude la sessione, sblocca la UI e i
risultati tardivi della vecchia vengono scartati.

`Toplevel` dello stesso root Tk di `SearchPopup`, costruita e pilotata tramite
`run_on_ui`. Aperta dalla voce tray "Cerca file..." o da **Ctrl+F** nel popup host
(`on_file_search`). A sinistra il pannello host (superficie `nav`): campo filtro,
selettore segmentato Tutti/TEST/PROD e `ListView` con Preferiti, Recenti e ambienti e le
icone di stato (`SearchPopup.active_hosts()`). A destra: titolo con chip TEST/PROD, barra
percorso con pulsanti icona (con tooltip), filtri con placeholder, tabella, stato e azioni. Le righe vengono dalle stesse funzioni del popup
(`build_host_rows` / `fill_host_listbox` / `next_host_row` in `search_dialog.py`),
quindi i due elenchi si comportano allo stesso modo. La riga selezionata è l'host
attivo: filtrare sceglie la corrispondenza migliore; frecce e clic cambiano host; Ctrl+D
aggiorna i preferiti condivisi. Una ricerca riuscita aggiorna i Recenti. Ogni
operazione di rete gira su un thread worker (`_run_async`) e i risultati tornano via
`run_on_ui`; se l'host è cambiato nel frattempo vengono scartati. Una sola operazione
alla volta. "Apri" funziona come WinSCP: copia in `OPEN_DIR/<host>/<id>/`, Blocco note,
cancellazione alla chiusura del processo. Se il processo vive meno di 5 s il file è
passato a una finestra già aperta: la copia resta a `purge_open_dir()`, chiamata
all'uscita dalla tray e alla creazione del dialog. La cronologia delle cartelle è salvata
per host in `file_search_paths`.

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
- `session_alive(host)`/`ssh_child_alive(pid)`: più stretti di `is_active`, perché
  richiedono il processo ssh vivo sotto la console. Il nome è in `SSH_PROCESS_NAMES`
  (i test lo puntano a un binario di sistema eseguito sul posto, mai copiato o
  rinominato).
- `kill_console(pid)` uccide console + ssh e li marca con `expect_exit`, così il
  monitor non li segnala come chiusure inattese.

### `ssh/session_monitor.py` — notifiche sulle sessioni
Thread `SessionMonitor` (tick 3 s) avviato/fermato dalla tray. Sorgenti: le console
del tracker e quelle nascoste di Init (`InitOrchestrator.hidden_entries`).
- **Connessione chiusa**: tiene un handle `OpenProcess(SYNCHRONIZE)` su ogni ssh per
  leggerne l'exit code. Visibile → notifica solo su 255 con console ancora aperta;
  nascosta → sempre, e uccide la console orfana; ignora le uscite attese e gli host
  di un Init in corso (`busy_hosts`).
- **Tunnel perso** (ogni 10 s): porte LocalForward da `ssh -G host` (cache per
  mtime del config) confrontate con le porte in LISTEN di qualsiasi ssh
  (`psutil.net_connections`, nessuna connessione aperta). Notifica su perdita o su
  mancato bind dopo 30 s di grace; una sola volta finché non si ripristina.
- Il keepalive fallito è segnalato da `SshLauncher._run_keepalive` (evento puntuale).

### `notifications.py` — sink delle notifiche
`notify(kind, title, msg)` usabile da qualsiasi modulo senza dipendere da pystray;
la tray installa il balloon con `set_sink()`. Filtra per tipo secondo
`AppSettings` (`init`, `connection_lost`, `tunnel_lost`, `keepalive_failed`).

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
- **Preferiti/Recenti** in cima: intestazione disabilitata "Preferiti" + host con
  bitmap di stato (voci di primo livello nel `_bitmap_plan` con `children=None`),
  poi il sottomenu "Recenti". `connect_to_host` registra il recente. Lo stato del
  refresh loop include l'mtime del file preferenze.
- **Impostazioni**: `_ensure_settings_dialog` crea il `SettingsDialog` sul thread Tk
  del popup (pre-warm in `init_tray`). `_apply_settings` applica rebind della
  hotkey, avvio automatico e salva in `AppSettings`, restituendo un messaggio
  d'errore al dialog se qualcosa fallisce.

### `gui/hotkey_manager.py` — hotkey globale (Win32)
Binding configurabile (default **Ctrl+Shift+Space**): `parse_binding` /
`format_binding` convertono stringhe tipo `"Ctrl+Alt+K"` (almeno un modificatore,
eccetto i tasti F). `start()` restituisce se la registrazione è riuscita;
`rebind()` ripristina il binding precedente se il nuovo è occupato.
Registra la hotkey globale e notifica la tray quando
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
- **UI** (stile Windows 11, `gui/widgets.py`): `SearchEntry` con lente e placeholder +
  selettore segmentato Tutti/TEST/PROD + `ListView` dei risultati. Ogni host ha l'icona di
  stato del menu tray (cerchio TEST, quadrato PROD, verde se connesso, da
  `status_provider` = `tracker.active_hosts`); i separatori sono titoli di sezione
  piccoli, saltati dalla navigazione. Nel piè di pagina: conteggio host e tasti.
- **Righe condivise**: `build_host_rows()` (sezioni Preferiti/Recenti/ambienti, filtro,
  rilevanza), `fill_host_listbox(listview, rows, favorites, active)` e `next_host_row()`
  sono funzioni di modulo, riusate dal pannello host di "Cerca file".
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
  `_env_touched` è alzato solo da un cambio del selettore (`_on_env_changed`).
- **Smoke test manuale**: `py -m ssh_connection.gui.search_dialog` apre il
  popup con una lista host fittizia.

### `gui/settings_dialog.py` — dialog Impostazioni
`Toplevel` dello stesso root Tk del `SearchPopup`: tutte le chiamate passano da
`SearchPopup.run_on_ui(fn)`, che mette `fn` sulla coda del thread Tk. Stile Windows 11:
`Sidebar` a sinistra e pagine fatte di card (`setting_row`) a destra, con Salva / Annulla in
basso. Pagine (costruite da `_page_<chiave>`): **Generale** (scorciatoia, avvio
automatico, keepalive, timeout tunnel), **Aspetto** (tema scuro con anteprima immediata,
annullata da Annulla; schema console PROD), **Host**, **Utente e password**,
**Preferiti**, **Notifiche**, **Info**.
- La scorciatoia si cattura da un campo (`binding_from_keys` usa il VK di
  `event.keycode`, indipendente dal layout) e, mentre il campo ha il focus, l'hotkey
  globale è sospesa tramite il callback `hotkey_suspend`.
- **Host**: a ogni apertura carica una copia di lavoro `SshConfigDocument` del config. In
  alto gli indirizzi dei jump host; sotto la tabella degli host (jump esclusi) con
  Aggiungi / Modifica / Elimina. Aggiungi e Modifica aprono `HostForm`, un form modale che
  chiama `add_host`/`update_host` sulla copia e mostra i `ConfigError` nel form. Rinominare
  un host aggiorna anche i preferiti.
- **Utente e password**: campi (password mascherata, con mostra/nascondi) letti con
  `ConfigLoader.read_maven_credentials_raw()`.
- **Salva**: `collect()` valida le preferenze; gli indirizzi dei jump host vanno nella
  copia; `on_save` (tray) può rifiutare le preferenze con un messaggio; poi
  `_write_files()` scrive il config solo se `dirty` (con backup) e le credenziali solo se
  cambiate (entrambi i campi obbligatori). Qualunque errore resta nel piè di pagina e il
  dialog resta aperto. **Annulla** non scrive nulla: la copia viene ricaricata alla
  prossima apertura.
- I preferiti si modificano su una copia di lavoro (Aggiungi / Rimuovi / Su / Giù).

### `gui/token_dialog.py` — token 2FA di Init TEST / Init PROD
Una `Toplevel` per ambiente sul root Tk del `SearchPopup`, costruita nascosta in
`init_tray` (`_install_token_prompt`, che imposta `InitOrchestrator.token_prompt`). Aprirla
è un `deiconify`, circa 0,1 s contro i circa 2,8 s del processo PowerShell + WinForms +
`Add-Type` C# di prima. Segue tema e DPI dell'app. `ask(env, timeout)` è chiamata dal
thread worker di Init e aspetta su un `threading.Event` la risposta, data sul thread Tk da
Connetti/Invio (token), Annulla/Esc/chiusura (None) o dal timeout. Una finestra per
ambiente, così TEST e PROD possono aspettare insieme. Chiamarla dal thread Tk è rifiutato
perché lo bloccherebbe. Il campo viene svuotato a ogni chiusura.

### `gui/widgets.py` — componenti in stile Windows 11
Mattoncini riusati da popup, "Cerca file" e Impostazioni, tutti registrati su `theme`:
`Button` (secondario con bordo da 1 px ottenuto con una cornice, perché `tk.Button` su
Windows non disegna l'highlight; `primary`; `danger`; `subtle`), `IconButton` (glifi Segoe
Fluent Icons / MDL2, con `Tooltip`), `Card` + `setting_row`, `Toggle` (interruttore
legato a una `BooleanVar`), `Segmented`, `SearchEntry`, `Sidebar`, e `ListView`, una
`ttk.Treeview` con l'API della `tk.Listbox` usata dai dialog (`insert`, `delete(0, "end")`,
`curselection`, `selection_set`, `see`…). `<<ListboxSelect>>` parte solo per le selezioni
dell'utente, come in una Listbox, non per `selection_set()`. Le immagini (icone di stato,
cartella/file, interruttore) sono disegnate con PIL sovracampionato 4x e messe in cache per
tema e scala.

### `gui/theme.py` — tema e DPI delle finestre Tk
Aspetto comune di popup host, "Cerca file" e Impostazioni (tutti sul thread Tk del
`SearchPopup`).
- **DPI**: `enable_dpi_awareness()` è chiamata da `main.py` prima di qualsiasi finestra
  (*system aware*: Tk 8.6 non gestisce `WM_DPICHANGED`). Senza, Windows ingrandisce le
  finestre come bitmap e il testo è sfocato. Tk scala i font in punti; le misure in pixel
  (geometrie, larghezze fisse, `wraplength`, righe Treeview) passano da `px()`, con
  fattore `winfo_fpixels("1i") / 96` calcolato in `init(root)`.
- **Temi**: `PALETTES["light"]` (Windows 11, accento `#005fb8`) e `PALETTES["dark"]`
  (One Half Dark di Windows Terminal); font Segoe UI in entrambi (`font()`, glifi con
  `icon_font()`). I widget sono registrati con un **ruolo** e una **superficie**
  (`style(widget, "label", "card")`: `ROLES[role](palette, colore superficie)` → opzioni
  Tk); i ruoli dei pulsanti hanno un colore di hover (`HOVER`). `use(dark)` riapplica la
  palette a tutti, ricolora le tendine delle combobox, la barra del titolo
  (`DwmSetWindowAttribute`) e chiama i listener `on_change` (immagini che dipendono dal
  tema). Stati dinamici via `set_role` (errore, Interrompi, chip TEST/PROD).
- **ttk**: `clam` ristilizzato in entrambi i temi (combobox piatte, scrollbar sottili senza
  frecce, Treeview senza bordi con righe da 28 px logici).
- La preferenza è `dark_theme` in `AppSettings`; `_apply_settings` della tray chiama
  `theme.use()` al salvataggio.

### `config/app_settings.py` e `config/autostart.py`
- `AppSettings`: preferenze utente in `~/.ssh_connection_prefs.json` (`search_env`,
  `hotkey`, `keepalive_interval`, `tunnel_timeout`, `notifications`, `favorites`,
  `recents`, `prod_console_theme`, `file_search_paths`, `dark_theme`), con valori sanificati e limitati, lock e scrittura atomica.
  `SshLauncher.keepalive_command()`/`tunnel_wait_seconds()` le leggono al momento
  dell'uso.
- `autostart`: voce `HKCU\...\Run` con `--autostart`; considera anche il
  collegamento nella cartella Esecuzione automatica creato dagli script di build.
  `describe()` alimenta la riga informativa del dialog; `refresh_path()` (all'avvio)
  riallinea la voce `Run` se punta a un exe che non esiste più.

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
- `save_maven_credentials(user, password)`: scrive nel **primo** `<server>` cambiando
  solo il testo di `<username>`/`<password>` (con escaping XML; li aggiunge dopo `<id>` se
  mancano). Il resto del file, anche una vera configurazione Maven, resta com'è: i
  commenti vengono mascherati prima di cercare i tag. Il risultato viene validato con il
  parser XML prima della scrittura atomica. `read_maven_credentials_raw()` restituisce i
  valori grezzi (dominio incluso, segnaposto = vuoto) per il dialog.
- `maven_settings_path()` / `ensure_maven_settings()`: "Apri file" (pagine Utente e password
  e Info) apre il file in Blocco note e, se manca, lo crea da un modello. I segnaposto
  `INSERISCI_*` non compilati equivalgono a credenziali assenti. `_find()` cerca con e senza
  namespace Maven (confronto `is not None`: un `Element` senza figli è falsy).

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
`paramiko` (ricerca/download file, solo "Cerca file"),
`PyYAML` (config), `cryptography` (AES). Le API Win32 sono usate via `ctypes`
(nessuna dipendenza pywin32).
