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
