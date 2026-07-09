"""
queries.py
------------------------------------------------------------------
Databasequeries voor de webweergave — SQLite-versie.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db import get_connection
from scoring import bereken_punten

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def _parse_dt(s: str | None):
    if not s:
        return None
    return datetime.fromisoformat(s)


def _parse_dt_lokaal(s: str | None):
    """Voor weergave: UTC (zoals opgeslagen) omgezet naar Nederlandse tijd,
    met automatische zomer-/wintertijd via de ingebouwde tijdzonedatabase."""
    dt = _parse_dt(s)
    return dt.astimezone(AMSTERDAM) if dt else None


def _match_row(row: dict) -> dict:
    row = dict(row)
    row["kickoff"] = _parse_dt_lokaal(row["kickoff"])
    row["oefenwedstrijd"] = bool(row["oefenwedstrijd"])
    return row


def get_active_season() -> str:
    with get_connection() as conn:
        row = conn.execute("SELECT seizoen_actief FROM app_settings LIMIT 1").fetchone()
        return row["seizoen_actief"] if row else "2026/2027"


def get_matches(seizoen: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM matches WHERE seizoen = ? ORDER BY kickoff", (seizoen,)
        ).fetchall()
        return [_match_row(r) for r in rows]


def get_match(match_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        return _match_row(row) if row else None


def get_predictions_for_match(match_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT pr.*, p.naam
            FROM predictions pr
            JOIN participants p ON p.id = pr.participant_id
            WHERE pr.match_id = ? AND p.status = 'goedgekeurd' AND p.rol = 'deelnemer'
            ORDER BY p.naam
            """,
            (match_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_my_prediction(match_id: int, participant_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM predictions WHERE match_id = ? AND participant_id = ?",
            (match_id, participant_id),
        ).fetchone()
        return dict(row) if row else None


def upsert_prediction(match_id: int, participant_id: str, rust: tuple[int, int], eind: tuple[int, int], joker: bool) -> None:
    with get_connection() as conn:
        bestaande = conn.execute(
            "SELECT id FROM predictions WHERE match_id = ? AND participant_id = ?",
            (match_id, participant_id),
        ).fetchone()
        pred_id = bestaande["id"] if bestaande else uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO predictions (id, match_id, participant_id, rust_thuis, rust_uit, eind_thuis, eind_uit, joker)
            VALUES (:id, :match_id, :participant_id, :rust_thuis, :rust_uit, :eind_thuis, :eind_uit, :joker)
            ON CONFLICT (match_id, participant_id) DO UPDATE SET
                rust_thuis = excluded.rust_thuis, rust_uit = excluded.rust_uit,
                eind_thuis = excluded.eind_thuis, eind_uit = excluded.eind_uit,
                joker = excluded.joker
            """,
            {
                "id": pred_id, "match_id": match_id, "participant_id": participant_id,
                "rust_thuis": rust[0], "rust_uit": rust[1],
                "eind_thuis": eind[0], "eind_uit": eind[1], "joker": int(joker),
            },
        )


def get_rules(seizoen: str) -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM rules WHERE seizoen = ?", (seizoen,)).fetchone()
        if row:
            return dict(row)
        conn.execute("INSERT INTO rules (seizoen) VALUES (?)", (seizoen,))
        row = conn.execute("SELECT * FROM rules WHERE seizoen = ?", (seizoen,)).fetchone()
        return dict(row)


def update_rules(seizoen: str, rules: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE rules SET halftime_punten=:halftime_punten, fulltime_punten=:fulltime_punten,
                outcome_punten=:outcome_punten, joker_kosten=:joker_kosten,
                joker_vermenigvuldiger=:joker_vermenigvuldiger, bijgewerkt_op=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE seizoen = :seizoen
            """,
            {**rules, "seizoen": seizoen},
        )


def get_pending_participants() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM participants WHERE status = 'aangevraagd' ORDER BY aangemaakt_op").fetchall()
        return [dict(r) for r in rows]


def get_all_participants() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM participants ORDER BY naam").fetchall()
        return [dict(r) for r in rows]


def set_participant_status(participant_id: str, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE participants SET status = ? WHERE id = ?", (status, participant_id))


def delete_participant(participant_id: str) -> None:
    """Verwijdert een deelnemer volledig — gebruikt bij het weigeren van een
    aanmelding, zodat er geen data van geweigerde aanmeldingen bewaard blijft.
    PRAGMA foreign_keys staat aan (zie db.py), dus eventuele gekoppelde rijen
    (voorspellingen, reminders) worden via ON DELETE CASCADE automatisch mee
    opgeruimd — voor een geweigerde aanmelding zijn die er normaliter nooit,
    want inloggen kon nog niet, maar dit is een extra vangnet."""
    with get_connection() as conn:
        conn.execute("DELETE FROM participants WHERE id = ?", (participant_id,))


def get_season_prediction(participant_id: str, seizoen: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM season_predictions WHERE participant_id = ? AND seizoen = ?",
            (participant_id, seizoen),
        ).fetchone()
        return dict(row) if row else None


def upsert_season_prediction(participant_id: str, seizoen: str, na17: tuple[int, int], na34: tuple[int, int]) -> None:
    with get_connection() as conn:
        bestaande = conn.execute(
            "SELECT id FROM season_predictions WHERE participant_id = ? AND seizoen = ?",
            (participant_id, seizoen),
        ).fetchone()
        row_id = bestaande["id"] if bestaande else uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO season_predictions (id, participant_id, seizoen, na17_positie, na17_punten, na34_positie, na34_punten)
            VALUES (:id, :participant_id, :seizoen, :na17_positie, :na17_punten, :na34_positie, :na34_punten)
            ON CONFLICT (participant_id, seizoen) DO UPDATE SET
                na17_positie = excluded.na17_positie, na17_punten = excluded.na17_punten,
                na34_positie = excluded.na34_positie, na34_punten = excluded.na34_punten
            """,
            {
                "id": row_id, "participant_id": participant_id, "seizoen": seizoen,
                "na17_positie": na17[0], "na17_punten": na17[1],
                "na34_positie": na34[0], "na34_punten": na34[1],
            },
        )


def get_registratie_sluit_na_wedstrijd() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT registratie_sluit_na_wedstrijd FROM app_settings LIMIT 1").fetchone()
        return row["registratie_sluit_na_wedstrijd"] if row else 2


def set_registratie_sluit_na_wedstrijd(n: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE app_settings SET registratie_sluit_na_wedstrijd = ?", (n,))


def registratie_gesloten(seizoen: str) -> bool:
    """Registratie sluit bij de aftrap van de N-de Eredivisie-wedstrijd
    (oefenwedstrijden tellen niet mee), waarbij N instelbaar is door de
    beheerder (standaard 2). Zolang die N-de wedstrijd nog niet bekend is
    (schema nog niet volledig gesynchroniseerd), blijft registratie open —
    we sluiten nooit per ongeluk te vroeg bij onvolledige data."""
    n = get_registratie_sluit_na_wedstrijd()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT kickoff FROM matches
            WHERE seizoen = ? AND competitie = 'Eredivisie' AND oefenwedstrijd = 0
            ORDER BY kickoff LIMIT 1 OFFSET ?
            """,
            (seizoen, n - 1),
        ).fetchone()
    if not row:
        return False
    return datetime.now(timezone.utc) >= _parse_dt(row["kickoff"])


def eerste_competitiewedstrijd_gestart(seizoen: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT kickoff FROM matches
            WHERE seizoen = ? AND competitie = 'Eredivisie' AND oefenwedstrijd = 0
            ORDER BY kickoff LIMIT 1
            """,
            (seizoen,),
        ).fetchone()
    if not row:
        return False
    return datetime.now(timezone.utc) >= _parse_dt(row["kickoff"])


def get_klassement() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM klassement").fetchall()
        return [dict(r) for r in rows]


def add_manual_match(seizoen: str, competitie: str, ronde: str, thuis: str, uit: str, kickoff_iso: str, oefenwedstrijd: bool) -> None:
    # Handmatige wedstrijden krijgen een negatief id, zodat ze nooit kunnen
    # botsen met een echt fixture-id van football-data.org (die zijn altijd
    # positief). github_sync.py raakt id's die niet in de GitHub-tabel staan
    # nooit aan, dus deze rij is hierna veilig voor altijd.
    with get_connection() as conn:
        laagste = conn.execute("SELECT MIN(id) AS m FROM matches").fetchone()
        nieuw_id = (laagste["m"] - 1) if laagste and laagste["m"] is not None and laagste["m"] < 0 else -1
        conn.execute(
            """
            INSERT INTO matches (id, seizoen, competitie, ronde, thuis, uit, kickoff, status, oefenwedstrijd)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'gepland', ?)
            """,
            (nieuw_id, seizoen, competitie, ronde or None, thuis, uit, kickoff_iso, int(oefenwedstrijd)),
        )


def bereken_en_bewaar_punten(match_id: int) -> int:
    """Rekent de punten van alle deelnemers voor deze wedstrijd door en slaat ze op.
    Wordt aangeroepen zodra een wedstrijd de status 'afgelopen' krijgt — zowel
    via github_sync.py (automatische Eredivisie-uitslagen) als via set_match_match_result()
    hierboven (handmatig ingevoerde Europese wedstrijden)."""
    match = get_match(match_id)
    if not match or not match["status"] == "afgelopen" or match["uitslag_eind_thuis"] is None:
        return 0

    rules = get_rules(match["seizoen"])
    uitslag = {
        "rust": {"thuis": match["uitslag_rust_thuis"], "uit": match["uitslag_rust_uit"]},
        "eind": {"thuis": match["uitslag_eind_thuis"], "uit": match["uitslag_eind_uit"]},
    }

    with get_connection() as conn:
        voorspellingen = conn.execute(
            "SELECT id, rust_thuis, rust_uit, eind_thuis, eind_uit, joker FROM predictions WHERE match_id = ?",
            (match_id,),
        ).fetchall()
        for v in voorspellingen:
            voorspelling = {
                "rust": {"thuis": v["rust_thuis"], "uit": v["rust_uit"]},
                "eind": {"thuis": v["eind_thuis"], "uit": v["eind_uit"]},
                "joker": bool(v["joker"]),
            }
            punten, _ = bereken_punten(voorspelling, uitslag, rules)
            conn.execute("UPDATE predictions SET punten = ? WHERE id = ?", (punten, v["id"]))
    return len(voorspellingen)


def set_match_result(match_id: int, rust: tuple[int, int], eind: tuple[int, int]) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE matches SET status = 'afgelopen',
                uitslag_rust_thuis = ?, uitslag_rust_uit = ?,
                uitslag_eind_thuis = ?, uitslag_eind_uit = ?
            WHERE id = ?
            """,
            (rust[0], rust[1], eind[0], eind[1], match_id),
        )
    bereken_en_bewaar_punten(match_id)


def get_standings_widget(seizoen: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM standings WHERE seizoen = ? AND positie BETWEEN
                MAX(1, (SELECT positie FROM standings WHERE seizoen = ? AND team LIKE '%Ajax%') - 1)
                AND (SELECT positie FROM standings WHERE seizoen = ? AND team LIKE '%Ajax%') + 1
            ORDER BY positie
            """,
            (seizoen, seizoen, seizoen),
        ).fetchall()
    resultaat = [dict(r) for r in rows]
    # Vóór de competitiestart staan bij football-data.org alle teams nog gelijk
    # (bv. iedereen op positie 1, 0 punten) — dat levert hier veel meer dan 3
    # rijen op. Dat is geen zinvolle "1 boven/onder Ajax"-stand, dus dan liever
    # niets tonen (de widget valt dan terug op "Stand nog niet gesynchroniseerd").
    if len(resultaat) > 3:
        return []
    return resultaat
