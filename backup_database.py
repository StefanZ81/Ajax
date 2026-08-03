"""
backup_database.py
------------------------------------------------------------------
Maakt een consistente back-up van jpoule.db en commit/pusht die naar
de GitHub-repo.

Draait mee op gewone paginabezoeken (net als github_sync.py en
nieuws_sync.py), met een ingebouwde rem van ~24 uur -- niet via een
PythonAnywhere Scheduled Task, want die functie zit sinds 15 januari
2026 alleen nog op gratis accounts die al vóór die datum bestonden, of
op een betaald plan. Deze aanpak werkt op elk gratis account.

Gebruikt sqlite3's ingebouwde .backup()-methode in plaats van een kale
bestandskopie -- dat geeft een consistente snapshot, ook terwijl de site
actief in gebruik is (WAL-mode-veilig), in tegenstelling tot 'cp'.

Bewaart bewust maar 1 rollend bestand (backups/jpoule_backup.db) in
plaats van een apart bestand per dag: git zelf bewaart de geschiedenis
al via de commits, dus oudere back-ups blijven gewoon opvraagbaar
zonder dat de repo blijft groeien met losse dagelijkse bestanden (zie
restore_database.py voor hoe je een oudere versie daaruit terughaalt).

LET OP -- privacy: dit back-upbestand bevat de (gehashte) wachtwoorden
en e-mailadressen van alle deelnemers. Zorg dat de GitHub-repo privé
staat en blijft.

Vereist: 'git push' moet al non-interactief (zonder wachtwoord-prompt)
werken vanuit de PythonAnywhere-console -- dat is het geval zodra je
ooit 'git config credential.helper store' hebt gebruikt en een keer
succesvol met een Personal Access Token hebt gepusht (zie
DEPLOY_GRATIS_PLAN.md). Test dat eerst handmatig:
    python3 backup_database.py
------------------------------------------------------------------
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(PROJECT_DIR, "jpoule.db"))
BACKUP_PATH = os.path.join(PROJECT_DIR, "backups", "jpoule_backup.db")
MIN_INTERVAL_S = int(os.environ.get("BACKUP_MIN_INTERVAL_S", str(20 * 3600)))  # ~1x per dag

_laatste_poging = 0.0


def maak_backup() -> None:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database niet gevonden op {DB_PATH}")

    os.makedirs(os.path.dirname(BACKUP_PATH), exist_ok=True)

    bron = sqlite3.connect(DB_PATH)
    doel = sqlite3.connect(BACKUP_PATH)
    with doel:
        bron.backup(doel)  # consistente, WAL-veilige kopie, ook tijdens gebruik
    doel.close()
    bron.close()
    print(f"[backup] {DB_PATH} -> {BACKUP_PATH}")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True)


def git_commit_en_push() -> bool:
    _run(["git", "config", "user.name", "jpoule-backup"])
    _run(["git", "config", "user.email", "backup@jpoule.local"])

    _run(["git", "add", "backups/jpoule_backup.db"])
    diff = _run(["git", "diff", "--staged", "--quiet"])
    if diff.returncode == 0:
        print("[git] Geen wijzigingen in de back-up sinds de vorige run -- niets te committen.")
        return True

    stempel = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    commit = _run(["git", "commit", "-m", f"Automatische database-back-up {stempel}"])
    print(commit.stdout.strip(), commit.stderr.strip())

    _run(["git", "pull", "--rebase", "--autostash"])

    push = _run(["git", "push"])
    print(push.stdout.strip(), push.stderr.strip())
    if push.returncode != 0:
        print(
            "[git] Push mislukt -- de lokale back-up staat wel klaar in "
            "backups/jpoule_backup.db, alleen het naar GitHub sturen lukte niet.",
            file=sys.stderr,
        )
        return False
    print("[git] Back-up succesvol gepusht naar GitHub.")
    return True


def sync_if_needed(force: bool = False) -> None:
    """Aan te roepen bij elk paginabezoek (zie app.py) -- doet in de
    praktijk door de rem van MIN_INTERVAL_S maar ongeveer 1x per dag
    daadwerkelijk iets. Nooit een paginaverzoek laten mislukken op een
    back-upfout."""
    global _laatste_poging
    nu = time.time()
    if not force and (nu - _laatste_poging) < MIN_INTERVAL_S:
        return
    _laatste_poging = nu
    try:
        maak_backup()
        git_commit_en_push()
    except Exception as e:
        print(f"[backup] mislukt, probeert het later opnieuw: {e}", file=sys.stderr)


if __name__ == "__main__":
    sync_if_needed(force=True)
