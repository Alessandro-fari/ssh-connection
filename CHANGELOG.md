# Changelog — SSH Connection Manager

Tutte le modifiche rilevanti al progetto vanno registrate in questo file.

**Regola**: ogni modifica (funzionalità, fix, refactoring, build, documentazione) deve
aggiungere una voce sotto `[Non rilasciato]`, con data e una breve descrizione del
*perché* oltre che del *cosa*. Eventuali note, problemi noti o decisioni prese vanno
annotati nella voce stessa. Formato ispirato a [Keep a Changelog](https://keepachangelog.com/it/1.1.0/).

## [Non rilasciato]

- **2026-09-21** — **Scorciatoia visibile nel menu tray**: la voce
  **"Cerca host..."** mostra ora `Ctrl+Shift+Space` allineato a destra, come
  fanno le voci native di Windows (Explorer, Blocco note).
  *Perché*: l'hotkey globale era scoperto solo leggendo il README; averlo sotto
  gli occhi al passaggio del mouse lo rende scopribile.
  *Come*: un **tab** nella stringa dell'item — Windows allinea a destra e
  disegna in grigio ciò che segue `	`. pystray passa `MenuItem.text` dritto a
  `MENUITEMINFO.dwTypeData`, quindi non serve nessuna chiamata Win32 in più né
  toccare `win32_menu_bitmaps.py` (che usa solo `MIIM_BITMAP`, il testo resta
  intatto). Verificato il round-trip `AppendMenuW`/`GetMenuStringW`: il tab
  sopravvive.
  - Il label vive in `HotkeyManager.SHORTCUT_LABEL`, accanto alla
    `RegisterHotKey`, così non può divergere dai tasti realmente registrati.
  - Le voci **"Cerca..."** dei sottomenu TEST/PROD restano senza hint: aprono
    la ricerca pre-filtrata, che l'hotkey globale non fa.

### Modificato
- **2026-09-28** — **"Apri utente e password" nella scheda Info** delle Impostazioni.
  Apre in Blocco note `~/.m2/settings.xml`, da cui l'app legge nome utente e password
  (primo `<server>`). Se il file non esiste lo crea da un modello con i segnaposto
  `INSERISCI_UTENTE` / `INSERISCI_PASSWORD` e spiega cosa compilare. Un file esistente
  non viene mai toccato, perché può essere anche la vera configurazione Maven. Le
  modifiche valgono dalla prossima connessione: `ConfigLoader.load()` rilegge il file
  a ogni lancio.
  - I segnaposto non compilati sono trattati come "credenziali assenti", così non
    vengono mai digitati come password in un login.
  - **Fix** in `ConfigLoader._load_maven_credentials`: `a.find(x) or a.find(ns_x)`
    scartava gli elementi trovati, perché un `Element` senza figli è *falsy*. Un
    `settings.xml` **senza namespace** Maven quindi non forniva credenziali. Ora c'è
    `_find()` con `is not None`. Il file reale con namespace funzionava già e
    continua a funzionare (verificato).
- **2026-09-28** — **Rimossi dal repository i file `__pycache__`/`*.pyc`** (17 file
  tracciati per errore) e aggiunti al `.gitignore`. Sono bytecode rigenerato a ogni
  esecuzione: comparivano sempre come modifiche non committate. I file restano su disco.
- **2026-09-28** — **Dialog Impostazioni: preferiti e scorciatoia più chiari** (feedback
  d'uso).
  - *Preferiti*: la lista a selezione multipla non si capiva. Ora in alto ci sono tutti
    gli host, con un filtro; si seleziona una riga e si preme **"Aggiungi ai
    preferiti"** (o doppio clic). Sotto c'è la lista dei preferiti già aggiunti, con
    **Rimuovi** e **Su/Giù** per l'ordine, che è quello di menu e popup. I preferiti
    non più presenti nel config restano visibili come "(non più nel config)" invece
    di sparire in silenzio.
  - *Scorciatoia*: le checkbox dei modificatori + combo del tasto sono sostituite da
    un **campo di cattura**. Clicchi e premi la combinazione (anteprima dei
    modificatori premuti, Esc annulla), con il pulsante **"Predefinita"**
    (Ctrl+Shift+Space). Il tasto finale si legge dal codice virtuale di Windows
    (`event.keycode`), non dal keysym, che dipende dal layout: con la tastiera
    italiana Shift+1 dà "exclam". Mentre il campo ha il focus l'hotkey globale è
    **sospesa** (`TrayIconManager._suspend_hotkey`); altrimenti `RegisterHotKey`
    intercetterebbe la combinazione attuale e aprirebbe la ricerca.
  - *Avvio automatico*: sotto la checkbox c'è una riga che dice cosa parte al login:
    il collegamento in *Esecuzione automatica* oppure l'exe registrato in `Run`, con
    un avviso se quel file non esiste più. All'avvio `autostart.refresh_path()`
    aggiorna la voce `Run` se punta a un exe spostato o rinominato, e ogni
    salvataggio con la checkbox attiva riscrive il percorso corrente.
  - Test: `binding_from_keys`, cattura con sospensione/ripristino dell'hotkey,
    aggiungi/rimuovi/riordina preferiti. `tests/test_features.py` ora usa un file
    prefs temporaneo: leggeva quello reale e falliva con una scorciatoia
    personalizzata.
  - *Nota*: `tests/test_settings_monitor.py` era stato rimosso dal "Cleanup" di
    Sophos (evento sulla versione con i binari rinominati) ed è stato ricreato
    nella versione pulita.

### Aggiunto
- **2026-09-28** — **Dialog Impostazioni** (voce di menu **"Impostazioni..."**, ex
  "Settings"). Prima la voce apriva solo `~/.ssh/config` in Notepad; ora apre un
  dialog tkinter a schede:
  - *Generale*: scorciatoia di ricerca (modificatori Ctrl/Shift/Alt/Win + tasto),
    intervallo keepalive, timeout di attesa del tunnel, avvio automatico con Windows;
  - *Notifiche*: un interruttore per ciascun tipo di notifica;
  - *Preferiti*: selezione degli host preferiti, pulsante "Svuota recenti";
  - *Info*: versione, percorsi di config/preferenze/log, pulsanti "Apri config SSH"
    e "Apri log".
  *Pre-warm*: è un `Toplevel` dello **stesso** interprete Tk del popup di ricerca,
  pilotato con `SearchPopup.run_on_ui()`. Si costruisce nascosto all'avvio e si apre
  all'istante. Un secondo `Tk()` su un altro thread sarebbe stato un secondo
  interprete Tcl, che non è thread-safe.
  Il salvataggio è validato: una scorciatoia senza modificatori o già usata da un
  altro programma (`RegisterHotKey` errore 1409) mostra l'errore, il dialog resta
  aperto e la scorciatoia precedente torna attiva (`HotkeyManager.rebind`).
  Keepalive e timeout valgono dalla prossima connessione, senza riavvio.
  Se il dialog non si può creare, la voce ricade sul vecchio comportamento (apre
  `~/.ssh/config`).
  - Nuovo `config/app_settings.py` (`AppSettings`): unico punto di accesso a
    `~/.ssh_connection_prefs.json`, con lock, read-modify-write e scrittura atomica.
    **Fix collegato**: il vecchio `save_prefs_env` riscriveva il file con la sola
    chiave `search_env`, e avrebbe cancellato tutte le altre preferenze.
  - Nuovo `config/autostart.py`: chiave `HKCU\...\CurrentVersion\Run`, senza
    privilegi admin. Conta come "attivo" anche il collegamento nella cartella
    *Esecuzione automatica* creato da `build_release.py`. L'attivazione non aggiunge
    la voce di registro se il collegamento c'è già, la disattivazione li rimuove
    entrambi: l'app non parte mai due volte.
  - `main.py --autostart` (passato dalla voce di registro): all'avvio con Windows
    niente message box "starting...".
- **2026-09-28** — **Preferiti e Recenti**.
  - *Menu tray*: in cima c'è l'intestazione "Preferiti" con gli host preferiti,
    cliccabili direttamente e con la bitmap di stato colorata. Sotto, il sottomenu
    "Recenti" con gli ultimi 5 host connessi. Gli host non più presenti in
    `~/.ssh/config` vengono ignorati.
  - *Popup di ricerca*: senza filtro la lista parte con le sezioni "★ Preferiti" e
    "Recenti" (preferiti esclusi); il primo preferito è preselezionato, quindi
    hotkey + Invio apre subito l'host preferito. Mentre si filtra, i preferiti
    vengono ordinati per primi in ogni ambiente e marcati con ★. `Ctrl+D` aggiunge
    o toglie dai preferiti l'host selezionato.
  - I recenti sono registrati in `TrayIconManager.connect_to_host`, quindi valgono
    per menu, preferiti e popup; gli Init non li alimentano.
  - Il refresh loop della tray include ora l'mtime del file preferenze nello
    stato, così il menu si aggiorna subito dopo una modifica.
- **2026-09-28** — **Notifiche di problemi sulle sessioni** (nuovo
  `ssh/session_monitor.py`, thread con tick di 3 s, e `notifications.py`, un sink
  centrale filtrato per tipo dalle impostazioni):
  - *Connessione chiusa inaspettatamente*. Il monitor tiene un handle Win32 su ogni
    `ssh.exe`, per leggerne l'exit code dopo la chiusura (la console
    `-NoExit` sopravvive a ssh):
    - console visibili: notifica solo con codice **255** (errore di ssh: rete, VPN,
      ServerAlive timeout); logout normale e finestra chiusa dall'utente restano
      silenziosi;
    - console nascoste di Init: qualsiasi uscita viene notificata, con l'invito a
      rilanciare Init; la console orfana viene uccisa;
    - uscite causate da noi (`kill_console`) vengono ignorate
      (`expect_exit`/`exit_was_expected`), come i fallimenti durante un Init in
      corso, che segnala già l'Init (`InitOrchestrator.busy_hosts`).
  - *Tunnel perso*. Per ogni sessione viva, le porte LocalForward risolte da
    `ssh -G <host>` (gestisce i blocchi `Host *it1tf*` come ssh) devono risultare
    in LISTEN da un `ssh.exe` nella tabella TCP (`psutil.net_connections`), quindi
    **nessun traffico** verso DB o sshd remoti. La notifica parte se una porta
    smette di essere in ascolto, o se una sessione nuova non l'ha aperta entro
    30 s (tipicamente la porta è già occupata). Una sola notifica finché la porta
    non torna attiva.
  - *Keepalive non avviato*. `SshLauncher._run_keepalive` verifica 5 s dopo l'invio
    che `watch` sia partito (niente "command not found", niente prompt tornato
    subito) e notifica se il prompt shell non compare mai. Non notifica se la
    sessione è già morta: in quel caso lo segnala il monitor.
  - Anche l'esito di Init passa ora dal sink (tipo `init`), quindi è disattivabile.
  - Test: nuova suite `tests/test_settings_monitor.py`: preferenze,
    parsing/rebind della hotkey, menu con preferiti/recenti, popup e dialog
    Impostazioni reali, monitor (exit 255/0/kill nostro/console nascosta), tunnel
    (salute, perdita, ri-armo, grace), keepalive.
  - Build: aggiunti gli hidden import `session_monitor`, `settings_dialog`,
    `app_settings`, `autostart`, `notifications`, `tkinter.ttk` in
    `build_release.py`/`build_debug.py`.
  *Nota sui test*: la prima versione simulava ssh copiando `cmd.exe`/`PING.EXE`
  come `%TEMP%\sshcm-test-*\ssh.exe`. Sophos l'ha correttamente bloccata come
  *masquerading* (Evade_13a, T1036.003), uccidendo il processo di test. Ora
  `connection_tracker.SSH_PROCESS_NAMES` è configurabile: i test eseguono i binari
  di sistema **dal loro percorso originale** e li riconoscono per nome. Mai copiare
  o rinominare un eseguibile di sistema.
  *Problemi noti*: una sessione visibile con ssh chiuso resta "attiva" nel menu
  finché la finestra è aperta, perché la semantica di `is_active` non è cambiata.
  Non è stata fatta una prova end-to-end sul server reale (VPN/token): il flusso è
  coperto dai test con processi simulati.

### Corretto
- **2026-09-28** — **Init ripetibile dopo un login fallito** (token sbagliato o VPN
  non attiva). Prima un Init fallito era irrecuperabile: bisognava chiudere e
  rilanciare l'applicazione.
  *Causa*: la console è `powershell -NoExit`. Quando ssh termina (VPN giù →
  *Connection timed out*; token rifiutato → *Access denied* / *Invalid username or
  password*), PowerShell resta vivo, nascosto e non chiudibile. Il tracker lo
  considerava quindi `login_*` **attivo**, e ogni Init successivo "riusava" una
  sessione morta invece di rifare il login. Anche le console nascoste dei target
  falliti restavano orfane.
  *Fix*:
  - `ConsoleInjector.FAILURE_MARKERS` / `failure_in()`: l'iniezione si interrompe
    subito quando ssh stampa un errore di connessione o autenticazione. Prima
    aspettava inutilmente il timeout di 60 s.
  - `InitOrchestrator._await_login()`: dopo l'invio del token attende la *prova* che
    il login sia riuscito (porta del tunnel aperta, o prompt shell), fino a 45 s.
    Aver scritto il token non basta: un token errato viene rifiutato dopo.
  - Ogni login fallito viene **ucciso e deregistrato** (`SshLauncher.discard`,
    `kill_console` che termina anche `ssh.exe`, `tracker.unregister`). Lo stesso
    vale per qualsiasi console nascosta la cui iniezione fallisce. La notifica
    indica il motivo e invita a riprovare da *Init TEST/PROD*.
  - Il riuso del login usa `tracker.session_alive()`, che richiede un `ssh.exe`
    vivo sotto la console, invece di `is_active()`. Prima di un nuovo login,
    `_discard_stale()` elimina le console nascoste rimaste; le finestre
    **visibili** vengono solo deregistrate, così il loro output d'errore resta
    leggibile.
  - Un Init *parziale* si può rilanciare: i target con ssh ancora vivo vengono
    tenuti, quelli morti vengono uccisi e riaperti.
  - `shutdown()` ora uccide anche i processi `ssh.exe` figli, non solo PowerShell.
  - Test `[3b]` in `tests/test_features.py`: marker di errore, rilevamento del
    figlio ssh (con un finto `ssh.exe`), `kill_console`, retry di `_run` dopo un
    login fallito.
  *Nota*: la semantica di `is_active()` non cambia (menu e altri flussi invariati).
  Una sessione visibile con ssh chiuso resta quindi "attiva" nel menu finché la
  finestra è aperta.

### Modificato
- **2026-09-21** — **Ricerca host riscritta in tkinter, apertura istantanea**.
  Il dialog PowerShell/WinForms introdotto il 2026-09-17 aveva due difetti
  bloccanti: (1) *lentezza* — ogni apertura lanciava `powershell.exe` e faceva
  `Add-Type -AssemblyName System.Windows.Forms`, ~1.5-3 s prima che la finestra
  comparisse, inaccettabile per un popup pensato per "digita e salta all'host";
  (2) *non funzionava* — la `ListBox` `OwnerDrawFixed` andava in errore perché
  PowerShell spacchetta i valori delle hashtable in modo diverso da quanto
  assunto nell'handler `DrawItem`, quindi la lista risultati restava
  inutilizzabile.
  *Decisione*: abbandonare il processo esterno e passare a **tkinter**
  (già nella stdlib e già negli hidden import PyInstaller, quindi zero nuove
  dipendenze e nessun peso aggiuntivo sull'exe) con un'istanza **pre-caricata**.
  - `gui/search_dialog.py` riscritto: `SearchDialog` (usa e getta, subprocess)
    sostituito da `SearchPopup`, un singolo `Tk()` nascosto creato **una volta
    sola** all'avvio su un thread dedicato con il suo `mainloop`. Aprire il
    popup è `deiconify()` + `SetForegroundWindow()`; chiuderlo è `withdraw()`.
    Misurato: `show()` ritorna in ~0 ms e la finestra compare entro il poll da
    50 ms, contro i ~2 s di prima. Il pre-warm (~1 s) è pagato una volta
    all'avvio dell'applicazione, non dall'utente.
  - Thread-safety: Tcl non è thread-safe, quindi solo il thread del popup tocca
    Tk; tray e thread hotkey comunicano via `queue.Queue` drenata da un
    `after(50)`. `show()` è così chiamabile da qualsiasi thread senza bloccare.
  - Foreground: helper `_force_foreground` con trucco `AttachThreadInput` +
    `SetForegroundWindow`, necessario perché un processo non in foreground non
    può alzare una finestra (sul percorso hotkey Windows concede già il diritto
    a chi riceve `WM_HOTKEY`, sul percorso menu tray no).
  - Funzionalità mantenute: filtro sottostringa live, separatori TEST/PROD non
    selezionabili e saltati da ↑/↓, `Invio` connette, `Esc` chiude, ComboBox
    ambiente con persistenza in `~/.ssh_connection_prefs.json` solo se l'utente
    la cambia davvero (flag `_env_touched`, sostituisce l'echo di
    `$script:saveEnv`). Aggiunti: ordinamento risultati per rilevanza
    (esatto > prefisso > confine di parola > sottostringa), PagSu/PagGiù,
    doppio click per connettere, riga di stato con il conteggio host.
  - `gui/tray_icon_manager.py`: nuovo `_ensure_search_popup()` chiamato in
    `init_tray` (pre-warm prima del blocco su `icon.run()`); `_open_search`
    ora posta solo la richiesta e ritorna, niente più thread per apertura.
    Alla conferma, `_on_search_selected` lancia `connect_to_host` su un thread
    separato: è bloccante (spawn ssh + iniezione console) e non deve girare sul
    thread Tk, altrimenti il popup resterebbe congelato a video. `stop()` e
    `quit_application()` chiudono anche il popup.
  - `SSH-Connection-Manager*.spec`: aggiunto hidden import `tkinter.ttk`
    (usato per la ComboBox; `tkinter` c'era già).
  - *Nota / problema noto*: il popup resta a video se si clicca altrove — la
    chiusura su perdita di fuoco è stata rimossa perché in Tk il `FocusOut`
    arriva anche per i widget figli (combo aperta inclusa) e chiudeva il popup
    a sproposito. Si chiude con `Esc`, `Annulla` o la X.
  - *Smoke test*: `py -m ssh_connection.gui.search_dialog` apre il popup con
    una lista host fittizia, senza tray né SSH.

### Aggiunto
- **2026-09-17** — **Ricerca host da tastiera**: nuovo hotkey globale
  **Ctrl+Shift+Space** che apre un dialog "Cerca host" (Windows Forms in
  processo PowerShell STA separato, stesso pattern del dialog token 2FA).
  L'utente digita per filtrare (sottostringa case-insensitive), scorre con
  le frecce ↑/↓ (saltando i separatori TEST/PROD), `Invio` connette,
  `Esc` annulla. A destra una ComboBox **Tutti/TEST/PROD** restringe la
  ricerca; l'ultima scelta è persistita in `~/.ssh_connection_prefs.json`
  e diventa il default al prossimo avvio via hotkey.
  *Perché*: con molti host, navigare i sottomenu tray diventa lento; la
  ricerca keystroke-driven apre il terminale in due secondi senza toggere
  il mouse.
  - Nuovo modulo `gui/hotkey_manager.py`: thread dedicato con message loop
    Win32 (`GetMessage`) e `RegisterHotKey`/`UnregisterHotKey` via ctypes;
    riceve `WM_HOTKEY` e invoca il callback. Thread separato perché pystray
    possiede il message loop della tray sul thread principale e non espone
    hook. `MOD_NOREPEAT` evita la ripetizione del tasto tenuto premuto.
  - Nuovo modulo `gui/search_dialog.py`: dialog Windows Forms eseguito
    come processo PowerShell STA separato (message loop proprio, niente
    interferenza con la tray, foreground forzato via `SetForegroundWindow`).
    Lista host passata come JSON su stdin (PIPE, non ereditata: evita il
    bug dello STD_INPUT_HANDLE stale visto nel dialog token); l'host scelto
    è stampato su stdout. I separatori TEST/PROD sono voci non selezionabili
    disegnate con `OwnerDrawFixed`. La ComboBox ambiente aggiorna il filtro
    live e aggiorna `$script:saveEnv` (persistito solo se l'utente cambia
    esplicitamente, non se forzato dal sottomenu di partenza).
  - `gui/tray_icon_manager.py`: ogni sottomenu TEST/PROD ha ora una voce
    **"Cerca..."** in cima (bitmap `None`, niente pallino) che apre il dialog
    pre-filtrato a quell'ambiente; `_open_search` gira in thread background
    (il dialog è bloccante). `HotkeyManager` avviato in `init_tray`,
    fermato in `quit_application`/`stop`.
  - **Note/decisioni**: il payload host transita su stdin come JSON per
    non dover escapizzare nomi di host nello script PS; l'output è due
    righe (host + env finale) per gestire la persistenza. `save_prefs_env`
    scrive solo se `new_env != initial`, così aprire "Cerca..." dal
    sottomenu TEST (forzato) senza toccare la combo non sovrascrive il
    default "Tutti" usato dall'hotkey.
  - Hidden import `hotkey_manager` e `search_dialog` aggiunti a spec
    PyInstaller, `build_release.py` e `build_debug.py` (l'import di
    `search_dialog` è lazy dentro il worker, non visibile all'analisi
    statica di PyInstaller).
- **2026-09-18** — **Voce top-level "Cerca host..." nel menu tray**: prima
  delle voci Init/Settings/Exit, separata da separatori, per rendere la
  ricerca immediatamente visibile senza aprire il sottomenu TEST/PROD.
  Richiama `_open_search_top` (stesso del hotkey globale, usa la preferenza
  salvata, nessun ambiente forzato).
  *Perché*: la voce "Cerca..." dentro ogni sottomenu non era evidente;
  l'utente non la notava finché non espandeva il sottomenu.

### Corretto
- **2026-09-18** — **Testo sovrapposto nel ListBox del dialog di ricerca**:
  le voci del ListBox owner-drawn apparivano "compenetrate" (text overlap)
  specialmente su display ad alto DPI. *Causa*: il `DrawItem` usava
  `Graphics.DrawString` con coordinate Y calcolate a mano (`Y + 2`/`Y + 3`),
  che su DPI scaling non rispetta l'altezza della cella. *Fix*: riscritto con
  `TextRenderer.DrawText` + flag `VerticalCenter` (API corretta per
  owner-drawn ListBox: centra il testo nel rettangolo `e.Bounds`).
  Aggiornato anche `ItemHeight = 22` esplicito per coerenza. Il background
  è ora fillato con `FillRectangle` prima del testo (cancella residui).
- **2026-07-17** — Dialog del token 2FA che non compariva su ogni Init **successivo al
  primo** (es. Init TEST completato, poi Init PROD: nessun popup, flusso interpretato
  come "annullato"). Dal log: `Token dialog failed: [WinError 6] Handle non valido` /
  `[WinError 50] Richiesta non supportata`, istantaneo allo spawn di powershell.
  *Causa*: le iniezioni console (`FreeConsole`/`AttachConsole` in `console_injector`)
  lasciano lo `STD_INPUT_HANDLE` ereditato del processo tray puntare a una console
  ormai liberata; `subprocess.run(capture_output=True)` ridireziona stdout/stderr ma
  eredita stdin, quindi Python fa `GetStdHandle`+`DuplicateHandle` sull'handle stale
  e fallisce. Il primo Init funziona perché nessuna iniezione è ancora avvenuta.
  *Fix*: `stdin=subprocess.DEVNULL` nello spawn del dialog (`init_orchestrator._ask_token`),
  così lo std handle ereditato non viene mai toccato.

### Aggiunto
- **2026-07-14** — Nuova voce di menu **Init**: con un solo click chiede un TOKEN 2FA
  in una dialog e apre l'intero set di tunnel — i due jump host `login_test` e
  `login_prod` (password + token iniettati) e i quattro host settlement/DB
  `stlit1tf01`, `stlit1te01`, `stlit1pf01`, `stlit1pe01` — tutti con il keepalive
  `watch -n 240 date`. *Perché*: evitare di aprire e autenticare manualmente sei
  connessioni ogni volta solo per tenere su i tunnel DB.
  - Tutte e sei le console vengono aperte **nascoste** (`STARTUPINFO` con
    `SW_HIDE` + `ShowWindow` difensivo per Windows Terminal): restano console reali
    (l'iniezione via `AttachConsole` continua a funzionare) ma senza finestra né
    voce in taskbar/Alt-Tab, perché servono solo a mantenere sessioni e tunnel.
  - I quattro host `stli*` **non** vengono registrati nel tracker → invisibili anche
    nel menu; i due login sì (il menu li mostra attivi e i click normali riusano i
    loro tunnel). All'uscita dell'app le console nascoste di Init vengono terminate
    (l'utente non ha una finestra da chiudere).
  - Nuovo modulo `ssh/init_orchestrator.py`; `console_injector` generalizzato a una
    sequenza di segreti (`inject_secrets`, con `inject_password` come caso a un solo
    segreto) per rispondere a prompt password→token distinguendoli per avanzamento
    del cursore; `ssh_launcher._launch` esteso con `hidden`/`secrets`/`register`/
    `keepalive` e nuovo `launch_for_init`. Aggiornati hidden import build/spec
    (`init_orchestrator`).
  - **Note/decisioni**: gli host di Init sono elencati come costanti in
    `init_orchestrator.py` (`INIT_LOGINS`, `INIT_TARGETS`); il token è richiesto solo
    dai due login (gli `stli*` chiedono solo la password, già iniettata). Init è
    single-flight: un secondo click mentre è in corso (o con console ancora vive)
    mostra "Init già attivo".
- **2026-07-15** — Dialog del token 2FA: il popup non compariva e bloccava la tray.
  Due cause distinte, entrambe risolte:
  1. **Deadlock** in `InitOrchestrator.start()`: teneva `cls._lock` (un `threading.Lock`)
     e chiamava `_live_procs()` che tentava di riacquisirlo → il thread principale della
     tray (i callback pystray girano lì) si bloccava per sempre, congelando il menu e
     non aprendo mai il dialog. Risolto usando un `threading.RLock` rientrante.
  2. **Dialog invisibile**: un dialog tkinter creato in un thread secondario dello stesso
     processo della tray destabilizza il message loop Win32. Sostituito con un dialog
     **Windows Forms eseguito come processo PowerShell separato** (STA), che ha un proprio
     message loop, non tocca la tray e si forza in primo piano via `SetForegroundWindow` /
     `BringWindowToTop` (un processo lanciato dal click sulla tray non possiede il
     foreground, quindi altrimenti si apriva dietro le altre finestre). Grafica curata:
     header blu coerente con l'icona, font Segoe UI, campo mascherato, pulsanti OK/Annulla.
  - Aggiunto logging su tutto il percorso Init (click → start → dialog → lancio host).
- **2026-07-15** — **Init suddiviso per ambiente**: la singola voce "Init" è stata
  sostituita da due voci — **Init TEST** e **Init PROD** — ognuna con il proprio token.
  *Perché*: verificato end-to-end che un singolo token SecurID autentica **un solo**
  login. Con lo stesso token: in sequenza il secondo login dà *"Invalid username or
  password"*, in contemporanea (iniezione dei due token a ~5ms) dà *"Session not started
  or timedout"* — in entrambi i casi passa un solo ambiente (è una corsa su quale vince).
  Quindi TEST e PROD richiedono ciascuno un token fresco.
  - `InitOrchestrator` ora è parametrico per ambiente (`INIT_ENVS = {TEST:…, PROD:…}`),
    `start(env, notify)` con single-flight **per ambiente** (`_running` è un set), il
    dialog mostra titolo/host dell'ambiente. Ogni bottone apre il suo jump host
    (`login_*`, password+token) e i suoi 2 host `stli*` nascosti, con keepalive.
  - Rimossi i metodi `submit_password_await_token`/`type_secret` dall'injector (servivano
    al tentativo "token simultaneo", ora abbandonato): il flusso a login singolo usa
    di nuovo `inject_secrets([password, token])` con il gating posizionale del cursore.
- **2026-07-15** — Corretta una **race** nell'iniezione del token in `console_injector`:
  dopo l'Invio sulla password il cursore scende su una riga vuota mentre il server non ha
  ancora stampato il prompt del token, ma la vecchia riga `Password:` (che finisce con `:`)
  restava l'ultima non vuota → il token veniva iniettato **nel vuoto** e il login restava
  bloccato al prompt token (visto in `login_test`: prompt token vuoto, nessun errore).
  Ora `_prompt_ready` per i prompt successivi al primo richiede una riga di prompt
  **nuova e diversa** dalla precedente (non solo il cursore avanzato). Verificato con test
  che simula il ritardo del server prima del prompt token. Con questo fix Init TEST e
  Init PROD aprono correttamente tutti e 4 i tunnel `stli*` (2222/2224 + 3222/3223),
  nascosti.

### Documentazione
- **2026-07-14** — Creati `docs/ARCHITECTURE.md` (architettura e funzionamento interno),
  questo `CHANGELOG.md` e `CLAUDE.md` con la regola di aggiornamento della cronologia
  a ogni modifica.

## [1.0.0] — Cronologia precedente (ricostruita da git)

### Aggiunto
- Password mirata al terminale (iniezione Win32 nel buffer della console del PID
  target, indipendente dal focus), stato connessioni colorato nel menu tray
  (TEST=cerchi, PROD=quadrati), avvio automatico del jump host, keepalive della
  sessione (`watch -n 240 date`) e hot-reload di `~/.ssh/config`. (`bf4985b`)
- Voce Settings nel menu tray e gestione riavvio. (`d8d1825`)
- Esempio di configurazione in `example_config/`. (`7584201`)

### Corretto
- Build release: flag `--clean` e hidden import `win32_menu_bitmaps` nello spec
  PyInstaller — senza, le icone colorate del menu mancavano nell'exe. (`4cabd09`)

### Note
- Chiave di cifratura derivata da `%COMPUTERNAME%` per compatibilità con la
  precedente implementazione Java: le stringhe cifrate non sono portabili tra macchine.
- Preferire l'OpenSSH di Windows a quello di Git: risolve `~/.ssh/config` via
  `%USERPROFILE%`, mentre l'ssh di Git usa `HOME` (che in questo ambiente punta altrove).
