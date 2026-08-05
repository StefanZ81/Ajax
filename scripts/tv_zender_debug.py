"""
scripts/tv_zender_debug.py (v2)
------------------------------------------------------------------
TIJDELIJK diagnose-script -- niet voor productiegebruik.

Bleek uit v1: de datum staat in een eigen, geïsoleerd element zonder
teamnamen/logo's erbinnen. Deze versie zoekt daarom vanaf elke
datum-vondst een paar niveaus OMHOOG (ouder, grootouder, ...) en toont
per niveau wat daar te vinden is, zodat duidelijk wordt op welke
hoogte teamnamen én logo's voor het eerst samen voorkomen.
------------------------------------------------------------------
"""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

TEAM_PAGINA = "https://sport-tv-gids.nl/team/ajax"

_MAANDEN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATUM_PATROON = re.compile(
    r"(\d{1,2})\.\s*(" + "|".join(_MAANDEN.keys()) + r")", re.IGNORECASE
)


def toon_niveau(element, diepte: int) -> None:
    teamnamen = []
    for tag_naam in ["h1", "h2", "h3", "h4", "h5", "h6", "b", "strong"]:
        for t in element.find_all(tag_naam):
            tekst = t.get_text(strip=True)
            if tekst:
                teamnamen.append((tag_naam, tekst))

    afbeeldingen = element.find_all("img")
    zender_imgs = [img for img in afbeeldingen if "/sportzender/" in (img.get("src") or "")]

    print(f"  [omhoog {diepte}] tag=<{element.name} class={element.get('class')}>  "
          f"teamnamen={teamnamen[:6]}  aantal_img={len(afbeeldingen)}  aantal_zender_img={len(zender_imgs)}")
    if zender_imgs:
        for img in zender_imgs[:4]:
            print(f"      zender-img: src={img.get('src')!r} title={img.get('title')!r} alt={img.get('alt')!r}")


def main() -> None:
    resp = requests.get(TEAM_PAGINA, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    kop = soup.find(string=re.compile(r"Volgende wedstrijden live op\s*TV", re.IGNORECASE))
    if not kop:
        print("STOP -- kop niet gevonden.")
        return
    kop_element = kop.find_parent()

    geziene_datums = set()
    for element in kop_element.find_all_next():
        if not hasattr(element, "get_text"):
            continue
        tekst = element.get_text(" ", strip=True)
        if "Recente resultaten" in tekst and len(tekst) < 50:
            break

        datum_match = _DATUM_PATROON.search(tekst)
        if not datum_match or not hasattr(element, "find_all"):
            continue
        if len(tekst) > len(datum_match.group(0)) + 15:
            continue

        datum_sleutel = datum_match.group(0)
        if datum_sleutel in geziene_datums:
            continue
        geziene_datums.add(datum_sleutel)

        print(f"\n=== Datum-element gevonden: '{tekst}' (tag=<{element.name}>) ===")
        niveau = element
        for diepte in range(1, 6):
            niveau = niveau.parent
            if niveau is None:
                break
            toon_niveau(niveau, diepte)

        if len(geziene_datums) >= 2:
            break


if __name__ == "__main__":
    main()
