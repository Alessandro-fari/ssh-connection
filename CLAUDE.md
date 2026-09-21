# SSH Connection Manager — istruzioni di progetto

## Regola obbligatoria: cronologia delle modifiche

**Ogni singola modifica al progetto (codice, build, configurazione, documentazione)
deve aggiornare `CHANGELOG.md`** aggiungendo una voce sotto `[Non rilasciato]` con:
- la data (formato `YYYY-MM-DD`),
- cosa è cambiato e perché,
- eventuali note, decisioni prese o problemi noti emersi.

Se la modifica tocca l'architettura (nuovi moduli, nuovi flussi, cambio di
responsabilità tra componenti), aggiornare anche `docs/ARCHITECTURE.md`.

## Riferimenti

- `docs/ARCHITECTURE.md` — architettura e funzionamento interno dell'applicazione.
- `CHANGELOG.md` — cronologia di cambiamenti, miglioramenti e note.
- `README.md` — funzionalità, installazione, configurazione utente.

## Note ambiente

- Solo Windows: le API Win32 (iniezione console, bitmap menu) sono usate via `ctypes`.
- Python si lancia con `py` (il comando `python` potrebbe non essere disponibile).
- Usare l'OpenSSH di Windows (`C:\Windows\System32\OpenSSH\ssh.exe`), non quello di
  Git: quest'ultimo risolve la config via `HOME`, che punta altrove in questo ambiente.
- Build exe: `py build_release.py`; ricordare gli hidden import nello spec PyInstaller
  (`win32_menu_bitmaps`).
