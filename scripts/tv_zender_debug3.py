"""
scripts/tv_zender_debug3.py
------------------------------------------------------------------
TIJDELIJK diagnose-script: toont hoe sport-tv-gids.nl de aankomende
wedstrijden echt weergeeft (datumnotatie, teamnamen, zenders), en wat de
scraper daar nu van maakt. Bedoeld om te achterhalen waarom een
specifieke wedstrijd (bv. Ajax-NEC) niet wordt opgepikt.
Verwijderen zodra dit is opgehelderd.
------------------------------------------------------------------
"""
import re

import requests
from bs4 import BeautifulSoup

import tv_zender_sync as s


def main() -> None:
    resp = requests.get(s.TEAM_PAGINA, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    kop = soup.find(string=re.compile(r"Volgende wedstrijden live op\s*TV", re.IGNORECASE))
    if not kop:
        print("Kop 'Volgende wedstrijden live op TV' NIET gevonden.")
        return

    container = kop.find_parent()
    for _ in range(6):
        if len(s._DATUM_PATROON.findall(container.get_text(" "))) >= 2:
            break
        if container.parent is None:
            break
        container = container.parent

    print("=== Datum-achtige teksten op de pagina (zo schrijft de site de maand) ===")
    tekst = container.get_text(" ")
    for d in sorted(set(re.findall(r"\d{1,2}\.\s*[A-Za-z]{3,}", tekst))):
        print(f"  {d!r}")

    print()
    print("=== Lineaire dump (T = tekst, Z = zenderlogo) ===")
    for node in container.descendants:
        naam = getattr(node, "name", None)
        if naam in ("h1", "h2", "h3") and "Recente resultaten" in node.get_text():
            break
        if isinstance(node, str):
            t = node.strip()
            if t:
                ouder = node.parent.name if node.parent else "?"
                print(f"  T[{ouder}] {t[:90]!r}")
        elif naam == "img" and "/sportzender/" in (node.get("src") or ""):
            print(f"  Z      title={node.get('title')!r}")

    print()
    print("=== Wat de scraper er nu van maakt ===")
    for w in s.haal_op():
        print(f"  {w['dag']}-{w['maand']} | {w['tegenstander']} | {[z['naam'] for z in w['zenders']]}")


if __name__ == "__main__":
    main()
