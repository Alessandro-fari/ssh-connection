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
- **2026-09-30** — **Finestra del token di Init TEST / Init PROD istantanea e a tema.**
  Era un dialog WinForms lanciato come processo PowerShell: circa **2,8 s** misurati
  prima ancora che esistesse la finestra (avvio di powershell.exe, caricamento degli
  assembly, compilazione C# dell'`Add-Type` per il primo piano). In più ignorava il tema
  ed era sfocato, perché non era DPI aware. Ora è una finestra Tk pre-caricata sul thread
  del popup (`gui/token_dialog.py`) e appare in circa **0,1 s**.
  - Stesso stile delle altre finestre: chip TEST/PROD, host che verranno aperti, token
    mascherato con mostra/nascondi, Invio = Connetti, Esc = Annulla. Il token vuoto è
    rifiutato nella finestra.
  - L'orchestratore la usa tramite `InitOrchestrator.token_prompt`, installato dalla
    tray; senza tray (CLI) resta il dialog PowerShell. Il vecchio motivo per non usare Tk
    (un root Tk su un thread worker disturbava la tray) non vale più: il thread Tk del
    popup esiste già ed è stabile.
  - Una finestra per ambiente (TEST e PROD possono aspettare insieme), timeout 300 s,
    campo svuotato a ogni chiusura. Test: `tests/test_token_dialog.py`.
- **2026-09-30** — **Nuovo aspetto in stile Windows 11** per Cerca host, Cerca file e
  Impostazioni (la UI precedente era poco curata: bande blu, bordi neri, liste fitte).
  - *Impostazioni*: barra di navigazione a sinistra (Generale, Aspetto, Host, Utente e
    password, Preferiti, Notifiche, Info) e pagine fatte di card con titolo, descrizione e
    controllo a destra. Interruttori on/off al posto delle caselle. Il **tema scuro** ha
    l'anteprima immediata e Annulla la toglie.
  - *Cerca host*: campo di ricerca con lente e placeholder, selettore segmentato
    Tutti/TEST/PROD, righe più alte con l'**icona di stato** del menu tray (cerchio TEST /
    quadrato PROD, verde se connesso), sezioni come piccoli titoli.
  - *Cerca file*: pannello host come barra laterale, pulsanti icona con tooltip per
    indietro/su/home/aggiorna, filtri con placeholder, chip TEST/PROD, icone cartella/file
    disegnate, tabella senza bordi pesanti.
  - Nuovo `gui/widgets.py` (Button, IconButton, Tooltip, Card, setting_row, Toggle,
    Segmented, SearchEntry, Sidebar, ListView). `ListView` è una Treeview con l'API della
    Listbox, così la logica di popup, pannello host e preferiti non è cambiata.
    `gui/theme.py` ha ora palette Windows 11 / One Half Dark con superfici (bg, card, nav),
    colori di hover e ttk `clam` ristilizzato in entrambi i temi.
  - I pulsanti secondari hanno un bordo da 1 px ottenuto con una cornice: `tk.Button` su
    Windows ignora `highlightthickness`.
- **2026-09-30** — Scheda **Notifiche** spostata dopo **Preferiti** (ordine: Generale,
  Preferiti, Notifiche, Info): i preferiti si usano più spesso delle notifiche.
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

### Corretto
- **2026-10-01** — **Le Impostazioni non restano più sempre in primo piano.** La finestra
  era aperta con `-topmost` e tutte le altre applicazioni finivano sotto di lei; lo stesso
  valeva per i form modali *Host* e *Porta*. Ora si porta davanti una volta sola
  all'apertura (`_force_foreground`, come "Cerca file"), poi si comporta come una finestra
  normale: le app aperte dopo le vanno sopra e si ritrova dalla barra delle applicazioni,
  da Alt+Tab o riaprendola dalla tray. I due form, `transient`, restano sopra le
  Impostazioni ma non sopra le altre app.
  - *Verifica*: stili Win32 della finestra (niente `WS_EX_TOPMOST`, nessun owner, niente
    tool window, quindi pulsante sulla taskbar) e z-order con Blocco note aperto dopo:
    Blocco note, poi il form Host, poi le Impostazioni.
  - Restano `-topmost` il popup di ricerca e la finestra del token di Init, che sono
    finestre "volanti" aperte da hotkey o tray e chiuse subito dopo l'uso.
- **2026-09-30** — **"Cerca file" non trovava nulla nelle cartelle applicative**:
  0 risultati in 0,1 s su `/app/nets/batchcommon` di `stlit1te01`, dove i file c'erano.
  *Causa*: la cartella è una catena di link simbolici. `batchcommon` punta a
  `BATCHCOMMON_3.2.0.3`, `files` a `/files/nets/batchcommon_3.0.0/`, `clear` a
  `/files/nets/batchcommon/clear`. Senza opzioni `find` non segue i link, nemmeno quello
  di partenza, mentre il controllo `[ -d ]` iniziale li segue: nessun errore e nessun
  risultato.
  *Fix*: `find -L` in tutte le invocazioni dello script. Con `-L` anche `-type f`,
  dimensione e data si riferiscono al file puntato. GNU find riconosce i cicli di link e
  li salta; valgono i limiti già presenti (2000 risultati, timeout).
  - Verificato dal vivo: la stessa ricerca ora trova 2 file (quello in `files/clear` e la
    copia `.pgp` in `files/cache/outgoing/completed`) in 1,4 s.
  - Test di regressione sulla presenza di `-L`. Il test locale con `sh` non può creare
    link simbolici veri su Windows senza privilegi.
- **2026-09-30** — **Finestre sfocate** su schermi con ridimensionamento (es. 125%). Il
  processo non dichiarava la *DPI awareness*: Windows disegnava le finestre Tk a 96 DPI e
  le ingrandiva come bitmap, con testo impastato. Ora `main.py` chiama
  `theme.enable_dpi_awareness()` prima di creare qualsiasi finestra (anche il messaggio di
  avvio e la tray): `SetProcessDpiAwarenessContext(SYSTEM_AWARE)`, con fallback
  `SetProcessDpiAwareness(1)` / `SetProcessDPIAware()`.
  - *System aware* e non per-monitor: Tk 8.6 non gestisce `WM_DPICHANGED`, così su un
    monitor con DPI diverso ci pensa ancora Windows.
  - Tk scala da sé i font (in punti), non le misure in pixel: geometrie, larghezze fisse,
    `wraplength`, altezza della banda e righe del Treeview passano da `theme.px()`
    (fattore = DPI / 96). Impostazioni un po' più alta (640 px logici) per la nuova voce.
  - Anche il menu tray e le sue icone di stato (`SM_CXMENUCHECK`) sono ora nitidi.

### Aggiunto
- **2026-10-01** — **Scelta dell'editor per i file di testo, "Apri con" con Solo questa
  volta / Sempre.** Prima "Apri" del config usava `os.startfile`: il config non ha
  estensione, quindi Windows chiedeva il programma ogni volta, senza poterlo ricordare. I
  file di "Cerca file" e le credenziali andavano sempre in Blocco note, senza scelta.
  - Nuovo `gui/text_editor.py`. Finché non c'è un editor salvato, una finestra "Apri con"
    elenca gli editor trovati sul PC e permette di sceglierne un altro con
    **Sfoglia...**; poi **Solo questa volta** o **Sempre**. Su questo PC trova Blocco note,
    Notepad++ (predefinito per .txt), klogg (predefinito per .log) e VS Code.
  - Vale per tutti gli "Apri" delle Impostazioni (config SSH, utente e password,
    preferenze, log) e per il doppio clic / Invio su un file in "Cerca file". Gli archivi
    e i documenti (`.gz`, `.zip`, `.pdf`, `.xlsx`…) continuano ad aprirsi con il loro
    programma.
  - *Impostazioni → Generale → Editor dei file di testo*: combo con "Chiedi ogni volta"
    (il default) e gli editor trovati, più Sfoglia...; nuova preferenza `text_editor`.
  - "Cerca file": annullare "Apri con" non apre nulla e cancella subito la copia
    temporanea. La pulizia alla chiusura dell'editor resta (`NOTEPAD_HANDOFF_SECONDS` →
    `EDITOR_HANDOFF_SECONDS`): un editor che passa il file a una finestra già aperta ed
    esce subito (Notepad++, VS Code, Blocco note a schede) lascia la copia a
    `purge_open_dir()`.
  - *Scelta*: una preferenza dell'app invece dell'associazione di Windows. Il config non
    ha estensione e i file remoti ne hanno di ogni tipo (`.log`, `.out`, `.1`…), quindi un
    solo editor per tutti è più prevedibile, e l'app ha bisogno dell'handle del processo
    per cancellare la copia.
  - Build: aggiunto l'hidden import `ssh_connection.gui.text_editor` (build_release,
    build_debug, spec).
- **2026-10-01** — **Più porte inoltrate per host dalla GUI** (*Impostazioni → Host →
  Modifica*, sezione **Porte inoltrate**). Prima la GUI gestiva solo il tunnel SSH
  sul jump host: le altre righe `LocalForward` nel blocco dell'host (DB, HSM, mbean JMX…)
  si potevano aggiungere solo a mano nel config. Così la GUI non copriva tutto quello che
  si fa nel file.
  - Tabella con porta locale, destinazione e descrizione (il commento subito sopra la
    riga, es. `# mbean di GWEPS`), con **Aggiungi porta… / Modifica… / Elimina**. Il form
    della porta chiede porta locale, destinazione (vuoto = `localhost`, cioè l'host
    stesso), porta remota (vuoto = uguale alla locale) e descrizione. Disponibile anche
    per un host nuovo e per gli host diretti. La tabella degli host ha la nuova colonna
    "Porte inoltrate".
  - `SshConfigDocument.forwards()` / `check_forwards()` / `set_forwards()`. Le righe non
    toccate restano identiche; quelle modificate sono riscritte sul posto; quelle
    eliminate spariscono con il loro commento; le nuove vanno in fondo al blocco con
    l'indentazione delle righe vicine. I commenti che sono direttive commentate
    (`#LocalForward …`) non sono presi come descrizione.
  - *Conflitti*. Errore se la porta locale è già quella di un tunnel di un jump host
    (sempre aperta con lui), se è doppia nello stesso host o se è già inoltrata da un
    blocco wildcard che vale anche per quell'host (es. `Host *it1te*` → 1523): ssh non
    riuscirebbe ad aprirla. **Solo un avviso** (con conferma) se un *altro* host usa la
    stessa porta locale. Il config reale lo fa apposta (3050, 9978, 25000… su più host):
    vuol dire solo che le due sessioni non si aprono insieme.
  - Le porte già presenti nel file e non modificate non vengono ricontrollate, così un
    conflitto preesistente non blocca le altre modifiche. Modifica dell'host e porte si
    applicano insieme: se una delle due fallisce, la copia di lavoro torna com'era.
  - Come per ogni modifica al config: valgono dalla prossima apertura della sessione
    dell'host, senza riavviare l'app.
- **2026-09-30** — **Host, jump host e credenziali modificabili dalle Impostazioni**, senza
  aprire i file a mano (richiesta d'uso: ogni nuovo server voleva due modifiche coordinate
  nel config, tunnel sul jump host e voce Host, fatte in Blocco note).
  - *Impostazioni → Host*: indirizzi di `login_test` / `login_prod` e tabella degli host
    con ricerca. **Aggiungi host** chiede ambiente, nome, descrizione, server di
    destinazione (vuoto = uguale al nome), porta SSH e porta locale (vuoto = la prima
    libera). Crea `LocalForward <porta> <server>:22` sul jump host e il blocco
    `Host … HostName localhost / Port <porta>` in fondo alla sezione. **Modifica**
    (anche rinomina, che aggiorna i preferiti) ed **Elimina** agiscono su entrambi.
  - Nuovo `ssh/ssh_config_editor.py` (`SshConfigDocument`). Modifica solo le righe
    coinvolte copiandone l'indentazione: commenti, tab, CRLF/BOM, blocchi `Host *it1tf*`
    dei DB e note restano identici (verificato sul config reale: il diff contiene solo le
    righe attese e `ssh -G` legge i nuovi tunnel). Validazioni: nome unico, porte libere
    anche rispetto ai tunnel DB wildcard, descrizione che non nomina l'altro ambiente
    (altrimenti il parser cambierebbe sezione).
  - *Sicurezza del file*: si scrive solo con **Salva**; prima `config.bak`, poi la
    sostituzione atomica. Se il config è cambiato su disco dopo l'apertura delle
    Impostazioni il salvataggio si rifiuta invece di sovrascrivere.
  - *Impostazioni → Utente e password*: nome utente e password (mascherata, con
    mostra/nascondi) al posto di Blocco note. `ConfigLoader.save_maven_credentials` cambia
    solo il testo di `<username>`/`<password>` del primo `<server>` di `~/.m2/settings.xml`
    (con escaping XML) e lascia intatto il resto, anche una vera configurazione Maven.
  - **Trovato nel config reale**: `travelit1pe05` è definito **due volte** in PROD
    (porta 3229 e porta 3238, verso `travelit1te05`). ssh usa solo il primo blocco. La
    tabella lo segnala ("definito due volte!") e non lo modifica: va corretto a mano.
  - Test: nuovo `tests/test_config_editor.py` (55 controlli: modello, aggiunta, modifica,
    eliminazione, validazioni, CRLF/BOM, backup, modifica concorrente, credenziali,
    Salva/Annulla del dialog), tutto su file temporanei.
- **2026-09-30** — **Tema scuro One Half Dark** per le finestre dell'app (Cerca host,
  Cerca file, Impostazioni), attivabile in *Impostazioni → Generale → "Tema scuro (One
  Half Dark)"* (preferenza `dark_theme`, default spento). Palette dello schema One Half
  Dark di Windows Terminal (sfondo `#282c34`, testo `#dcdfe4`, accento blu `#61afef`,
  verde/giallo/rosso per TEST/PROD/errori). Il font resta Segoe UI in entrambi i temi.
  - Si applica al salvataggio, senza riavvio: tutte le finestre aperte vengono ridipinte,
    barra del titolo compresa (`DwmSetWindowAttribute`, dark mode immersiva).
  - *Come*: nuovo modulo `gui/theme.py`. I widget non ricevono più colori fissi ma un
    **ruolo** (`theme.style(w, "button")`); `theme.use(dark)` riapplica la palette a tutti.
    I colori che dipendono dallo stato (stato in errore, pulsante Interrompi, etichetta
    TEST/PROD) cambiano ruolo con `set_role`. Le costanti `_C_*` di `search_dialog.py` e
    `file_search_dialog.py` sono state rimosse.
  - I widget ttk usano il tema nativo `vista` in chiaro (aspetto invariato) e `clam` in
    scuro, l'unico che accetta colori. Le `tk.Scrollbar` (native, non colorabili) sono
    diventate `ttk.Scrollbar`; il bordo di campi e liste è ora un `highlight` di 1 px
    (grigio, blu sul campo col focus) al posto del `relief="solid"` nero.
  - La lista a tendina delle combobox è creata una volta sola da ttk: viene ricolorata
    esplicitamente (`ttk::combobox::PopdownWindow`).
  - Non toccati: menu tray (nativo Windows), dialog di sistema (Salva con nome, conferme),
    dialog del token Init (WinForms) e schema colori delle console PROD.
  - Test: in `test_theme_file_search.py` il cambio tema a finestra aperta ridipinge sfondo,
    stato in errore e righe separatore, in entrambe le direzioni. `ssh_connection.gui.theme`
    aggiunto agli hidden import di `build_release.py` / `build_debug.py` e degli spec.
- **2026-09-30** — **"Cerca file": navigazione delle cartelle come WinSCP** (richiesta
  d'uso: con la sola ricerca non si vedevano cartelle e percorsi). La lista mostra la
  cartella corrente: prima le cartelle, poi i file, con `..` in cima; i link simbolici
  sono segnati con `→`.
  - *Muoversi*: doppio clic o Invio su una cartella per entrarci; `..`, Backspace o ▲
    per salire; ◀ (o Alt+←) per tornare alla cartella precedente; ⌂ per la home; ⟳ per
    aggiornare. Il percorso si può scrivere nella barra (Invio). La tendina propone le
    ultime cartelle visitate o cercate su quell'host.
  - *Link simbolici*: vengono seguiti per capire se puntano a una cartella, e il percorso
    resta quello **logico** (`/app/nets/batchcommon/files/clear`), come in WinSCP, non
    quello fisico a cui risolvono. Un link rotto compare come file da 0 byte.
  - *Filtro*: scrivendo nel campo **Nome** si filtra la cartella corrente in locale,
    senza richieste al server. **Invio** o **Cerca** lanciano la ricerca remota
    (`find -L`) *a partire dalla cartella corrente*. I risultati mostrano la colonna
    Cartella e una barra "Risultati della ricerca in …" con "✕ Torna alla cartella".
    **"Vai alla cartella"** apre la cartella del risultato con il file selezionato.
  - *File*: doppio clic apre in Blocco note dalla copia temporanea, come prima. Scarica e
    Copia percorso funzionano anche sulle cartelle (solo copia percorso).
  - *Apertura*: scelto un host, la sua ultima cartella (o `~`) viene elencata da sola
    **solo se il tunnel è già attivo** (`route_ready`, probe TCP sulla porta locale).
    Scorrere la lista degli host non deve mai aprire un login con token. Se il tunnel è
    giù, la riga di stato lo dice e un'azione esplicita (Invio sull'host, ⟳, un percorso)
    si connette come un terminale, aprendo il login se serve. L'elenco automatico parte
    400 ms dopo la scelta, così digitare nel filtro host non connette a ogni tasto.
  - *Cambio host durante un'operazione lenta*: ora è permesso. La sessione vecchia viene
    chiusa e il suo risultato tardivo scartato. Lo stato "occupato" è legato alla sessione
    (`_busy_session`). `RemoteSession.close()` marca la sessione come chiusa e un
    `connect()` ancora in corso su un worker non tiene la connessione (niente
    connessioni orfane).
  - *Come*: `RemoteSession.listdir()` / `resolve_folder()` via SFTP (`listdir_attr`, più
    `stat` solo per i link), sulla stessa connessione paramiko. `RemoteFile` ha
    `is_dir` / `is_link`. Icone cartella/file disegnate con `PhotoImage`, perché Tk 8.6
    non disegna emoji fuori dal piano BMP.
  - *Incidente durante lo sviluppo*: una prova dal vivo, lanciata mentre l'app era chiusa
    per la build e quindi con i tunnel giù, ha aperto una console di login `login_test`
    (`ensure_route`). L'ho chiusa subito. Da qui la regola: l'elenco automatico e le prove
    sul server si fanno solo se `route_ready`. Nella stessa prova è emerso il bug di
    `connect()`, che chiamando `close()` per ripulire si marcava come chiuso: ora usa
    `_close_client()`.
  - Test: filesystem finto per navigazione, filtro locale, ricerca e "Vai alla cartella",
    errori, tunnel giù, cambio host durante un elenco lento; `listdir` con SFTP finto
    (link a cartella, link rotto, permessi, percorsi logici). 94 controlli, stabili su 3
    esecuzioni. Il crash dump del grep di Git generato dal test ora finisce nella
    cartella temporanea e non più nel repository.
- **2026-09-30** — **"Cerca file": pannello host come il popup "Cerca host"** (feedback
  d'uso: mancavano il filtro Tutti/TEST/PROD e i preferiti/recenti, e la combo scrivibile
  non si capiva). A sinistra ora c'è un campo di ricerca, la combo Tutti/TEST/PROD e la
  lista con le sezioni **★ Preferiti**, **Recenti**, **TEST** e **PROD**.
  - Digitando, la lista si filtra e l'host più pertinente diventa quello attivo.
  - ↑/↓ e PagSu/PagGiù saltano i separatori; il clic sceglie l'host; Invio passa al nome
    file; **Ctrl+D** aggiunge o toglie dai preferiti, gli stessi del menu e del popup.
  - Il filtro d'ambiente parte da quello salvato dal popup (`search_env`). Se l'host
    richiesto con Ctrl+F non vi rientra, si allarga a "Tutti".
  - Una ricerca riuscita aggiunge l'host ai Recenti, gli stessi di menu e popup.
  - Il focus va sul nome file se l'host arriva da Ctrl+F, altrimenti sul campo host.
  - Mentre un'operazione è in corso l'host non si può cambiare.
  - *Come*: la costruzione delle righe (sezioni, filtro, ordinamento per rilevanza), il
    disegno nella `Listbox` e la navigazione che salta i separatori sono stati estratti da
    `SearchPopup` in funzioni condivise di `search_dialog.py`: `build_host_rows`,
    `fill_host_listbox` e `next_host_row`. Popup e "Cerca file" si comportano in modo
    identico. La vecchia combo host scrivibile (`matching_hosts` / `_commit_host`) è
    rimossa.
  - Ritocchi: riga di aiuto del nome file accorciata (era tagliata) e colonne dei
    risultati ristrette (la colonna "Modificato" usciva dal bordo). Finestra larga 1000 px.
- **2026-09-30** — **Host in ordine alfabetico** in ogni sezione TEST/PROD: menu tray,
  popup "Cerca host", Impostazioni (preferiti) e "Cerca file". Prima seguivano l'ordine
  di `~/.ssh/config`, scomodo con quasi 30 host PROD.
  - Il jump host (`login_*`) resta in cima alla sezione: è il punto d'ingresso
    dell'ambiente. Gli altri sono ordinati senza distinguere maiuscole e minuscole.
  - Un host ripetuto nel config compare una volta sola: `travelit1pe05`, definito due
    volte in PROD, appariva doppio nel menu.
  - L'ordine è applicato in un solo punto, `SshConfigParser.sort_hosts()`, chiamato a fine
    `parse_ssh_config()`. Nessun altro uso del parser dipende dall'ordine (jump host,
    tunnel, monitor). Preferiti e Recenti mantengono il loro ordine.
- **2026-09-28** — **Console PROD riconoscibili a colpo d'occhio.** Ogni terminale ha
  il titolo `[TEST] host` / `[PROD] host`. Le console PROD usano lo schema colori
  **Ubuntu-ColorScheme** di Windows Terminal (sfondo melanzana `#300A24`) e si aprono
  con un banner `PRODUZIONE - host`.
  *Perché*: serve a non scambiare una sessione PROD per una TEST prima di lanciare un
  comando.
  - *Come*: il launcher antepone a `ssh` qualche istruzione PowerShell
    (`_console_preamble`) che imposta il titolo e manda le sequenze OSC 4 (palette a 16
    colori) e OSC 10/11/12 (colori *predefiniti* di testo, sfondo e cursore). Poi pulisce
    lo schermo. Si cambiano i colori predefiniti, non l'attributo corrente, così anche
    `SGR 0` e `clear` del server remoto restano nel tema. Funziona in Windows Terminal e
    in conhost. Il terminale si lancia sempre allo stesso modo: il PID della console
    resta noto, quindi iniezione e tracking non cambiano.
  - ESC e BEL non possono viaggiare sulla riga di comando: vengono scritti come `|` e
    `!` e ripristinati da PowerShell con `.Replace()`.
  - *Impostazioni → Generale → "Tema delle console PROD"*: "Nessuno", gli schemi
    integrati e quelli definiti in `settings.json` di Windows Terminal. Il valore è
    salvato nella preferenza `prod_console_theme`. Le console nascoste di Init non
    ricevono il preambolo.
  - *Scelte*: il primo tentativo (sfondo rosso pieno) è stato scartato perché troppo
    aggressivo. Tra i temi di prova (ambra, blu, bordeaux, ardesia) è stato preferito lo
    schema Ubuntu di Windows Terminal. Le TEST hanno solo il titolo, per massimo
    contrasto con PROD.
  - *Limite noto*: una shell remota il cui `PS1` imposta il titolo lo sovrascrive. I
    colori invece restano.
- **2026-09-28** — **Finestra "Cerca file"** (voce di menu **"Cerca file..."**, oppure
  **Ctrl+F** sull'host selezionato nel popup "Cerca host"). Cerca file per nome, ed
  eventualmente per contenuto, in una cartella di un host. Il testo digitato si cerca
  come "contiene", altrimenti vale con caratteri jolly (`*.log`); c'è l'opzione
  sottocartelle. I risultati mostrano nome, cartella, dimensione e data, dal più recente.
  - *Host*: campo scrivibile con filtro mentre si digita, come nel popup host (feedback
    d'uso: la sola tendina era scomoda). Invio sceglie la corrispondenza migliore;
    freccia giù apre la lista filtrata. Aprire la tendina non conferma l'host.
  - *Apri* (doppio clic o Invio) funziona come in WinSCP: scarica il file in una
    cartella temporanea univoca (`%TEMP%/SSH-Connection-Manager/<host>/<id>/`), lo apre
    in Blocco note e **cancella la copia quando Blocco note si chiude**. È una copia in
    sola lettura: le modifiche non vengono mai ricaricate sul server, per sicurezza in
    PROD. Gli archivi e i documenti (`.gz`, `.zip`, `.pdf`...) si aprono col programma
    associato. Oltre 50 MB viene chiesta conferma.
  - Se Blocco note passa il file a una finestra già aperta (schede di Windows 11), il
    processo termina subito e la copia non si può cancellare in quel momento: la pulisce
    `purge_open_dir()` all'uscita dall'app e al successivo avvio.
  - *Scarica...* salva dove si vuole (default Download). *Copia percorso* (anche Ctrl+C).
    *Interrompi* chiude il comando remoto in corso.
  - Le cartelle cercate sono ricordate per host (preferenza `file_search_paths`, le
    ultime 10) e proposte nella combo.
  - *Come*: nuovo modulo `ssh/remote_files.py` basato su **paramiko** (nuova
    dipendenza). I terminali sono interattivi, mentre qui serve l'*output* di `find` più
    un trasferimento SFTP. OpenSSH per Windows non ha ControlMaster, e SSH_ASKPASS
    richiederebbe di passare la password a un processo esterno. Il percorso di rete è
    quello dei terminali: `SshLauncher.ensure_route()` (estratto da `_connect_sync`)
    apre il jump host se serve e attende il tunnel. `ssh -G` risolve HostName e Port
    (es. `localhost:2222`). Le credenziali vengono da `ConfigLoader`. `known_hosts` si
    legge in sola lettura: una chiave diversa è rifiutata, un host sconosciuto è
    accettato come fa il launcher.
  - I jump host (`login_*`) sono esclusi: il loro login chiede il token 2FA.
  - Testato su `stlit1tf01` (TEST): connessione in 1,7 s, `/var/log` elencato in
    1,6 s, download SFTP, copia temporanea rimossa alla chiusura di Blocco note.
  - Un'anteprima delle ultime righe è stata provata e poi tolta su richiesta: basta aprire
    il file.
  - Test: `tests/test_theme_file_search.py` (63 controlli), con lo script `find` eseguito
    davvero con `sh`. *Nota (2026-09-29)*: il GNU grep 3.0 di Git per Windows va in
    abort con `-i -F` insieme. Il controllo sul filtro per contenuto viene quindi saltato
    se il grep locale è difettoso. Sul server (grep 2.20) il filtro è verificato dal vivo.
- **2026-09-28** — Build: `paramiko` aggiunto a `requirements.txt`, `setup.py` e alle
  dipendenze degli script di build. Nuovi hidden import PyInstaller:
  `file_search_dialog`, `remote_files`, `console_themes`, `paramiko`.
- **2026-09-30** — Build **release e debug** con navigazione cartelle, fix link simbolici,
  pannello host e ordinamento alfabetico (`dist/SSH-Connection-Manager.exe`, `.zip`,
  `-DEBUG.exe`). Moduli nuovi e `paramiko` verificati in entrambi i `PYZ`.
- **2026-09-30** — Build release con console PROD a tema e "Cerca file"
  (`dist/SSH-Connection-Manager.exe` e `.zip`). Verificato nel `PYZ` che i nuovi
  moduli e `paramiko` siano inclusi. Il primo tentativo era fallito perché l'exe in
  esecuzione nella tray era bloccato ("Accesso negato"): prima del build va chiusa
  l'app.
- **2026-09-30** — Build release (`dist/SSH-Connection-Manager.exe`) con UI in stile
  Windows 11, tema scuro, finestre nitide (DPI), editor di host / jump host / credenziali
  nelle Impostazioni e finestra del token di Init istantanea. Verificato nel `PYZ` che
  `gui.theme`, `gui.widgets`, `gui.token_dialog`, `ssh.ssh_config_editor` e `PIL.ImageTk`
  siano inclusi. Prova di avvio con `--autostart`: tray avviata senza errori di import.
  L'unico errore nel log, `RegisterHotKey` 1409, veniva dall'istanza avviata da
  sorgente (`py run.py`) che teneva già la scorciatoia. `.zip` e `-DEBUG.exe` non
  rigenerati.

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
