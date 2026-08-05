"""
scripts/tv_zender_sync.py
------------------------------------------------------------------
Haalt op welke tv-zender aankomende Ajax-wedstrijden uitzendt, via
sport-tv-gids.nl/team/ajax, en schrijft dit naar data/tv_zenders.json.
Draait in GitHub Actions (waar wél internettoegang is), zie
.github/workflows/tv-zender-sync.yml.

Neemt zowel de zendernaam als de logo-URL mee (de teampagina gebruikt
de grotere logo's, niet de '-sm-'-varianten die elders op de site
staan, dus dit is meteen de betere resolutie).

LET OP -- dit is webscraping, geen officiële API: sport-tv-gids.nl kan
op elk moment zijn opmaak wijzigen, waardoor dit script niets meer
vindt. Daarom bewust defensief opgezet:
  - Herkent de pagina op zichtbare TEKST (dag/maand-patronen) in
    plaats van op interne CSS-klassen, die sneller wijzigen dan de
    zichtbare tekst.
  - Bij twijfel of een match niet gevonden wordt: sla die wedstrijd
    gewoon over (leeg blijft leeg) in plaats van te gokken.
  - Een volledig mislukte poging (bv. de site is onbereikbaar, of de
    opmaak is compleet gewijzigd) commit gewoon niets nieuws -- de
    vorige, laatst bekende data blijft dan gewoon staan.
------------------------------------------------------------------
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import requests
from bs4 import BeautifulSoup

TEAM_PAGINA = "https://sport-tv-gids.nl/team/ajax"
OUTPUT_PATH = "data/tv_zenders.json"

_MAANDEN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATUM_PATROON = re.compile(
    r"(\d{1,2})\.\s*(" + "|".join(_MAANDEN.keys()) + r")", re.IGNORECASE
)


def _is_zenderlogo(img) -> bool:
    """Onderscheidt een zenderlogo van een teamlogo/sport-icoon (die ook als
    <img> op de pagina staan) -- zenderlogo's staan in de map 'sportzender'."""
    src = img.get("src") or ""
    return "/sportzender/" in src and img.get("title")


def haal_op() -> list[dict]:
    resp = requests.get(TEAM_PAGINA, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    kop = soup.find(string=re.compile(r"Volgende wedstrijden live op\s*TV", re.IGNORECASE))
    if not kop:
        print("[tv_zender_sync] Kop 'Volgende wedstrijden live op TV' niet gevonden -- opmaak gewijzigd?")
        return []

    container = kop.find_parent()
    while container and not container.find(string=re.compile(_DATUM_PATROON)):
        container = container.find_next_sibling()
    if not container:
        print("[tv_zender_sync] Geen wedstrijdblokken gevonden onder de kop.")
        return []

    resultaat = []
    for element in [container] + container.find_all_next():
        if not hasattr(element, "get_text"):
            continue
        tekst = element.get_text(" ", strip=True)
        if "Recente resultaten" in tekst and len(tekst) < 50:
            break

        datum_match = _DATUM_PATROON.search(tekst)
        if not datum_match or not hasattr(element, "find_all"):
            continue

        dag = int(datum_match.group(1))
        maand = _MAANDEN[datum_match.group(2).lower()]

        teamnamen = [
            t.get_text(strip=True) for t in element.find_all(["h6", "h5", "b"])
            if t.get_text(strip=True) and t.get_text(strip=True).lower() != "ajax"
        ]
        tegenstander = teamnamen[0] if teamnamen else None

        zenders = []
        gezien = set()
        for img in element.find_all("img"):
            if not _is_zenderlogo(img):
                continue
            naam = img.get("title")
            if naam in gezien:
                continue
            gezien.add(naam)
            src = img.get("src") or ""
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = "https://sport-tv-gids.nl" + src
            zenders.append({"naam": naam, "logo": src})

        if tegenstander and zenders:
            resultaat.append({
                "dag": dag,
                "maand": maand,
                "tegenstander": tegenstander,
                "zenders": zenders,
            })

    return resultaat


def bewaar(wedstrijden: list[dict]) -> None:
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"wedstrijden": wedstrijden}, f, indent=2, ensure_ascii=False)
    print(f"[tv_zender_sync] {len(wedstrijden)} wedstrijd(en) met bekende zender weggeschreven.")


def git_commit_en_push() -> None:
    def run(cmd):
        return subprocess.run(cmd, capture_output=True, text=True)

    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    run(["git", "add", OUTPUT_PATH])
    diff = run(["git", "diff", "--staged", "--quiet"])
    if diff.returncode == 0:
        print("[tv_zender_sync] Geen wijzigingen -- niets te committen.")
        return
    run(["git", "commit", "-m", "Tv-zenders bijgewerkt [skip ci]"])
    run(["git", "pull", "--rebase", "--autostash"])
    push = run(["git", "push"])
    if push.returncode != 0:
        print(f"[tv_zender_sync] Push mislukt: {push.stderr.strip()}", file=sys.stderr)


if __name__ == "__main__":
    try:
        wedstrijden = haal_op()
    except Exception as e:
        print(f"[tv_zender_sync] Ophalen mislukt: {e}", file=sys.stderr)
        wedstrijden = None

    if wedstrijden is not None:
        bewaar(wedstrijden)
        git_commit_en_push()
