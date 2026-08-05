"""
scripts/tv_zender_debug.py
------------------------------------------------------------------
TIJDELIJK diagnose-script -- niet voor productiegebruik. Haalt
dezelfde pagina op als tv_zender_sync.py, maar toont in plaats van
netjes te structureren gewoon exact wat er in elk wedstrijdblok staat:
alle kop-tags (h1-h6, b, strong) en alle <img>-tags met hun src/title/
alt. Daarmee kan de 'echte' opmaak van de site worden afgelezen, zodat
tv_zender_sync.py daarop kan worden afgestemd.

Verwijderen zodra tv_zender_sync.py weer correct wedstrijden vindt.
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


def main() -> None:
    resp = requests.get(TEAM_PAGINA, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    kop = soup.find(string=re.compile(r"Volgende wedstrijden live op\s*TV", re.IGNORECASE))
    print("Kop gevonden:", kop is not None)
    if not kop:
        print("STOP -- kop zelf al niet gevonden, dus daar zit het probleem al.")
        return

    kop_element = kop.find_parent()
    print("Kop-element tag:", kop_element.name if kop_element else None)

    aantal_datumblokken = 0
    for element in kop_element.find_all_next():
        if not hasattr(element, "get_text"):
            continue
        tekst = element.get_text(" ", strip=True)
        if "Recente resultaten" in tekst and len(tekst) < 50:
            print("\n-- 'Recente resultaten' bereikt, stoppen --")
            break

        datum_match = _DATUM_PATROON.search(tekst)
        if not datum_match or not hasattr(element, "find_all"):
            continue

        # Alleen het KLEINSTE/eerste element per unieke datumtekst tonen,
        # anders spammen geneste ouders dezelfde info telkens opnieuw.
        aantal_datumblokken += 1
        if aantal_datumblokken > 15:
            print("\n(meer dan 15 datumblokken gezien, waarschijnlijk veel geneste duplicaten -- stop met tonen)")
            break

        print(f"\n=== Blok #{aantal_datumblokken} -- tag <{element.name}>, datum-match: '{datum_match.group(0)}' ===")
        print("Volledige tekst (eerste 150 tekens):", tekst[:150])

        print("Kop-tags (h1-h6, b, strong) hierin:")
        for tag_naam in ["h1", "h2", "h3", "h4", "h5", "h6", "b", "strong", "span"]:
            gevonden = element.find_all(tag_naam)
            if gevonden:
                teksten = [t.get_text(strip=True) for t in gevonden if t.get_text(strip=True)]
                if teksten:
                    print(f"  <{tag_naam}>: {teksten[:6]}")

        print("Afbeeldingen hierin:")
        for img in element.find_all("img")[:8]:
            print(f"  src={img.get('src')!r}  title={img.get('title')!r}  alt={img.get('alt')!r}")


if __name__ == "__main__":
    main()
