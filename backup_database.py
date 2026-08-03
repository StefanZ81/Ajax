"""
backup_database.py
------------------------------------------------------------------
Maakt een consistente back-up van jpoule.db en commit/pusht die naar
de GitHub-repo. Bedoeld om 1x per dag te draaien via PythonAnywhere's
Scheduled Tasks (Tasks-tab) — het gratis plan staat precies 1 geplande
taak per dag toe, dus dit past binnen die grens.

Gebruikt sqlite3's ingebouwde .backup()-methode in plaats van een kale
bestandskopie — dat geeft een consistente snapshot, ook terwijl de site
actief in gebruik is (WAL-mode-veilig), in tegenstelling tot 'cp'.

Bewaart bewust maar 1 rollend bestand (backups/jpoule_backup.db) in
plaats van een apart bestand per dag: git zelf bewaart de geschiedenis
al via de commits, dus oudere back-ups blijven gewoon opvraagbaar
zonder dat de repo blijft groeien met losse dagelijkse bestanden (zie
restore_database.py voor hoe je een oudere versie daaruit terughaalt).

LET OP — privacy: dit back-upbestand bevat de (gehashte) wachtwoorden
en e-mailadressen van alle deelnemers. Zorg dat de GitHub-repo privé
staat en blijft.

Instellen als Scheduled Task op PythonAnywhere:
    /home/<gebruikersnaam>/.virtualenvs/jpoule/bin/python \
        /home/<gebruikersnaam>/j-poule-web/backup_database.py

Vereist: 'git push' moet al non-interactief (zonder wachtwoord-prompt)
werken vanuit deze console — dat is het geval zodra je ooit
'git config credential.helper store' hebt gebruikt en een keer
succesvol met een Personal Access Token hebt gepusht (zie
DEPLOY_GRATIS_PLAN.md). Test dat eerst handmatig voordat je op deze
geplande taak vertrouwt.
------------------------------------------------------------------
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(PROJECT_DIR, "jpoule.db"))
BACKUP_PATH = os.path.join(PROJECT_DIR, "backups", "jpoule_backup.db")


def maak_backup() -> None:
    if not os.path.exists(DB_PATH):
        sys.exit(f"Fout: database niet gevonden op {DB_PATH}")

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


def git_commit_en_push() -> None:
    _run(["git", "config", "user.name", "jpoule-backup"])
    _run(["git", "config", "user.email", "backup@jpoule.local"])

    _run(["git", "add", "backups/jpoule_backup.db"])
    diff = _run(["git", "diff", "--staged", "--quiet"])
    if diff.returncode == 0:
        print("[git] Geen wijzigingen in de back-up sinds de vorige run — niets te committen.")
        return

    stempel = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    commit = _run(["git", "commit", "-m", f"Automatische database-back-up {stempel}"])
    print(commit.stdout.strip(), commit.stderr.strip())

    # Meerdere GitHub Actions-workflows committen onafhankelijk naar dezelfde
    # main-branch -- eerst rebasen voorkomt een mislukte push als daar net
    # iets tussendoor is gepusht.
    _run(["git", "pull", "--rebase", "--autostash"])

    push = _run(["git", "push"])
    print(push.stdout.strip(), push.stderr.strip())
    if push.returncode != 0:
        sys.exit(
            "[git] Push mislukt — controleer of 'git push' non-interactief werkt vanuit "
            "deze console (credential.helper). De lokale back-up staat wel klaar in "
            "backups/jpoule_backup.db, alleen het naar GitHub sturen lukte niet."
        )
    print("[git] Back-up succesvol gepusht naar GitHub.")


if __name__ == "__main__":
    maak_backup()
    git_commit_en_push()
