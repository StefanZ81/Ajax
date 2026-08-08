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
  2. Standen: elke 2 uur ververst (los van de dagelijkse ververing), zie
     moet_stand_verversen().
  3. Tijdens een wedstrijd: continu bevraagd vanaf net vóór aftrap tot 150
     minuten erna, elke cyclus opnieuw (niet slechts twee smalle momenten).
     Reden: football-data.org's gratis laag geeft VERTRAAGDE scores, geen
     real-time data -- een eenmalig smal venster liep het risico dat de
     bron op dát exacte moment nog niet had bijgewerkt, met als gevolg dat
     met name de ruststand (die maar kort 'actueel' is) blijvend gemist kon
     worden. Blijven proberen tot de wedstrijd 'afgelopen' is, vangt dit af.

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

# Continu proberen vanaf net vóór aftrap tot ruim ná de wedstrijd, in plaats
# van twee losse, smalle "schietgaten" (was: alleen rond T+50 en T+125 min).
# Reden: football-data.org's gratis laag geeft VERTRAAGDE scores, geen
# real-time data (bevestigd door henzelf en meerdere onafhankelijke bronnen).
# Bij een smal eenmalig venster loop je het risico dat de API op dát exacte
# moment nog niet heeft bijgewerkt, en dat venster daarna voorgoed gesloten
# is -- vooral riskant voor de ruststand, die maar kort "actueel" is voordat
# de tweede helft alweer begint. Door continu (elke cyclus) te blijven
# proberen zolang de wedstrijd nog niet 'afgelopen' is, pakken we een
# vertraagde ruststand alsnog op zodra de bron is bijgetrokken -- ongeacht
# hoe laat dat is.
PROBE_VENSTER_MINUTEN = 150  # ruim voorbij 90 min + rust + eventuele verlenging
PROBE_START_MARGE = timedelta(minutes=10)  # ook vlak vóór het geplande aftrapmoment al proberen


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


# ---------------- Continue live-probe tijdens een wedstrijd ----------------

def wedstrijden_binnen_probe_venster(matches: list[dict]) -> list[int]:
    """Geeft de id's van wedstrijden die deze cyclus opnieuw bevraagd moeten
    worden: alles wat nog niet 'afgelopen' is en waarvan de aftrap tussen
    (nu - PROBE_VENSTER_MINUTEN) en (nu + PROBE_START_MARGE) ligt."""
    nu = datetime.now(timezone.utc)
    te_proben = []
    for m in matches:
        if m["status"] == "afgelopen":
            continue
        kickoff = datetime.fromisoformat(m["kickoff"])
        venster_einde = kickoff + timedelta(minutes=PROBE_VENSTER_MINUTEN)
        if kickoff - PROBE_START_MARGE <= nu <= venster_einde:
            te_proben.append(m["id"])
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

    # TIJDELIJKE diagnose: laat zien wat de API precies teruggeeft, zodat we
    # kunnen zien of het probleem in de aanroep zelf zit, of in hoe we het
    # antwoord verwerken. Verwijderen zodra de standen weer goed doorkomen.
    print(f"[fetch_standings] seizoensfilter uit de respons: {data.get('filters')}")
    print(f"[fetch_standings] seizoen-info: {data.get('season')}")
    groepen = data.get("standings", [])
    print(f"[fetch_standings] aantal 'standings'-groepen in de respons: {len(groepen)}")
    for g in groepen:
        tabel = g.get("table", [])
        eerste_rijen = [
            {"positie": r["position"], "team": r["team"]["name"], "gespeeld": r["playedGames"], "punten": r["points"]}
            for r in tabel[:6]
        ]
        print(f"[fetch_standings]   groep type={g.get('type')!r} stage={g.get('stage')!r} "
              f"aantal_teams={len(tabel)} eerste_rijen={eerste_rijen}")

    for groep in groepen:
        if groep.get("type") == "TOTAL":
            return [
                {
                    "positie": rij["position"],
                    "team": rij["team"]["name"],
                    "punten": rij["points"],
                    "gespeeld": rij["playedGames"],
                    "winst": rij["won"],
                    "gelijk": rij["draw"],
                    "verlies": rij["lost"],
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


def save(matches: list[dict], stand: list[dict] | None = None, stand_bijgewerkt_op: str | None = None) -> None:
    payload = {
        "bijgewerkt_op": datetime.now(timezone.utc).isoformat(),
        "wedstrijden": sorted(matches, key=lambda m: m["kickoff"]),
        "stand": stand if stand is not None else [],
        # Aparte tijdstempel specifiek voor de standen (i.p.v. de algemene
        # 'bijgewerkt_op', die bij elke wedstrijd-ververing verandert): de
        # standen mogen namelijk op hun EIGEN, vaker terugkerend ritme
        # ververst worden (elke 2 uur), los van de dagelijkse volledige
        # wedstrijd-ververing.
        "stand_bijgewerkt_op": stand_bijgewerkt_op,
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


def moet_stand_verversen(bestaand: dict | None, marge_uren: float = 2.0) -> bool:
    """Standen mogen vaker ververst worden dan de wedstrijden zelf -- in een
    speelweekend verschuift de tabel door meerdere wedstrijden tegelijk in
    korte tijd. Ververst als het nog nooit is gebeurd, of langer dan
    marge_uren geleden."""
    if not bestaand or not bestaand.get("stand_bijgewerkt_op"):
        return True
    laatst = datetime.fromisoformat(bestaand["stand_bijgewerkt_op"])
    return (datetime.now(timezone.utc) - laatst) >= timedelta(hours=marge_uren)


def moet_stand_verversen_na_ajax_wedstrijd(bestaand: dict | None, matches: list[dict]) -> bool:
    """Extra, snellere trigger náást de reguliere 2-uursklok hierboven: de
    standenlijst verandert niet alleen door Ajax' eigen wedstrijden, maar
    ook door alle andere Eredivisie-wedstrijden die competitie-breed
    plaatsvinden -- dus het bijwerken van de stand hoort sowieso al
    onafhankelijk van Ajax' eigen speelschema te gebeuren (dat doet de
    2-uursklok hierboven ook al). Deze functie zorgt er daarnaast voor dat
    de stand ook SNEL (binnen ~2u05m, i.p.v. tot 2 uur wachten op de
    volgende klok-tik) wordt bijgewerkt rond het moment dat we wél zeker
    weten dat er iets relevants gebeurd is: vlak na een Ajax-wedstrijd."""
    stand_bijgewerkt_op = bestaand.get("stand_bijgewerkt_op") if bestaand else None
    laatste_update = datetime.fromisoformat(stand_bijgewerkt_op) if stand_bijgewerkt_op else None
    nu = datetime.now(timezone.utc)

    for m in matches:
        kickoff = datetime.fromisoformat(m["kickoff"])
        moment = kickoff + timedelta(hours=2, minutes=5)
        if moment <= nu and (laatste_update is None or laatste_update < moment):
            return True
    return False


# ---------------- Hoofdlogica ----------------

def main() -> None:
    bestaand = load_existing()
    matches = bestaand["wedstrijden"] if bestaand else []
    stand = bestaand.get("stand", []) if bestaand else []
    stand_bijgewerkt_op = bestaand.get("stand_bijgewerkt_op") if bestaand else None

    # Handmatig gestart via "Run workflow" (workflow_dispatch)? Dan altijd
    # alles verversen -- ook als de dagelijkse ververing vandaag al gebeurde
    # en er nu geen wedstrijd binnen een live-probevenster valt. Zonder dit
    # zou een handmatige run buiten die smalle momenten om stilzwijgend
    # helemaal niets doen, ook niet de standenlijst bijwerken.
    geforceerd = os.environ.get("FORCE_REFRESH", "").lower() == "true"
    daily_nodig = moet_daily_refresh(bestaand)

    if daily_nodig or geforceerd:
        matches = daily_full_refresh()

    # Standen op hun EIGEN ritme verversen, VOLLEDIG los van of Ajax zelf al
    # heeft gespeeld -- de standenlijst verandert immers ook door alle
    # andere Eredivisie-wedstrijden. Twee triggers samen:
    #  1. een reguliere klok (standaard elke 2 uur) als algemene ondergrens;
    #  2. een snellere, gerichte trigger die specifiek 2u05m na de aftrap
    #     van een Ajax-wedstrijd afgaat, zodat je niet per ongeluk tot bijna
    #     2 uur na zo'n wedstrijd op de eerstvolgende klok-tik hoeft te wachten.
    stand_nodig = moet_stand_verversen(bestaand) or moet_stand_verversen_na_ajax_wedstrijd(bestaand, matches)

    if stand_nodig or geforceerd:
        stand = fetch_standings()
        stand_bijgewerkt_op = datetime.now(timezone.utc).isoformat()

    if daily_nodig or geforceerd or stand_nodig:
        save(matches, stand, stand_bijgewerkt_op)
        if geforceerd:
            print("[main] Handmatig gestart -- volledige verversing (wedstrijden + stand) uitgevoerd.")

    te_proben = wedstrijden_binnen_probe_venster(matches)
    if not te_proben:
        print("[main] Geen wedstrijd binnen een probe-venster deze cyclus — niets verder te doen.")
        return

    by_id = {m["id"]: m for m in matches}
    standen_verversen = False
    for fixture_id in te_proben:
        was_al_afgelopen = by_id.get(fixture_id, {}).get("status") == "afgelopen"
        print(f"[main] Probe fixture {fixture_id}...")
        bijgewerkt = probe_fixture(fixture_id)
        if bijgewerkt:
            by_id[fixture_id] = bijgewerkt
            # Zodra een wedstrijd voor het eerst als 'afgelopen' terugkomt
            # (dus niet al eerder zo was), is de standenlijst realistisch
            # gezien ook net gewijzigd -- niet wachten tot de eerstvolgende
            # dagelijkse verversing (die kan tot bijna 24 uur later zijn).
            if not was_al_afgelopen and bijgewerkt.get("status") == "afgelopen":
                standen_verversen = True

    if standen_verversen:
        print("[main] Wedstrijd afgerond -- standenlijst direct meeverversen.")
        stand = fetch_standings()
        stand_bijgewerkt_op = datetime.now(timezone.utc).isoformat()

    save(list(by_id.values()), stand, stand_bijgewerkt_op)


if __name__ == "__main__":
    main()
