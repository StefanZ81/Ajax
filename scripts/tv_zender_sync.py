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

# sport-tv-gids.nl gebruikt op sommige plekken nog een verouderde zendernaam
# die niet meer overeenkomt met de huidige indeling. Handmatig bevestigde
# correcties (naam + logo), toegepast ná het scrapen, vóórdat de data wordt
# opgeslagen. "Ziggo Voetbal" was de oude naam van het huidige "Ziggo Sport 2",
# maar de bron koppelt dat label nog aan uitzendingen die in werkelijkheid op
# "Ziggo Sport 1" te zien zijn -- vandaar deze correctie op naam én logo.
_ZENDER_CORRECTIES = {
    "Ziggo Voetbal": {
        "naam": "Ziggo Sport 1",
        "logo": "/static/zenders/ziggo-sport-1.jpg",
    },
    # "Ziggo Sport" zonder nummer is, net als "Ziggo Voetbal", een oudere/
    # alternatieve aanduiding voor hetzelfde kanaal.
    "Ziggo Sport": {
        "naam": "Ziggo Sport 1",
        "logo": "/static/zenders/ziggo-sport-1.jpg",
    },
    # Ook als de bron het label zelf al correct "Ziggo Sport 1" noemt, toch
    # ons eigen, geverifieerd actuele logo gebruiken -- het bijbehorende
    # logo-bestand bij de bron bleek namelijk ook verouderd te zijn.
    "Ziggo Sport 1": {
        "naam": "Ziggo Sport 1",
        "logo": "/static/zenders/ziggo-sport-1.jpg",
    },
    "Ziggo Sport 2": {
        "naam": "Ziggo Sport 2",
        "logo": "/static/zenders/ziggo-sport-2.jpg",
    },
    "Ziggo Sport 3": {
        "naam": "Ziggo Sport 3",
        "logo": "/static/zenders/ziggo-sport-3.jpg",
    },
}


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
        raise RuntimeError(
            "Kop 'Volgende wedstrijden live op TV' niet gevonden -- de opmaak van "
            "sport-tv-gids.nl is vermoedelijk gewijzigd, de scraper moet worden aangepast."
        )

    kop_element = kop.find_parent()
    if not kop_element:
        raise RuntimeError("Kon geen bovenliggend element bij de kop vinden.")

    # BELANGRIJK (bevestigd via handmatige diagnose op de echte pagina):
    # wedstrijden staan NIET elk in hun eigen kaart/wrapper -- datum, teams
    # en zenderlogo's van ALLE aankomende wedstrijden staan plat naast
    # elkaar in dezelfde grote lijst-container. Daarom reconstrueren we
    # elke wedstrijd door de container in documentvolgorde te doorlopen en
    # alles wat ná een datum komt bij die datum te groeperen, tot de
    # eerstvolgende datum begint.
    container = kop_element
    for _ in range(6):
        if len(_DATUM_PATROON.findall(container.get_text(" "))) >= 2:
            break
        if container.parent is None:
            break
        container = container.parent

    resultaat = []
    huidige: dict | None = None

    for node in container.descendants:
        naam_tag = getattr(node, "name", None)

        if naam_tag in ("h1", "h2", "h3") and "Recente resultaten" in node.get_text():
            break

        if isinstance(node, str):
            tekst = node.strip()
            datum_match = _DATUM_PATROON.search(tekst)
            # Alleen een tekstnode die (nagenoeg) UITSLUITEND de datum is
            # telt als een nieuw wedstrijd-startpunt -- dat voorkomt dat een
            # datum die toevallig ergens middenin een langere zin voorkomt
            # verkeerd als nieuwe wedstrijd wordt gezien.
            if datum_match and len(tekst) <= len(datum_match.group(0)) + 15:
                if huidige and huidige["tegenstander"] and huidige["zenders"]:
                    resultaat.append(huidige)
                huidige = {
                    "dag": int(datum_match.group(1)),
                    "maand": _MAANDEN[datum_match.group(2).lower()],
                    "tegenstander": None,
                    "_teams": [],
                    "zenders": [],
                }
            continue

        if huidige is None:
            continue

        if naam_tag in ("h6", "h5", "b", "strong"):
            tekst = node.get_text(strip=True)
            if tekst and tekst.lower() != "ajax" and tekst not in huidige["_teams"]:
                huidige["_teams"].append(tekst)
                huidige["tegenstander"] = huidige["_teams"][0]

        elif naam_tag == "img" and _is_zenderlogo(node):
            zendernaam = node.get("title")
            if zendernaam and zendernaam not in [z["naam"] for z in huidige["zenders"]]:
                src = node.get("src") or ""
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://sport-tv-gids.nl" + src

                if zendernaam in _ZENDER_CORRECTIES:
                    correctie = _ZENDER_CORRECTIES[zendernaam]
                    zendernaam, src = correctie["naam"], correctie["logo"]
                    # Kan na correctie alsnog een duplicaat zijn (bv. als de
                    # juiste naam toevallig al apart in de lijst stond) --
                    # dan niet nogmaals toevoegen.
                    if zendernaam in [z["naam"] for z in huidige["zenders"]]:
                        continue

                huidige["zenders"].append({"naam": zendernaam, "logo": src})

    if huidige and huidige["tegenstander"] and huidige["zenders"]:
        resultaat.append(huidige)

    for w in resultaat:
        del w["_teams"]

    return resultaat

def bewaar(wedstrijden: list[dict]) -> None:
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"wedstrijden": wedstrijden}, f, indent=2, ensure_ascii=False)
    print(f"[tv_zender_sync] {len(wedstrijden)} wedstrijd(en) met bekende zender weggeschreven.")


def git_commit_en_push() -> bool:
    def run(cmd):
        return subprocess.run(cmd, capture_output=True, text=True)

    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    run(["git", "add", OUTPUT_PATH])
    diff = run(["git", "diff", "--staged", "--quiet"])
    if diff.returncode == 0:
        print("[tv_zender_sync] Geen wijzigingen -- niets te committen.")
        return True
    run(["git", "commit", "-m", "Tv-zenders bijgewerkt [skip ci]"])
    run(["git", "pull", "--rebase", "--autostash"])
    push = run(["git", "push"])
    if push.returncode != 0:
        print(f"[tv_zender_sync] Push mislukt: {push.stderr.strip()}", file=sys.stderr)
        return False
    return True


if __name__ == "__main__":
    try:
        wedstrijden = haal_op()
    except Exception as e:
        print(f"[tv_zender_sync] Ophalen mislukt: {e}", file=sys.stderr)
        sys.exit(1)  # laat de workflow duidelijk rood zien -- niet stil doorlopen

    bewaar(wedstrijden)
    if not git_commit_en_push():
        sys.exit(1)  # zelfde: een mislukte push mag niet als 'succesvol' ogen
