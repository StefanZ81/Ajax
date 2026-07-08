"""
ajax_sync.py
------------------------------------------------------------------
Houdt Ajax-programma, live standen en uitslagen bij via API-Football.
Python-equivalent van ajax-sync.js.

Aanroepfrequentie:
    - 1x per minuut  -> alleen zolang er een Ajax-wedstrijd "live" is
    - 1x per dag     -> op alle andere momenten (programma/uitslagen-check)

Dit bestand bevat geen eigen scheduler. check_and_sync() moet elke
minuut aangeroepen worden door een externe cron — bv. een crontab-regel:

    * * * * *  /usr/bin/python3 /pad/naar/run_sync.py

De functie zelf bepaalt vervolgens of er die minuut ook echt een
API-verzoek wordt gedaan, of dat hij niets doet.

Vereist: Python 3.8+, package "requests", env var API_FOOTBALL_KEY
------------------------------------------------------------------
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from psycopg.rows import dict_row

from db import pool
from reminders import check_and_send_reminders
from scoring import bereken_punten

API_BASE = "https://v3.football.api-sports.io"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "")

# Eenmalig opzoeken via GET /teams?search=Ajax en hierna vastzetten
# (voorkomt onnodige API-calls). Neem het team uit country "Netherlands".
AJAX_TEAM_ID = int(os.environ.get("AJAX_TEAM_ID", "194"))

LIVE_CODES = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT"}
AFGELOPEN_CODES = {"FT", "AET", "PEN"}
EUROPESE_TOERNOOIEN = {"UEFA Champions League", "UEFA Europa League", "UEFA Conference League"}

# Voorrondes en play-offs van de Europese toernooien lopen elk jaar tot eind
# augustus; de competitiefase/hoofdtoernooi begint daarna. We gebruiken dit
# als harde, jaarlijks terugkerende datumgrens — bewust NIET afhankelijk van
# hoe de API een ronde exact noemt, want "Play-offs" bevat bijvoorbeeld geen
# van de woorden "voorronde"/"qualifying"/"preliminary" en zou een puur
# tekst-gebaseerde filter dus ten onrechte doorlaten.
VOORRONDES_EINDIGEN_MAAND = 9  # vanaf 1 september telt een Europese wedstrijd pas mee

# In-memory cache van het huidige seizoensprogramma.
# In productie: vervang door je eigen database (bv. Postgres/Supabase).
_cached_fixtures: list[dict] = []
_last_daily_sync_date: Optional[str] = None


# ---------------- Hulpfuncties ----------------

def _api_football(path: str) -> list[dict]:
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


def _normaliseer_score(s: Optional[dict]) -> Optional[dict]:
    if not s or s.get("home") is None or s.get("away") is None:
        return None
    return {"thuis": s["home"], "uit": s["away"]}


def _seizoen_label(kickoff: datetime) -> str:
    start_jaar = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    return f"{start_jaar}/{start_jaar + 1}"


def _map_fixture(f: dict) -> dict:
    kickoff = f["fixture"]["date"]
    return {
        "id": f["fixture"]["id"],
        "seizoen": _seizoen_label(datetime.fromisoformat(kickoff)),
        "competitie": f["league"]["name"],  # bv. "Eredivisie", "Conference League"
        "ronde": f["league"]["round"],
        "thuis": f["teams"]["home"]["name"],
        "uit": f["teams"]["away"]["name"],
        "kickoff": kickoff,  # ISO 8601
        "status": _map_status(f["fixture"]["status"]["short"]),
        "rust": _normaliseer_score(f["score"]["halftime"]),
        "eind": _normaliseer_score(f["score"]["fulltime"]),
    }


def _is_relevante_competitie(match: dict) -> bool:
    competitie = match["competitie"]
    ronde = match.get("ronde") or ""

    if competitie == "Eredivisie":
        return True
    if competitie not in EUROPESE_TOERNOOIEN:
        return False

    # Check 1 (hoofdcheck): harde datumgrens. Alles vóór 1 september is
    # per definitie voorronde/kwalificatie/play-off, ongeacht de naam.
    kickoff = datetime.fromisoformat(match["kickoff"])
    seizoen_start_jaar = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    grens = datetime(seizoen_start_jaar, VOORRONDES_EINDIGEN_MAAND, 1, tzinfo=kickoff.tzinfo)
    if kickoff < grens:
        return False

    # Check 2 (extra vangnet): als de API een ronde-naam meegeeft die
    # expliciet op kwalificatie wijst, ook uitsluiten — zelfs als de datum
    # door een schemawijziging onverhoopt toch in september zou vallen.
    is_kwalificatie = re.search(r"qualif|preliminary|voorronde|play-?off", ronde, re.IGNORECASE)
    return not is_kwalificatie


# Zelfde opzoekprincipe als AJAX_TEAM_ID: eenmalig checken via
# GET /leagues?name=Eredivisie&country=Netherlands en hierna vastzetten.
EREDIVISIE_LEAGUE_ID = int(os.environ.get("EREDIVISIE_LEAGUE_ID", "88"))


def sync_standings() -> None:
    seizoen_jaar = datetime.now(timezone.utc).year if datetime.now(timezone.utc).month >= 7 else datetime.now(timezone.utc).year - 1
    response = _api_football(f"/standings?league={EREDIVISIE_LEAGUE_ID}&season={seizoen_jaar}")
    if not response:
        return
    tabel = response[0]["league"]["standings"][0]  # buitenste lijst = eventuele groepen; Eredivisie heeft er 1

    with pool.connection() as conn, conn.cursor() as cur:
        seizoen_label = _seizoen_label(datetime(seizoen_jaar, 8, 1))
        cur.execute("DELETE FROM standings WHERE seizoen = %s", (seizoen_label,))
        for rij in tabel:
            cur.execute(
                "INSERT INTO standings (seizoen, positie, team, punten) VALUES (%s, %s, %s, %s)",
                (seizoen_label, rij["rank"], rij["team"]["name"], rij["points"]),
            )
    print(f"[sync_standings] Eredivisiestand bijgewerkt ({len(tabel)} teams).")


# ---------------- 1x per dag: volledig programma + uitslagen ----------------

def daily_schedule_sync() -> None:
    global _cached_fixtures, _last_daily_sync_date

    nu = datetime.now(timezone.utc)
    seizoen = nu.year if nu.month >= 7 else nu.year - 1
    response = _api_football(f"/fixtures?team={AJAX_TEAM_ID}&season={seizoen}")

    _cached_fixtures = [
        m for m in (_map_fixture(f) for f in response)
        if _is_relevante_competitie(m)
    ]

    _last_daily_sync_date = nu.date().isoformat()
    persist_fixtures(_cached_fixtures)  # -> eigen database
    sync_standings()
    print(f"[daily_schedule_sync] {len(_cached_fixtures)} wedstrijden bijgewerkt.")


# ---------------- 1x per minuut: alleen tijdens een live Ajax-wedstrijd ----------------

def live_score_sync(fixture_id: int) -> None:
    global _cached_fixtures

    response = _api_football(f"/fixtures?id={fixture_id}")
    if not response:
        return
    bijgewerkt = _map_fixture(response[0])

    _cached_fixtures = [bijgewerkt if m["id"] == bijgewerkt["id"] else m for m in _cached_fixtures]
    persist_fixtures(_cached_fixtures)

    eind = bijgewerkt["eind"] or {}
    print(f"[live_score_sync] {bijgewerkt['thuis']} {eind.get('thuis', '-')}-{eind.get('uit', '-')} "
          f"{bijgewerkt['uit']} ({bijgewerkt['status']})")

    # Zodra de wedstrijd is afgelopen: reken hier ook direct de punten door
    # voor alle deelnemers (zie bereken_punten() uit de app), en verstuur
    # eventueel een notificatie "uitslag & punten bekend".
    if bijgewerkt["status"] == "afgelopen":
        bereken_en_bewaar_punten(bijgewerkt)


# ---------------- Entry point: elke minuut door externe cron aangeroepen ----------------

def check_and_sync() -> None:
    vandaag = datetime.now(timezone.utc).date().isoformat()

    # 1x per dag: ververs het volledige programma (ook als er niets live is)
    if _last_daily_sync_date != vandaag:
        daily_schedule_sync()

    # Is er nu een Ajax-wedstrijd "live" of net begonnen (kickoff .. kickoff+130 min)?
    nu = datetime.now(timezone.utc)
    lopende_wedstrijd = None
    for m in _cached_fixtures:
        if m["status"] == "afgelopen":
            continue
        start = datetime.fromisoformat(m["kickoff"])
        marge = timedelta(minutes=130)  # rust + blessuretijd + eventueel verlenging
        if start <= nu <= start + marge:
            lopende_wedstrijd = m
            break

    if lopende_wedstrijd:
        # 1x per minuut, uitsluitend voor déze wedstrijd
        live_score_sync(lopende_wedstrijd["id"])
    # Geen live wedstrijd? Dan gebeurt er deze minuut niets — geen API-call.

    # Reminder-check kost geen API-Football-call (draait op _cached_fixtures),
    # dus die mag altijd elke minuut mee, ongeacht of er iets live is.
    deelnemers = load_participants()  # -> eigen database
    voorspellingen = load_predictions()  # -> eigen database
    check_and_send_reminders(_cached_fixtures, deelnemers, voorspellingen)


# ---------------- Aansluiten op eigen database / puntenlogica ----------------

def persist_fixtures(fixtures: list[dict]) -> None:
    if not fixtures:
        return
    with pool.connection() as conn, conn.cursor() as cur:
        for m in fixtures:
            cur.execute(
                """
                INSERT INTO matches (id, seizoen, competitie, ronde, thuis, uit, kickoff,
                                      status, uitslag_rust_thuis, uitslag_rust_uit,
                                      uitslag_eind_thuis, uitslag_eind_uit)
                VALUES (%(id)s, %(seizoen)s, %(competitie)s, %(ronde)s, %(thuis)s, %(uit)s, %(kickoff)s,
                        %(status)s, %(rust_thuis)s, %(rust_uit)s, %(eind_thuis)s, %(eind_uit)s)
                ON CONFLICT (id) DO UPDATE SET
                    competitie = EXCLUDED.competitie,
                    ronde = EXCLUDED.ronde,
                    kickoff = EXCLUDED.kickoff,
                    status = EXCLUDED.status,
                    uitslag_rust_thuis = EXCLUDED.uitslag_rust_thuis,
                    uitslag_rust_uit = EXCLUDED.uitslag_rust_uit,
                    uitslag_eind_thuis = EXCLUDED.uitslag_eind_thuis,
                    uitslag_eind_uit = EXCLUDED.uitslag_eind_uit,
                    bijgewerkt_op = now()
                """,
                {
                    **m,
                    "rust_thuis": (m["rust"] or {}).get("thuis"),
                    "rust_uit": (m["rust"] or {}).get("uit"),
                    "eind_thuis": (m["eind"] or {}).get("thuis"),
                    "eind_uit": (m["eind"] or {}).get("uit"),
                },
            )


def bereken_en_bewaar_punten(match: dict) -> None:
    if not match["eind"]:
        return  # nog geen eindstand bekend, niets te berekenen

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM rules WHERE seizoen = %s", (match["seizoen"],))
            rules = cur.fetchone()
            if not rules:
                print(f"[bereken_en_bewaar_punten] geen 'rules' gevonden voor seizoen {match['seizoen']}, overgeslagen.")
                return

            cur.execute(
                "SELECT id, participant_id, rust_thuis, rust_uit, eind_thuis, eind_uit, joker "
                "FROM predictions WHERE match_id = %s",
                (match["id"],),
            )
            voorspellingen = cur.fetchall()

        uitslag = {"rust": match["rust"], "eind": match["eind"]}
        with conn.transaction():
            with conn.cursor() as cur:
                for v in voorspellingen:
                    voorspelling = {
                        "rust": {"thuis": v["rust_thuis"], "uit": v["rust_uit"]},
                        "eind": {"thuis": v["eind_thuis"], "uit": v["eind_uit"]},
                        "joker": v["joker"],
                    }
                    punten, _ = bereken_punten(voorspelling, uitslag, rules)
                    cur.execute(
                        "UPDATE predictions SET punten = %s WHERE id = %s",
                        (punten, v["id"]),
                    )
    print(f"[bereken_en_bewaar_punten] {len(voorspellingen)} voorspellingen doorgerekend voor wedstrijd {match['id']}.")
    # TODO: trigger hier eventueel een pushmelding "uitslag & punten bekend".


def load_participants() -> list[dict]:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM participants ORDER BY naam")
        return cur.fetchall()


def load_predictions() -> dict:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT match_id, participant_id, rust_thuis, rust_uit, eind_thuis, eind_uit, joker "
            "FROM predictions"
        )
        rows = cur.fetchall()

    resultaat: dict[str, dict] = {}
    for r in rows:
        resultaat.setdefault(str(r["match_id"]), {})[str(r["participant_id"])] = {
            "rust": {"thuis": r["rust_thuis"], "uit": r["rust_uit"]},
            "eind": {"thuis": r["eind_thuis"], "uit": r["eind_uit"]},
            "joker": r["joker"],
        }
    return resultaat

