"""
nieuws_sync.py
------------------------------------------------------------------
Haalt data/nieuws.json op vanaf GitHub (via raw.githubusercontent.com,
dat op de gratis PythonAnywhere-whitelist staat — zelfde principe als
github_sync.py voor het wedstrijdprogramma). Wordt gebruikt door de
nieuwsticker die op elke pagina bovenin staat (zie base.html).

Houdt de laatste ophaal in het geheugen (per proces), ververst hooguit
elke NIEUWS_MIN_INTERVAL_S seconden — een ticker hoeft niet op elk
paginabezoek een nieuwe HTTP-call te doen.

Faalt het ophalen (GitHub onbereikbaar, whitelist-issue, etc.), dan
toont de ticker gewoon de laatst bekende artikelen (of niets, bij de
allereerste keer) — nooit een harde fout voor de bezoeker.
------------------------------------------------------------------
"""

from __future__ import annotations

import os
import time

import requests

GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "<jouwgebruikersnaam>")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "j-poule-web")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
NIEUWS_DATA_URL = os.environ.get(
    "NIEUWS_DATA_URL",
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/data/nieuws.json",
)
NIEUWS_MIN_INTERVAL_S = int(os.environ.get("NIEUWS_SYNC_MIN_INTERVAL_S", "300"))  # 5 minuten

_cache: list[dict] = []
_laatste_poging = 0.0


def get_nieuws() -> list[dict]:
    global _laatste_poging, _cache
    nu = time.time()
    if (nu - _laatste_poging) < NIEUWS_MIN_INTERVAL_S:
        return _cache
    _laatste_poging = nu
    try:
        resp = requests.get(NIEUWS_DATA_URL, timeout=8)
        resp.raise_for_status()
        payload = resp.json()
        _cache = payload.get("artikelen") or []
    except Exception as e:
        print(f"[nieuws_sync] ophalen mislukt, toon laatst bekende artikelen: {e}")
    return _cache
