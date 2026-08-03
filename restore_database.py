"""
restore_database.py
------------------------------------------------------------------
Herstelt jpoule.db vanuit een back-upbestand (standaard:
backups/jpoule_backup.db, de laatste automatische back-up).

Draai dit ALLEEN handmatig, in de PythonAnywhere Bash-console — nooit
als geplande taak. Voordat er iets wordt overschreven, wordt de
huidige (mogelijk kapotte) database eerst zelf ook veiliggesteld met
een tijdstempel, zodat een misgreep nooit onomkeerbaar is. Het
back-upbestand wordt bovendien eerst op integriteit gecontroleerd —
bij twijfel wordt er niets overschreven.

Gebruik:
    python3 restore_database.py                     # herstelt vanuit backups/jpoule_backup.db
    python3 restore_database.py pad/naar/ouder.db    # herstelt vanuit een specifiek bestand

Een OUDERE back-up terughalen (git bewaart elke dag een eigen versie
in de commit-geschiedenis, ook al heet het bestand steeds hetzelfde):
    git log --oneline -- backups/jpoule_backup.db
    git show <commit-hash>:backups/jpoule_backup.db > /tmp/oude_backup.db
    python3 restore_database.py /tmp/oude_backup.db
------------------------------------------------------------------
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(PROJECT_DIR, "jpoule.db"))
STANDAARD_BACKUP = os.path.join(PROJECT_DIR, "backups", "jpoule_backup.db")


def controleer_integriteit(pad: str) -> None:
    try:
        conn = sqlite3.connect(pad)
        resultaat = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
    except sqlite3.DatabaseError as e:
        sys.exit(f"Fout: {pad} is geen geldig SQLite-bestand ({e}). Niets hersteld.")
    if resultaat != "ok":
        sys.exit(f"Fout: {pad} faalt de integriteitscontrole ({resultaat}). Niets hersteld.")
    print(f"[herstel] Integriteitscontrole van {pad}: OK")


def herstel(bron_pad: str) -> None:
    if not os.path.exists(bron_pad):
        sys.exit(f"Fout: back-upbestand niet gevonden op {bron_pad}")

    controleer_integriteit(bron_pad)

    if os.path.exists(DB_PATH):
        stempel = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        veiligheidskopie = f"{DB_PATH}.voor_herstel_{stempel}"
        shutil.copy2(DB_PATH, veiligheidskopie)
        print(f"[herstel] Huidige database eerst veiliggesteld als {veiligheidskopie}")

    shutil.copy2(bron_pad, DB_PATH)
    print(f"[herstel] {bron_pad} -> {DB_PATH}")
    print("[herstel] Klaar. Herstart de webapp (Reload op de Web-tab) om de herstelde data te gebruiken.")


if __name__ == "__main__":
    bron = sys.argv[1] if len(sys.argv) > 1 else STANDAARD_BACKUP
    herstel(bron)
