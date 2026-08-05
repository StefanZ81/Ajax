"""
reminder_export.py
------------------------------------------------------------------
De "brug" tussen de PythonAnywhere-app (die weet wie de deelnemers zijn)
en GitHub Actions (dat wél e-mail kan versturen, zie
scripts/reminder_sync.py). Schrijft data/reminder_data.json met:
  - alle goedgekeurde deelnemers die reminders willen ontvangen
    (naam, e-mail, uitschrijf-token — NIET het wachtwoord of de hash)
  - alle nog niet gespeelde wedstrijden (ELKE competitie, dus ook
    handmatig toegevoegde Europese wedstrijden — niet alleen wat er in
    ajax_schedule.json staat, want dat is Eredivisie-only)

Commit + pusht dit bestand naar GitHub, net als backup_database.py.
Wordt aangeroepen:
  - periodiek, throttled, bij paginabezoeken (zie app.py)
  - meteen (geforceerd) na een goedkeuring of een uitschrijving, voor
    snellere doorwerking

LET OP — privacy: dit bestand bevat e-mailadressen. Zorg dat de
GitHub-repo privé staat en blijft (wachtwoorden zelf staan hier nooit
in, alleen naam/e-mail/token).
------------------------------------------------------------------
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import git_auth
from db import get_connection

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(PROJECT_DIR, "data", "reminder_data.json")
MIN_INTERVAL_S = int(os.environ.get("REMINDER_EXPORT_MIN_INTERVAL_S", "300"))  # 5 minuten

_laatste_export = 0.0


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True)


def exporteer(seizoen: str) -> None:
    with get_connection() as conn:
        deelnemers = conn.execute(
            """
            SELECT naam, email, uitschrijf_token FROM participants
            WHERE status = 'goedgekeurd' AND ontvangt_reminders = 1
            """
        ).fetchall()
        wedstrijden = conn.execute(
            """
            SELECT id, competitie, thuis, uit, kickoff FROM matches
            WHERE seizoen = ? AND status != 'afgelopen'
            ORDER BY kickoff
            """,
            (seizoen,),
        ).fetchall()

    payload = {
        "deelnemers": [dict(r) for r in deelnemers],
        "wedstrijden": [dict(r) for r in wedstrijden],
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[reminder_export] {len(deelnemers)} deelnemers, {len(wedstrijden)} wedstrijden weggeschreven.")


def _git_commit_en_push() -> bool:
    _run(["git", "config", "user.name", "jpoule-reminder-export"])
    _run(["git", "config", "user.email", "reminder-export@jpoule.local"])

    _run(["git", "add", "data/reminder_data.json"])
    diff = _run(["git", "diff", "--staged", "--quiet"])
    if diff.returncode == 0:
        return True  # geen wijziging, niets te doen -- geen fout

    _run(["git", "commit", "-m", "Reminder-exportdata bijgewerkt [skip ci]"])
    # Meerdere GitHub Actions-workflows committen onafhankelijk naar dezelfde
    # main-branch -- eerst rebasen voorkomt een mislukte push als daar net
    # iets tussendoor is gepusht.
    git_auth.zorg_voor_geauthenticeerde_remote(PROJECT_DIR)
    _run(["git", "pull", "--rebase", "--autostash"])
    push = _run(["git", "push"])
    if push.returncode != 0:
        print(
            f"[reminder_export] git push mislukt.\n"
            f"[reminder_export] stdout: {push.stdout.strip()}\n"
            f"[reminder_export] stderr: {push.stderr.strip()}",
            file=sys.stderr,
        )
    return push.returncode == 0


def sync_if_needed(seizoen: str, force: bool = False) -> None:
    global _laatste_export
    nu = time.time()
    if not force and (nu - _laatste_export) < MIN_INTERVAL_S:
        return
    _laatste_export = nu
    try:
        exporteer(seizoen)
        gelukt = _git_commit_en_push()
        if not gelukt:
            print("[reminder_export] git push mislukt -- probeert het later opnieuw.", file=sys.stderr)
    except Exception as e:
        # Nooit een paginaverzoek laten mislukken op een exportfout.
        print(f"[reminder_export] mislukt: {e}", file=sys.stderr)


if __name__ == "__main__":
    import queries
    sync_if_needed(queries.get_active_season(), force=True)
