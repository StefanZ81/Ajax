"""
scripts/ajax_data_sync.py
------------------------------------------------------------------
Draait in GitHub Actions (zie .github/workflows/ajax-data-sync.yml),
NIET op PythonAnywhere — dit ontwijkt precies de outbound-internet-
beperking van het gratis PythonAnywhere-plan, want GitHub Actions
heeft onbeperkt internet.

Wat het doet, elke keer dat het draait:
  1. Eén keer per dag: volledige ververing van het Ajax-programma
     (Eredivisie + Europese toernooien, met dezelfde 1-september-
     grens als eerder om voorrondes/play-offs uit te sluiten).
  2. Tijdens een wedstrijd: gerichte extra opvraging van precies
     die ene wedstrijd, rond T+50 min (ruststand) en T+115 min
     (eindstand) na aftrap — niet vaker, om de API-quota te sparen.

Schrijft alles naar data/ajax_schedule.json — dit bestand wordt
VOLLEDIG OVERSCHREVEN bij elke run (geen historie, alleen de
actuele stand van zaken), en door de workflow teruggecommit naar
de repo. De website leest dit bestand via:
    https://raw.githubusercontent.com/<jouw-org>/<jouw-repo>/main/data/ajax_schedule.json
------------------------------------------------------------------
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests

API_BASE = "https://v3.football.api-sports.io"
API_KEY = os.environ["API_FOOTBALL_KEY"]

# Eenmalig opgezocht via GET /teams?search=Ajax (team uit country "Netherlands").
AJAX_TEAM_ID = int(os.environ.get("AJAX_TEAM_ID", "194"))

OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "data/ajax_schedule.json")

LIVE_CODES = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT"}
AFGELOPEN_CODES = {"FT", "AET", "PEN"}
EUROPESE_TOERNOOIEN = {"UEFA Champions League", "UEFA Europa League", "UEFA Conference League"}
VOORRONDES_EINDIGEN_MAAND = 9  # vanaf 1 september telt een Europese wedstrijd pas mee

# Precies deze twee momenten na aftrap worden extra bevraagd (zie opdracht):
PROBE_MOMENTEN = [
    (50, "rust"),   # ruim na de eerste helft (45 min + wat marge)
    (115, "eind"),  # 90 min + rust + ruime marge voor blessuretijd/verlenging
]
PROBE_MARGE = timedelta(minutes=5)  # cron draait elke 5 min, dus dit dekt het venster af


# ---------------- API-Football ----------------

def api_football(path: str) -> list[dict]:
    res = requests.get(f"{API_BASE}{path}", headers={"x-apisports-key": API_KEY}, timeout=15)
    res.raise_for_status()
    data = res.json()
    if data.get("errors"):
        raise RuntimeError(f"API-Football gaf een fout terug: {data['errors']}")
    return data["response"]


def _map_status(code: str) -> str:
    if code in LIVE_CODES:
        return "live"
    if code in AFGELOPEN_CODES:
        return "afgelopen"
    return "gepland"


def _normaliseer_score(s: dict | None) -> dict | None:
    if not s or s.get("home") is None or s.get("away") is None:
        return None
    return {"thuis": s["home"], "uit": s["away"]}


def _seizoen_label(kickoff: datetime) -> str:
    start_jaar = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    return f"{start_jaar}/{start_jaar + 1}"


def map_fixture(f: dict) -> dict:
    kickoff = datetime.fromisoformat(f["fixture"]["date"])
    return {
        "id": f["fixture"]["id"],
        "seizoen": _seizoen_label(kickoff),
        "competitie": f["league"]["name"],
        "ronde": f["league"]["round"],
        "thuis": f["teams"]["home"]["name"],
        "uit": f["teams"]["away"]["name"],
        "kickoff": kickoff.isoformat(),
        "status": _map_status(f["fixture"]["status"]["short"]),
        "rust": _normaliseer_score(f["score"]["halftime"]),
        "eind": _normaliseer_score(f["score"]["fulltime"]),
    }


def is_relevante_competitie(match: dict) -> bool:
    if match["competitie"] == "Eredivisie":
        return True
    if match["competitie"] not in EUROPESE_TOERNOOIEN:
        return False

    kickoff = datetime.fromisoformat(match["kickoff"])
    seizoen_start_jaar = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    grens = datetime(seizoen_start_jaar, VOORRONDES_EINDIGEN_MAAND, 1, tzinfo=kickoff.tzinfo)
    if kickoff < grens:
        return False

    is_kwalificatie = re.search(r"qualif|preliminary|voorronde|play-?off", match.get("ronde") or "", re.IGNORECASE)
    return not is_kwalificatie


# ---------------- Dagelijkse volledige ververing ----------------

def daily_full_refresh() -> list[dict]:
    nu = datetime.now(timezone.utc)
    seizoen = nu.year if nu.month >= 7 else nu.year - 1
    response = api_football(f"/fixtures?team={AJAX_TEAM_ID}&season={seizoen}")
    matches = [m for m in (map_fixture(f) for f in response) if is_relevante_competitie(m)]
    print(f"[daily_full_refresh] {len(matches)} relevante wedstrijden opgehaald.")
    return matches


# ---------------- Gerichte live-probe rond T+50 / T+115 min ----------------

def wedstrijden_binnen_probe_venster(matches: list[dict]) -> list[tuple[int, str]]:
    nu = datetime.now(timezone.utc)
    te_proben = []
    for m in matches:
        if m["status"] == "afgelopen":
            continue
        kickoff = datetime.fromisoformat(m["kickoff"])
        for offset_minuten, doel in PROBE_MOMENTEN:
            moment = kickoff + timedelta(minutes=offset_minuten)
            if moment - PROBE_MARGE <= nu <= moment + PROBE_MARGE:
                te_proben.append((m["id"], doel))
    return te_proben


def probe_fixture(fixture_id: int) -> dict | None:
    response = api_football(f"/fixtures?id={fixture_id}")
    if not response:
        return None
    return map_fixture(response[0])


# ---------------- Lezen/schrijven van de JSON-tabel ----------------

def load_existing() -> dict | None:
    if not os.path.exists(OUTPUT_PATH):
        return None
    with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save(matches: list[dict]) -> None:
    payload = {
        "bijgewerkt_op": datetime.now(timezone.utc).isoformat(),
        "wedstrijden": sorted(matches, key=lambda m: m["kickoff"]),
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[save] {len(matches)} wedstrijden weggeschreven naar {OUTPUT_PATH}.")


def moet_daily_refresh(bestaand: dict | None) -> bool:
    if not bestaand:
        return True
    laatst = datetime.fromisoformat(bestaand["bijgewerkt_op"])
    return laatst.date() != datetime.now(timezone.utc).date()


# ---------------- Hoofdlogica ----------------

def main() -> None:
    bestaand = load_existing()
    matches = bestaand["wedstrijden"] if bestaand else []

    if moet_daily_refresh(bestaand):
        matches = daily_full_refresh()
        save(matches)

    te_proben = wedstrijden_binnen_probe_venster(matches)
    if not te_proben:
        print("[main] Geen wedstrijd binnen een probe-venster deze cyclus — niets te doen.")
        return

    by_id = {m["id"]: m for m in matches}
    for fixture_id, doel in te_proben:
        print(f"[main] Probe fixture {fixture_id} (doel: {doel})...")
        bijgewerkt = probe_fixture(fixture_id)
        if bijgewerkt:
            by_id[fixture_id] = bijgewerkt

    save(list(by_id.values()))


if __name__ == "__main__":
    main()
