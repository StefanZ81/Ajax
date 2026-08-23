"""
scripts/tv_zender_debug2.py
------------------------------------------------------------------
TIJDELIJK diagnose-script -- toont het ruwe 'title'-attribuut van elk
zenderlogo op de teampagina, om te bevestigen of daar een "Live "-
voorvoegsel in zit (zoals bij het hoveren op de website te zien is).
Verwijderen zodra dit is opgehelderd.
------------------------------------------------------------------
"""
import re
import requests
from bs4 import BeautifulSoup

TEAM_PAGINA = "https://sport-tv-gids.nl/team/ajax"


def main() -> None:
    resp = requests.get(TEAM_PAGINA, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    print("=== Alle <img>-tags binnen /sportzender/ met hun ruwe title/alt-attributen ===")
    gezien = set()
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if "/sportzender/" not in src:
            continue
        titel = img.get("title")
        alt = img.get("alt")
        sleutel = (titel, alt, src)
        if sleutel in gezien:
            continue
        gezien.add(sleutel)
        print(f"  src={src!r}")
        print(f"    title={titel!r}")
        print(f"    alt={alt!r}")


if __name__ == "__main__":
    main()
