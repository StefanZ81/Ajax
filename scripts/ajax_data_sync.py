"""
scripts/ajax_data_sync.py
------------------------------------------------------------------
Draait in GitHub Actions (zie .github/workflows/ajax-data-sync.yml).
Haalt Ajax' Eredivisie-programma en -uitslagen op bij football-data.org
(permanent gratis tier — zie https://www.football-data.org/coverage).

LET OP — bewuste beperking van deze opzet:
football-data.org's gratis tier bevat de Eredivisie, maar GEEN Europa
League of Conference League. Dit script haalt dus alleen Eredivisie-
wedstrijden van Ajax op. Europese wedstrijden (dit seizoen Conference
League) moeten de beheerder er los bij zetten — zie DEPLOY_GRATIS_PLAN.md,
stap 5, voor het commando daarvoor. Dat gebeurt hierdoor niet automatisch
overschreven, want dit script raakt alleen wedstrijden met
competitie == "Eredivisie" aan (zie save()/merge-logica onderaan).

Wat het doet, elke keer dat het draait:
  1. Eén keer per dag: volledige ververing van Ajax' Eredivisie-programma.
  2. Tijdens een wedstrijd: gerichte extra opvraging van precies díe
     wedstrijd, rond T+50 min (ruststand) en T+115 min (eindstand) na
     aftrap — niet vaker, om binnen de 10 verzoeken/minuut te blijven.

Schrijft naar data/ajax_schedule.json — dit bestand wordt bij elke
dagelijkse ververing VOLLEDIG OVERSCHREVEN (geen historie, alleen de
actuele stand van zaken). Bevat uitsluitend Eredivisie-wedstrijden.
Europese wedstrijden voegt de beheerder rechtstreeks toe via het
formulier in het beheerscherm van de website (zie app.py/queries.py) —
die komen dus nooit in dit bestand te staan, en dit script hoeft er
dan ook geen rekening mee te houden: github_sync.py aan de andere kant
werkt alleen de wedstrijd-id's bij die in deze JSON staan, en raakt
nooit rijen aan die er niet in voorkomen.
------------------------------------------------------------------
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import requests

API_BASE = "https://api.football-data.org/v4"
API_KEY = os.environ["FOOTBALL_DATA_API_KEY"]
COMPETITIE_CODE = "DED"  # Eredivisie — enige Ajax-competitie in de gratis tier

OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "data/ajax_schedule.json")

# Precies deze twee momenten na aftrap worden extra bevraagd (zie opdracht):
PROBE_MOMENTEN = [
    (50, "rust"),   # ruim na de eerste helft (45 min + wat marge)
    (115, "eind"),  # 90 min + rust + ruime marge voor blessuretijd/verlenging
]
PROBE_MARGE = timedelta(minutes=5)  # cron draait elke 5 min, dus dit dekt het venster af


# ---------------- football-data.org ----------------

def football_data(path: str) -> dict:
    res = requests.get(f"{API_BASE}{path}", headers={"X-Auth-Token": API_KEY}, timeout=15)
    res.raise_for_status()
    return res.json()


def _map_status(status: str) -> str:
    if status in ("IN_PLAY", "PAUSED"):
        return "live"
    if status in ("FINISHED", "AWARDED"):
        return "afgelopen"
    return "gepland"  # SCHEDULED, TIMED, SUSPENDED, POSTPONED


def _normaliseer_score(s: dict | None) -> dict | None:
    if not s or s.get("home") is None or s.get("away") is None:
        return None
    return {"thuis": s["home"], "uit": s["away"]}


def _seizoen_label(kickoff: datetime) -> str:
    start_jaar = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    return f"{start_jaar}/{start_jaar + 1}"


def map_match(m: dict) -> dict:
    kickoff = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
    return {
        "id": m["id"],
        "seizoen": _seizoen_label(kickoff),
        "competitie": "Eredivisie",
        "ronde": f"Speelronde {m['matchday']}" if m.get("matchday") else None,
        "thuis": m["homeTeam"]["name"],
        "uit": m["awayTeam"]["name"],
        "kickoff": kickoff.isoformat(),
        "status": _map_status(m["status"]),
        "rust": _normaliseer_score(m["score"].get("halfTime")),
        "eind": _normaliseer_score(m["score"].get("fullTime")),
    }


def is_ajax_wedstrijd(m: dict) -> bool:
    return "Ajax" in m["homeTeam"]["name"] or "Ajax" in m["awayTeam"]["name"]


# ---------------- Dagelijkse volledige ververing ----------------

def daily_full_refresh() -> list[dict]:
    nu = datetime.now(timezone.utc)
    seizoen_jaar = nu.year if nu.month >= 7 else nu.year - 1
    data = football_data(f"/competitions/{COMPETITIE_CODE}/matches?season={seizoen_jaar}")
    matches = [map_match(m) for m in data["matches"] if is_ajax_wedstrijd(m)]
    print(f"[daily_full_refresh] {len(matches)} Eredivisie-wedstrijden van Ajax opgehaald.")
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
    try:
        data = football_data(f"/matches/{fixture_id}")
    except requests.HTTPError as e:
        print(f"[probe_fixture] mislukt voor {fixture_id}: {e}")
        return None
    return map_match(data)


def fetch_standings() -> list[dict]:
    data = football_data(f"/competitions/{COMPETITIE_CODE}/standings")
    for groep in data.get("standings", []):
        if groep.get("type") == "TOTAL":
            return [
                {
                    "positie": rij["position"],
                    "team": rij["team"]["name"],
                    "punten": rij["points"],
                    "gespeeld": rij["playedGames"],
                }
                for rij in groep["table"]
            ]
    return []


# ---------------- Lezen/schrijven van de JSON-tabel ----------------

def load_existing() -> dict | None:
    if not os.path.exists(OUTPUT_PATH):
        return None
    with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save(matches: list[dict], stand: list[dict] | None = None) -> None:
    payload = {
        "bijgewerkt_op": datetime.now(timezone.utc).isoformat(),
        "wedstrijden": sorted(matches, key=lambda m: m["kickoff"]),
        "stand": stand if stand is not None else [],
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[save] {len(matches)} wedstrijden en {len(payload['stand'])} standen-rijen weggeschreven naar {OUTPUT_PATH}.")


def moet_daily_refresh(bestaand: dict | None) -> bool:
    if not bestaand or not bestaand.get("bijgewerkt_op"):
        return True
    laatst = datetime.fromisoformat(bestaand["bijgewerkt_op"])
    return laatst.date() != datetime.now(timezone.utc).date()


# ---------------- Hoofdlogica ----------------

def main() -> None:
    bestaand = load_existing()
    matches = bestaand["wedstrijden"] if bestaand else []
    stand = bestaand.get("stand", []) if bestaand else []

    if moet_daily_refresh(bestaand):
        matches = daily_full_refresh()
        stand = fetch_standings()
        save(matches, stand)

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

    save(list(by_id.values()), stand)


if __name__ == "__main__":
    main()
