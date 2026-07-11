"""
scripts/nieuws_sync.py
------------------------------------------------------------------
Draait in GitHub Actions (zie .github/workflows/nieuws-sync.yml).
Haalt de Ajax-RSS-feed van Voetbalprimeur.nl op en schrijft de laatste
5 artikelen weg naar data/nieuws.json — dat bestand wordt bij elke run
VOLLEDIG OVERSCHREVEN (geen historie), en door de workflow teruggecommit
naar de repo. De website leest dit bestand via raw.githubusercontent.com,
zelfde principe als data/ajax_schedule.json.
------------------------------------------------------------------
"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

FEED_URL = "https://www.voetbalprimeur.nl/feed/news.xml?tag=ajax"
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "data/nieuws.json")
AANTAL_ARTIKELEN = 5


def haal_artikelen_op() -> list[dict]:
    res = requests.get(FEED_URL, timeout=15, headers={"User-Agent": "J-Poule/1.0 (+https://github.com)"})
    res.raise_for_status()
    root = ET.fromstring(res.content)

    artikelen = []
    for item in root.findall("./channel/item")[:AANTAL_ARTIKELEN]:
        titel = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date_raw = item.findtext("pubDate")

        try:
            gepubliceerd_op = parsedate_to_datetime(pub_date_raw).astimezone(timezone.utc).isoformat() if pub_date_raw else None
        except (TypeError, ValueError):
            gepubliceerd_op = None

        if titel and link:
            artikelen.append({"titel": titel, "link": link, "gepubliceerd_op": gepubliceerd_op})

    return artikelen


def save(artikelen: list[dict]) -> None:
    payload = {
        "bijgewerkt_op": datetime.now(timezone.utc).isoformat(),
        "artikelen": artikelen,
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[save] {len(artikelen)} artikelen weggeschreven naar {OUTPUT_PATH}.")


def main() -> None:
    artikelen = haal_artikelen_op()
    save(artikelen)


if __name__ == "__main__":
    main()
