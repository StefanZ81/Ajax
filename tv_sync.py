"""
tv_sync.py
------------------------------------------------------------------
Website-kant van de tv-zender-koppeling: haalt data/tv_zenders.json op
(geschreven door scripts/tv_zender_sync.py via GitHub Actions) en zoekt
daarin de zender(s) op voor een specifieke wedstrijd.

Zelfde patroon als nieuws_sync.py: cache van een paar minuten, en een
mislukte ophaalpoging toont gewoon geen zenderinfo in plaats van de
pagina te laten crashen.
------------------------------------------------------------------
"""

from __future__ import annotations

import os
import time

import requests

GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "<jouwgebruikersnaam>")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "j-poule-web")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
BRON_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/data/tv_zenders.json"

CACHE_DUUR_S = 5 * 60

_cache: dict = {"data": [], "opgehaald_op": 0.0}

# sport-tv-gids.nl gebruikt voor sommige clubs een andere naam dan onze
# eigen (van football-data.org afkomstige) naam waarmee de wedstrijd in de
# app wordt getoond. Deze tabel is UITSLUITEND bedoeld om de tv-zender aan
# de juiste wedstrijd te koppelen -- de clubnaam zoals die in de app wordt
# weergegeven, blijft hierdoor altijd ongewijzigd.
_NAAM_ALIASSEN = {
    "Nijmegen": "NEC"
    "N.e.c.": "NEC"
    "N.E.C.": "NEC"
    "N.E.C": "NEC"
}


def _namen_komen_overeen(scraped_naam: str, eigen_naam: str) -> bool:
    if scraped_naam in eigen_naam or eigen_naam in scraped_naam:
        return True
    alias = _NAAM_ALIASSEN.get(scraped_naam)
    return bool(alias) and (alias in eigen_naam or eigen_naam in alias)


def _ververs_cache_indien_nodig() -> None:
    nu = time.time()
    if nu - _cache["opgehaald_op"] < CACHE_DUUR_S:
        return
    try:
        resp = requests.get(BRON_URL, timeout=10)
        resp.raise_for_status()
        _cache["data"] = resp.json().get("wedstrijden", [])
        _cache["opgehaald_op"] = nu
    except Exception as e:
        # Nooit de pagina laten mislukken op een ophaalfout -- gewoon de
        # vorige (of lege) cache laten staan.
        print(f"[tv_sync] ophalen mislukt: {e}")


def zoek_zenders_voor_wedstrijd(match: dict) -> list[dict]:
    """Zoekt de bekende tv-zender(s) voor deze wedstrijd op, aan de hand van
    dag+maand van de aftrap en de naam van de tegenstander. Geeft een lege
    lijst terug als er niets bekend is (of de wedstrijd al is afgelopen --
    het blok hoort dan sowieso niet meer getoond te worden)."""
    if match.get("status") == "afgelopen":
        return []

    _ververs_cache_indien_nodig()

    kickoff = match.get("kickoff")
    if not kickoff:
        return []

    is_thuis = "Ajax" in match["thuis"]
    tegenstander = match["uit"] if is_thuis else match["thuis"]

    for w in _cache["data"]:
        if w["dag"] == kickoff.day and w["maand"] == kickoff.month and _namen_komen_overeen(w["tegenstander"], tegenstander):
            return w["zenders"]
    return []
