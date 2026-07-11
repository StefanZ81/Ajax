"""
github_sync.py
------------------------------------------------------------------
Haalt data/ajax_schedule.json op vanaf GitHub (via raw.githubusercontent.com,
dat op de gratis PythonAnywhere-whitelist staat) en werkt de lokale
`matches`-tabel bij. Dit is de "andere kant" van scripts/ajax_data_sync.py,
dat in GitHub Actions draait en dat bestand vult.

Wordt aangeroepen vanuit de programma- en wedstrijd-routes in app.py,
met een minimale tussenpoos (GITHUB_SYNC_MIN_INTERVAL_S) zodat niet
elke paginaweergave een nieuwe HTTP-call naar GitHub triggert.

Faalt een sync (GitHub onbereikbaar, whitelist-issue, etc.) dan gaat de
site gewoon door met de laatst bekende lokale data — nooit een harde fout
voor de bezoeker.
------------------------------------------------------------------
"""

from __future__ import annotations

import os
import time

import requests

from db import get_connection

GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "<jouwgebruikersnaam>")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "j-poule-web")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_DATA_URL = os.environ.get(
    "GITHUB_DATA_URL",
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/data/ajax_schedule.json",
)
MIN_INTERVAL_S = int(os.environ.get("GITHUB_SYNC_MIN_INTERVAL_S", "60"))

_laatste_poging = 0.0  # per proces; op PythonAnywhere is dat meestal 1 worker


def sync_if_needed(force: bool = False) -> None:
    global _laatste_poging
    nu = time.time()
    if not force and (nu - _laatste_poging) < MIN_INTERVAL_S:
        return
    _laatste_poging = nu
    try:
        aantal = _sync_now()
        print(f"[github_sync] {aantal} wedstrijden gesynchroniseerd vanuit GitHub.")
    except Exception as e:
        # Nooit de pagina laten breken op een mislukte sync — gewoon doorgaan
        # met wat er al lokaal staat.
        print(f"[github_sync] mislukt, ga door met lokale data: {e}")


def _sync_now() -> int:
    resp = requests.get(GITHUB_DATA_URL, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    wedstrijden = payload.get("wedstrijden") or []
    stand = payload.get("stand") or []

    import queries  # lokale import om circulaire imports met app.py te vermijden

    if stand:
        seizoen = queries.get_active_season()
        with get_connection() as conn:
            conn.execute("DELETE FROM standings WHERE seizoen = ?", (seizoen,))
            for rij in stand:
                conn.execute(
                    "INSERT INTO standings (seizoen, positie, team, punten, gespeeld) VALUES (?, ?, ?, ?, ?)",
                    (seizoen, rij["positie"], rij["team"], rij["punten"], rij.get("gespeeld", 0)),
                )
        queries.auto_vul_seizoensuitkomst(seizoen)

    if not wedstrijden:
        return 0

    with get_connection() as conn:
        for m in wedstrijden:
            conn.execute(
                """
                INSERT INTO matches (id, seizoen, competitie, ronde, thuis, uit, kickoff, status,
                                      uitslag_rust_thuis, uitslag_rust_uit, uitslag_eind_thuis, uitslag_eind_uit)
                VALUES (:id, :seizoen, :competitie, :ronde, :thuis, :uit, :kickoff, :status,
                        :rust_thuis, :rust_uit, :eind_thuis, :eind_uit)
                ON CONFLICT (id) DO UPDATE SET
                    competitie = excluded.competitie,
                    ronde = excluded.ronde,
                    kickoff = excluded.kickoff,
                    status = excluded.status,
                    uitslag_rust_thuis = excluded.uitslag_rust_thuis,
                    uitslag_rust_uit = excluded.uitslag_rust_uit,
                    uitslag_eind_thuis = excluded.uitslag_eind_thuis,
                    uitslag_eind_uit = excluded.uitslag_eind_uit,
                    bijgewerkt_op = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                {
                    "id": m["id"],
                    "seizoen": m["seizoen"],
                    "competitie": m["competitie"],
                    "ronde": m.get("ronde"),
                    "thuis": m["thuis"],
                    "uit": m["uit"],
                    "kickoff": m["kickoff"],
                    "status": m["status"],
                    "rust_thuis": (m.get("rust") or {}).get("thuis"),
                    "rust_uit": (m.get("rust") or {}).get("uit"),
                    "eind_thuis": (m.get("eind") or {}).get("thuis"),
                    "eind_uit": (m.get("eind") or {}).get("uit"),
                },
                # NB: 'oefenwedstrijd' bewust NIET in deze upsert — dat is een
                # handmatige vlag van de beheerder, geen data uit API-Football.
            )

    # Puntenberekening BEWUST buiten het bovenstaande 'with get_connection()'-blok:
    # bereken_en_bewaar_punten() opent zelf ook een verbinding om te schrijven.
    # Twee gelijktijdig open schrijfverbindingen op SQLite (de bovenstaande,
    # nog niet gecommit, én deze) leiden tot "database is locked" — dus eerst
    # volledig committen, dan pas de punten doorrekenen.
    voor_punten = [m["id"] for m in wedstrijden if m["status"] == "afgelopen"]
    if voor_punten:
        for match_id in voor_punten:
            queries.bereken_en_bewaar_punten(match_id)

    return len(wedstrijden)
