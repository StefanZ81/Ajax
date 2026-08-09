"""
queries.py
------------------------------------------------------------------
Databasequeries voor de webweergave — SQLite-versie.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
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


def get_voorspelling_status(match_id: int) -> list[dict]:
    """Voor het beheerder-only overzicht 'wie heeft nog niet voorspeld' —
    alleen zinvol vóór aftrap. Compleet = zowel rust als eindstand ingevuld
    (het formulier vereist altijd beide tegelijk, dus in de praktijk is een
    voorspelling per definitie compleet of afwezig, nooit half)."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.naam,
                   (pr.id IS NOT NULL) AS heeft_voorspeld
            FROM participants p
            LEFT JOIN predictions pr ON pr.participant_id = p.id AND pr.match_id = ?
            WHERE p.status = 'goedgekeurd'
            ORDER BY heeft_voorspeld ASC, p.naam
            """,
            (match_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_predictions_for_match(match_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT pr.*, p.naam
            FROM predictions pr
            JOIN participants p ON p.id = pr.participant_id
            WHERE pr.match_id = ? AND p.status = 'goedgekeurd'
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


SEIZOENSPUNTEN_PER_ONDERDEEL = 5  # juiste positie = 5 punten, juiste puntenaantal = 5 punten


def auto_vul_seizoensuitkomst(seizoen: str) -> None:
    """Wordt na elke standen-sync aangeroepen. Zodra ALLE teams minimaal 17
    resp. 34 wedstrijden hebben gespeeld ÉN dat checkpoint nog niet is
    vastgelegd, wordt Ajax' actuele positie/punten overgenomen als
    seizoensuitkomst en doorgerekend.

    Bewust gebaseerd op het MINIMUM aantal gespeelde wedstrijden over ALLE
    teams heen, niet alleen op Ajax' eigen aantal: de Eredivisie-speelronden
    worden niet altijd door alle teams tegelijk afgerond (uitgestelde
    wedstrijden, midweeks inhaalprogramma door Europees voetbal). Zolang
    niet elk team evenveel wedstrijden heeft gespeeld, is de tabelpositie
    nog niet stabiel — die kan nog verschuiven zodra de achterblijvers hun
    inhaalwedstrijd spelen. Ajax' eigen puntenaantal staat overigens al wel
    vast na Ajax' 17e wedstrijd; alleen de positie is het onzekere deel.

    Overschrijft nooit een al vastgelegd checkpoint (ook niet als de
    beheerder het handmatig heeft ingevuld/gecorrigeerd) — dus veilig om na
    elke sync te draaien."""
    with get_connection() as conn:
        ajax = conn.execute(
            "SELECT positie, punten FROM standings WHERE seizoen = ? AND team LIKE '%Ajax%'",
            (seizoen,),
        ).fetchone()
        minimum_gespeeld = conn.execute(
            "SELECT MIN(gespeeld) AS m FROM standings WHERE seizoen = ?",
            (seizoen,),
        ).fetchone()
        vroegste_kickoff = conn.execute(
            "SELECT MIN(kickoff) AS v FROM matches WHERE seizoen = ?",
            (seizoen,),
        ).fetchone()
    if not ajax or not minimum_gespeeld or minimum_gespeeld["m"] is None:
        return
    alle_teams_gespeeld = minimum_gespeeld["m"]

    # Aannemelijkheidscontrole tegen de kalender: 17 (laat staan 34)
    # wedstrijden voor ALLE 18 teams is onmogelijk binnen enkele weken na de
    # seizoensstart. Vangt dezelfde onstabiele/foutieve brondata af die ook
    # al bij de wedstrijdstatus tot een te-vroeg-'afgelopen'-probleem leidde
    # (zie ajax_data_sync.py) -- hier zonder deze check zou een foutieve
    # 'gespeeld'-waarde meteen en onomkeerbaar seizoenspunten uitkeren.
    if vroegste_kickoff and vroegste_kickoff["v"]:
        seizoen_start = datetime.fromisoformat(vroegste_kickoff["v"])
        nu = datetime.now(timezone.utc)
        weken_bezig = (nu - seizoen_start).days / 7
        if alle_teams_gespeeld >= 17 and weken_bezig < 8:
            print(f"[auto_vul_seizoensuitkomst] Bron meldt {alle_teams_gespeeld} gespeelde wedstrijden voor "
                  f"alle teams, maar het seizoen is pas {weken_bezig:.1f} weken bezig -- onaannemelijk, genegeerd.")
            return
        if alle_teams_gespeeld >= 34 and weken_bezig < 20:
            print(f"[auto_vul_seizoensuitkomst] Bron meldt {alle_teams_gespeeld} gespeelde wedstrijden voor "
                  f"alle teams, maar het seizoen is pas {weken_bezig:.1f} weken bezig -- onaannemelijk, genegeerd.")
            return

    resultaat = get_season_result(seizoen) or {}
    if alle_teams_gespeeld >= 17 and resultaat.get("na17_positie") is None:
        set_season_result(seizoen, "na17", ajax["positie"], ajax["punten"])
    if alle_teams_gespeeld >= 34 and resultaat.get("na34_positie") is None:
        set_season_result(seizoen, "na34", ajax["positie"], ajax["punten"])


def get_season_result(seizoen: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM season_results WHERE seizoen = ?", (seizoen,)).fetchone()
        return dict(row) if row else None


def set_season_result(seizoen: str, checkpoint: str, positie: int, punten: int) -> None:
    """checkpoint is 'na17' of 'na34'. Slaat de daadwerkelijke Ajax-positie/
    punten op dat moment op, en rekent daarna meteen de seizoenspunten van
    alle deelnemers voor dat checkpoint opnieuw door."""
    assert checkpoint in ("na17", "na34")
    with get_connection() as conn:
        conn.execute(
            f"""
            INSERT INTO season_results (seizoen, {checkpoint}_positie, {checkpoint}_punten)
            VALUES (?, ?, ?)
            ON CONFLICT (seizoen) DO UPDATE SET
                {checkpoint}_positie = excluded.{checkpoint}_positie,
                {checkpoint}_punten = excluded.{checkpoint}_punten,
                bijgewerkt_op = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (seizoen, positie, punten),
        )
    bereken_seizoenspunten(seizoen, checkpoint)


def bereken_seizoenspunten(seizoen: str, checkpoint: str) -> int:
    """Vergelijkt voor elke deelnemer de voorspelling met de daadwerkelijke
    uitkomst voor dit checkpoint, en slaat de punten op (5 voor juiste
    positie, 5 voor juist puntenaantal, onafhankelijk van elkaar)."""
    assert checkpoint in ("na17", "na34")
    resultaat = get_season_result(seizoen)
    if not resultaat or resultaat[f"{checkpoint}_positie"] is None:
        return 0

    werkelijke_positie = resultaat[f"{checkpoint}_positie"]
    werkelijke_punten = resultaat[f"{checkpoint}_punten"]

    with get_connection() as conn:
        voorspellingen = conn.execute(
            f"SELECT id, {checkpoint}_positie, {checkpoint}_punten FROM season_predictions WHERE seizoen = ?",
            (seizoen,),
        ).fetchall()
        for v in voorspellingen:
            behaald = 0
            if v[f"{checkpoint}_positie"] == werkelijke_positie:
                behaald += SEIZOENSPUNTEN_PER_ONDERDEEL
            if v[f"{checkpoint}_punten"] == werkelijke_punten:
                behaald += SEIZOENSPUNTEN_PER_ONDERDEEL
            conn.execute(
                f"UPDATE season_predictions SET punten_{checkpoint} = ? WHERE id = ?",
                (behaald, v["id"]),
            )
    return len(voorspellingen)


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


TOP_TEGENSTANDERS = ("Feyenoord", "PSV", "AZ")
EREDIVISIE_LINKS = ("PSV", "Feyenoord", "NEC", "FC Twente", "FC Utrecht", "AZ", "SC Heerenveen", "FC Groningen")
EREDIVISIE_RECHTS = ("Sparta", "Fortuna Sittard", "Go Ahead Eagles", "Excelsior", "Telstar", "PEC Zwolle", "Cambuur", "Willem II", "ADO Den Haag")


def _aantal_gespeelde_eredivisie_wedstrijden(seizoen: str) -> int:
    with get_connection() as conn:
        rij = conn.execute(
            "SELECT COUNT(*) AS n FROM matches WHERE seizoen = ? AND competitie = 'Eredivisie' AND status = 'afgelopen'",
            (seizoen,),
        ).fetchone()
        return rij["n"]


def _positie_hoogste_laagste(participant_id: str, gespeeld: list[dict], alle_deelnemer_ids: list[str]) -> tuple[int | None, int | None]:
    """Reconstrueert, voor elke gespeelde wedstrijd chronologisch, de positie
    van deze deelnemer in het klassement OP DAT MOMENT (puur op basis van de
    wedstrijdpunten tot en met die wedstrijd -- seizoensvoorspelling-bonussen
    zijn niet aan één specifieke wedstrijd te koppelen en tellen hier bewust
    niet in mee). Geeft (hoogste positie ooit, laagste positie ooit) terug,
    waarbij 'hoogste positie' de BESTE (laagste getal, bv. 1e) is."""
    if not gespeeld:
        return None, None

    with get_connection() as conn:
        alle_predicties = conn.execute(
            "SELECT match_id, participant_id, punten FROM predictions WHERE punten IS NOT NULL"
        ).fetchall()

    punten_per_match: dict[int, dict[str, int]] = {}
    for r in alle_predicties:
        punten_per_match.setdefault(r["match_id"], {})[r["participant_id"]] = r["punten"]

    lopend_totaal = {pid: 0 for pid in alle_deelnemer_ids}
    posities = []
    for m in gespeeld:
        punten_deze_wedstrijd = punten_per_match.get(m["id"], {})
        for pid in alle_deelnemer_ids:
            lopend_totaal[pid] += punten_deze_wedstrijd.get(pid, 0)
        gesorteerd = sorted(alle_deelnemer_ids, key=lambda pid: -lopend_totaal[pid])
        posities.append(gesorteerd.index(participant_id) + 1)

    return min(posities), max(posities)


def get_mijn_statistieken(participant_id: str, seizoen: str) -> dict:
    """Persoonlijke statistieken voor één deelnemer, opgedeeld in drie
    secties die pas verschijnen zodra er genoeg Eredivisie-wedstrijden zijn
    gespeeld (1 / 5 / 17) om de betreffende cijfers zinvol te maken."""
    gespeelde_eredivisie = _aantal_gespeelde_eredivisie_wedstrijden(seizoen)

    with get_connection() as conn:
        gespeeld = [
            dict(r) for r in conn.execute(
                "SELECT * FROM matches WHERE seizoen = ? AND status = 'afgelopen' ORDER BY kickoff", (seizoen,)
            ).fetchall()
        ]
        eigen_voorspellingen = {
            r["match_id"]: dict(r) for r in conn.execute(
                "SELECT * FROM predictions WHERE participant_id = ?", (participant_id,)
            ).fetchall()
        }
        klassement = [dict(r) for r in conn.execute("SELECT * FROM klassement").fetchall()]
        seizoensvoorspelling_rij = conn.execute(
            "SELECT * FROM season_predictions WHERE participant_id = ? AND seizoen = ?", (participant_id, seizoen)
        ).fetchone()
        seizoensuitkomst_rij = conn.execute(
            "SELECT * FROM season_results WHERE seizoen = ?", (seizoen,)
        ).fetchone()

    resultaat: dict = {
        "toon_sectie_1": gespeelde_eredivisie >= 1,
        "toon_sectie_2": gespeelde_eredivisie >= 5,
        "toon_sectie_3": gespeelde_eredivisie >= 17,
    }
    if not resultaat["toon_sectie_1"]:
        return resultaat

    # ---------------- Sectie 1 ----------------
    gespeelde_met_voorspelling = [m for m in gespeeld if m["id"] in eigen_voorspellingen]
    resultaat["aantal_voorspeld"] = len(gespeelde_met_voorspelling)
    resultaat["aantal_gemist"] = len(gespeeld) - len(gespeelde_met_voorspelling)

    eigen_klassement_rij = next((r for r in klassement if r["participant_id"] == participant_id), None)
    resultaat["totaal_punten"] = eigen_klassement_rij["totaal_punten"] if eigen_klassement_rij else 0

    gesorteerd = sorted(klassement, key=lambda r: -r["totaal_punten"])
    resultaat["positie"] = next(
        (i + 1 for i, r in enumerate(gesorteerd) if r["participant_id"] == participant_id), None
    )

    resultaat["laatste_voorspelling_percentiel"] = None
    if gespeelde_met_voorspelling:
        laatste_wedstrijd = gespeelde_met_voorspelling[-1]  # al gesorteerd op aftrap, dus de laatste hier = de meest recente
        eigen_punten_laatste = eigen_voorspellingen[laatste_wedstrijd["id"]]["punten"] or 0
        with get_connection() as conn:
            medespelers = conn.execute(
                """
                SELECT p.id, COALESCE(pr.punten, 0) AS punten
                FROM participants p
                LEFT JOIN predictions pr ON pr.participant_id = p.id AND pr.match_id = ?
                WHERE p.status = 'goedgekeurd' AND p.id != ?
                """,
                (laatste_wedstrijd["id"], participant_id),
            ).fetchall()
        if medespelers:
            aantal_minder = sum(1 for r in medespelers if r["punten"] < eigen_punten_laatste)
            resultaat["laatste_voorspelling_percentiel"] = round(100 * aantal_minder / len(medespelers))

    if not resultaat["toon_sectie_2"]:
        return resultaat

    # ---------------- Sectie 2 ----------------
    alle_deelnemer_ids = [r["participant_id"] for r in klassement]
    resultaat["hoogste_positie"], resultaat["laagste_positie"] = _positie_hoogste_laagste(
        participant_id, gespeeld, alle_deelnemer_ids
    )

    def _uitkomst(thuis: int, uit: int) -> str:
        if thuis > uit:
            return "thuis"
        if thuis < uit:
            return "uit"
        return "gelijk"

    n = len(gespeelde_met_voorspelling)
    aantal_winnaar_juist = aantal_eind_juist = aantal_rust_juist = 0
    thuis_wedstrijden = uit_wedstrijden = thuis_juist = uit_juist = 0
    top_wedstrijden = top_juist = 0
    links_wedstrijden = links_juist = 0
    rechts_wedstrijden = rechts_juist = 0
    beste = None  # (punten, wedstrijd)
    slechtste = None  # (afwijking, wedstrijd)

    for m in gespeelde_met_voorspelling:
        v = eigen_voorspellingen[m["id"]]
        werkelijke_uitkomst = _uitkomst(m["uitslag_eind_thuis"], m["uitslag_eind_uit"])
        voorspelde_uitkomst = _uitkomst(v["eind_thuis"], v["eind_uit"])
        if werkelijke_uitkomst == voorspelde_uitkomst:
            aantal_winnaar_juist += 1

        eind_juist = (v["eind_thuis"] == m["uitslag_eind_thuis"] and v["eind_uit"] == m["uitslag_eind_uit"])
        rust_juist = (v["rust_thuis"] == m["uitslag_rust_thuis"] and v["rust_uit"] == m["uitslag_rust_uit"])
        if eind_juist:
            aantal_eind_juist += 1
        if rust_juist:
            aantal_rust_juist += 1

        is_thuis = "Ajax" in m["thuis"]
        if is_thuis:
            thuis_wedstrijden += 1
            thuis_juist += eind_juist
        else:
            uit_wedstrijden += 1
            uit_juist += eind_juist

        tegenstander = m["uit"] if is_thuis else m["thuis"]
        if any(t in tegenstander for t in TOP_TEGENSTANDERS):
            top_wedstrijden += 1
            top_juist += eind_juist
        if any(t in tegenstander for t in EREDIVISIE_LINKS):
            links_wedstrijden += 1
            links_juist += eind_juist
        if any(t in tegenstander for t in EREDIVISIE_RECHTS):
            rechts_wedstrijden += 1
            rechts_juist += eind_juist

        if v["punten"] is not None and (beste is None or v["punten"] > beste[0]):
            beste = (v["punten"], m)
        afwijking = abs(v["eind_thuis"] - m["uitslag_eind_thuis"]) + abs(v["eind_uit"] - m["uitslag_eind_uit"])
        if slechtste is None or afwijking > slechtste[0]:
            slechtste = (afwijking, m)

    def _pct(teller: int, noemer: int) -> float | None:
        return round(100 * teller / noemer, 1) if noemer else None

    resultaat.update({
        "pct_winnaar_juist": _pct(aantal_winnaar_juist, n),
        "pct_eindstand_juist": _pct(aantal_eind_juist, n),
        "pct_ruststand_juist": _pct(aantal_rust_juist, n),
        "pct_eindstand_thuis_juist": _pct(thuis_juist, thuis_wedstrijden),
        "pct_eindstand_uit_juist": _pct(uit_juist, uit_wedstrijden),
        "pct_eindstand_top_juist": _pct(top_juist, top_wedstrijden),
        "pct_eindstand_links_juist": _pct(links_juist, links_wedstrijden),
        "pct_eindstand_rechts_juist": _pct(rechts_juist, rechts_wedstrijden),
        "beste_wedstrijd": beste[1] if beste else None,
        "slechtste_wedstrijd": slechtste[1] if slechtste else None,
    })

    jokers = [eigen_voorspellingen[m["id"]] for m in gespeelde_met_voorspelling if eigen_voorspellingen[m["id"]]["joker"]]
    aantal_jokers = len(jokers)
    joker_winnaar_juist = joker_rust_juist = joker_eind_juist = 0
    for m in gespeelde_met_voorspelling:
        v = eigen_voorspellingen[m["id"]]
        if not v["joker"]:
            continue
        if _uitkomst(v["eind_thuis"], v["eind_uit"]) == _uitkomst(m["uitslag_eind_thuis"], m["uitslag_eind_uit"]):
            joker_winnaar_juist += 1
        if v["eind_thuis"] == m["uitslag_eind_thuis"] and v["eind_uit"] == m["uitslag_eind_uit"]:
            joker_eind_juist += 1
        if v["rust_thuis"] == m["uitslag_rust_thuis"] and v["rust_uit"] == m["uitslag_rust_uit"]:
            joker_rust_juist += 1

    resultaat.update({
        "aantal_jokers": aantal_jokers,
        "pct_joker_winnaar_juist": _pct(joker_winnaar_juist, aantal_jokers),
        "pct_joker_ruststand_juist": _pct(joker_rust_juist, aantal_jokers),
        "pct_joker_eindstand_juist": _pct(joker_eind_juist, aantal_jokers),
    })

    if not resultaat["toon_sectie_3"]:
        return resultaat

    # ---------------- Sectie 3 ----------------
    resultaat["seizoensvoorspelling"] = dict(seizoensvoorspelling_rij) if seizoensvoorspelling_rij else None
    resultaat["seizoensuitkomst"] = dict(seizoensuitkomst_rij) if seizoensuitkomst_rij else None

    return resultaat


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


def stop_sync_wedstrijd(match_id: int) -> None:
    """Zet de vlag die de automatische sync negeert voor deze wedstrijd,
    zonder de status/uitslag aan te passen. Bedoeld voor een gestaakte
    wedstrijd: de automatische bron (football-data.org) weet nog niet wat
    de uiteindelijke uitslag wordt, dus de beheerder bevriest de wedstrijd
    eerst en vult de uitslag later handmatig in via set_match_result()
    hierboven (die dezelfde vlag ook al zet)."""
    with get_connection() as conn:
        conn.execute("UPDATE matches SET handmatig_overschreven = 1 WHERE id = ?", (match_id,))


def hervat_sync_wedstrijd(match_id: int) -> None:
    """Heft stop_sync_wedstrijd() weer op: de automatische sync
    (football-data.org) mag deze wedstrijd weer als vanouds bijwerken."""
    with get_connection() as conn:
        conn.execute("UPDATE matches SET handmatig_overschreven = 0 WHERE id = ?", (match_id,))


def set_match_result(match_id: int, rust: tuple[int, int], eind: tuple[int, int]) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE matches SET status = 'afgelopen',
                uitslag_rust_thuis = ?, uitslag_rust_uit = ?,
                uitslag_eind_thuis = ?, uitslag_eind_uit = ?,
                handmatig_overschreven = 1
            WHERE id = ?
            """,
            (rust[0], rust[1], eind[0], eind[1], match_id),
        )
    bereken_en_bewaar_punten(match_id)


def vind_mogelijke_duplicaten(seizoen: str) -> list[tuple[dict, dict]]:
    """Signaleert mogelijke dubbele registraties van dezelfde Eredivisie-
    wedstrijd — het scenario waarin een gestaakte wedstrijd bij hervatting
    een NIEUW fixture-id krijgt in plaats van hetzelfde id te behouden
    (bv. Ajax-FC Groningen, gestaakt 30-11-2025, uitgespeeld 2-12-2025).

    Twee wedstrijden gelden als verdacht als: zelfde twee teams, zelfde
    thuis/uit-richting, in hetzelfde seizoen, met aftraptijden binnen 14
    dagen van elkaar. Een reguliere thuis- en uitwedstrijd tussen dezelfde
    teams liggen in de Eredivisie altijd maanden uit elkaar, dus dit venster
    is bewust krap gehouden om nooit een legitiem duel dubbel te melden."""
    matches = [m for m in get_matches(seizoen) if m["competitie"] == "Eredivisie"]
    gevonden = []
    for i, m1 in enumerate(matches):
        for m2 in matches[i + 1:]:
            if m1["id"] == m2["id"]:
                continue
            if m1["thuis"] == m2["thuis"] and m1["uit"] == m2["uit"]:
                verschil = abs((m2["kickoff"] - m1["kickoff"]).days)
                if verschil <= 14:
                    gevonden.append((m1, m2))
    return gevonden


def get_klassement_met_delta(seizoen: str) -> list[dict]:
    """Klassement zoals get_klassement(), aangevuld met per deelnemer:
    - punten_deze_ronde: de punten uit de meest recent afgeronde wedstrijd
      (over alle competities heen, dus ook een handmatig toegevoegde
      Europese wedstrijd telt mee als dat de laatste was)
    - delta: het aantal plaatsen gestegen (positief) of gedaald (negatief)
      t.o.v. de stand vóór die wedstrijd, 0 bij gelijk gebleven

    Geen aparte geschiedenistabel nodig: de 'vorige stand' wordt simpelweg
    teruggerekend door ieders punten uit de laatste wedstrijd van het
    huidige totaal af te trekken, en op basis daarvan opnieuw te ranken."""
    with get_connection() as conn:
        laatste = conn.execute(
            "SELECT id FROM matches WHERE seizoen = ? AND status = 'afgelopen' ORDER BY kickoff DESC LIMIT 1",
            (seizoen,),
        ).fetchone()
        laatste_match_id = laatste["id"] if laatste else None

        klassement = [dict(r) for r in conn.execute("SELECT * FROM klassement").fetchall()]

        punten_ronde = {}
        if laatste_match_id is not None:
            for rij in conn.execute(
                "SELECT participant_id, punten FROM predictions WHERE match_id = ? AND punten IS NOT NULL",
                (laatste_match_id,),
            ).fetchall():
                punten_ronde[rij["participant_id"]] = rij["punten"]

    for rij in klassement:
        rij["punten_deze_ronde"] = punten_ronde.get(rij["participant_id"], 0)
        rij["totaal_vorige_ronde"] = rij["totaal_punten"] - rij["punten_deze_ronde"]

    klassement_nu = sorted(klassement, key=lambda r: -r["totaal_punten"])
    for i, rij in enumerate(klassement_nu):
        rij["positie_nu"] = i + 1

    klassement_vorig = sorted(klassement, key=lambda r: -r["totaal_vorige_ronde"])
    positie_vorig_per_id = {rij["participant_id"]: i + 1 for i, rij in enumerate(klassement_vorig)}

    for rij in klassement_nu:
        rij["positie_vorig"] = positie_vorig_per_id[rij["participant_id"]]
        rij["delta"] = rij["positie_vorig"] - rij["positie_nu"]

    return klassement_nu


def uitschrijven_reminders(uitschrijf_token: str) -> dict | None:
    """Schrijft een deelnemer uit voor reminder-mails op basis van het token
    uit de uitschrijflink. Geeft de deelnemer terug bij succes, None als het
    token onbekend is (nooit een fout tonen die zou kunnen verraden of een
    token ooit heeft bestaan)."""
    with get_connection() as conn:
        rij = conn.execute(
            "SELECT * FROM participants WHERE uitschrijf_token = ?", (uitschrijf_token,)
        ).fetchone()
        if not rij:
            return None
        conn.execute(
            "UPDATE participants SET ontvangt_reminders = 0 WHERE uitschrijf_token = ?",
            (uitschrijf_token,),
        )
        return dict(rij)


def get_standings_widget(seizoen: str) -> list[dict]:
    with get_connection() as conn:
        gerangschikt = [
            dict(r) for r in conn.execute(
                """
                SELECT *, ROW_NUMBER() OVER (ORDER BY positie, team) AS rang
                FROM standings WHERE seizoen = ?
                ORDER BY rang
                """,
                (seizoen,),
            ).fetchall()
        ]

    totaal = len(gerangschikt)
    if totaal == 0:
        return []

    ajax_rang = next((r["rang"] for r in gerangschikt if "ajax" in r["team"].lower()), None)
    if ajax_rang is None:
        return []

    # Altijd een venster van 3 rijen (of minder als de competitie zelf
    # minder dan 3 teams telt) tonen, verschoven zodat het binnen de tabel
    # blijft: Ajax staat op de middelste regel, TENZIJ Ajax zelf 1e staat
    # (dan tonen we 1-2-3) of laatste staat (dan de laatste 3 posities).
    start = max(1, min(ajax_rang - 1, totaal - 2))
    eind = min(totaal, start + 2)
    return [r for r in gerangschikt if start <= r["rang"] <= eind]
