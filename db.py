"""
db.py
------------------------------------------------------------------
Database-connectie voor J-Poule op een gratis PythonAnywhere-account:
SQLite in plaats van PostgreSQL (geen aparte databaseserver nodig,
werkt zonder de outbound-internetbeperkingen van het gratis plan).

Env var: DB_PATH (standaard: jpoule.db naast dit bestand)
------------------------------------------------------------------
"""

import os
import secrets
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "jpoule.db"))


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # betere gelijktijdigheid bij meerdere workers
    return conn


@contextmanager
def get_connection():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(schema_path: str | None = None) -> None:
    """Eenmalig aan te roepen om een verse database te initialiseren."""
    schema_path = schema_path or os.path.join(os.path.dirname(__file__), "schema_sqlite.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        script = f.read()
    with get_connection() as conn:
        conn.executescript(script)


def run_migrations() -> None:
    """Kleine, achterwaarts-compatibele schemawijzigingen op een bestaande database.
    Wordt bij elke app-start aangeroepen (zie app.py) — elke ALTER TABLE is
    los geprobeerd en genegeerd als de kolom al bestaat, zodat dit veilig
    herhaald kan worden zonder bestaande data te raken."""
    migraties = [
        "ALTER TABLE app_settings ADD COLUMN registratie_sluit_na_wedstrijd INTEGER NOT NULL DEFAULT 2",
        "ALTER TABLE matches ADD COLUMN handmatig_overschreven INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE participants ADD COLUMN ontvangt_reminders INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE participants ADD COLUMN uitschrijf_token TEXT",
    ]
    with get_connection() as conn:
        for sql in migraties:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise

        # standings had ooit een verkeerde primary key (seizoen, positie) --
        # dat ging kapot zodra meerdere teams gelijk stonden (bv. vóór de
        # competitiestart, als alle teams nog op positie 1 met 0 punten
        # staan). Dit hoort een EENMALIGE reparatie te zijn -- daarom eerst
        # checken of de oude, foute sleutel er nog daadwerkelijk staat.
        # (Eerder liep dit onvoorwaardelijk bij elke opstart, waardoor de
        # standen-tabel bij elke herstart van de webapp leeggeveegd werd.)
        tabel_info = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'standings'"
        ).fetchone()
        if not tabel_info or "PRIMARY KEY (seizoen, positie)" in tabel_info["sql"]:
            conn.execute("DROP TABLE IF EXISTS standings")
            conn.execute("""
                CREATE TABLE standings (
                    seizoen         TEXT NOT NULL,
                    positie         INTEGER NOT NULL,
                    team            TEXT NOT NULL,
                    punten          INTEGER NOT NULL,
                    gespeeld        INTEGER NOT NULL DEFAULT 0,
                    bijgewerkt_op   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    PRIMARY KEY (seizoen, team)
                )
            """)

        # Opschoning: geen data van geweigerde aanmeldingen bewaren (kan van
        # vóór deze regel al in de database staan). Veilig herhaalbaar: als
        # er niets (meer) is met status 'geweigerd', verwijdert dit simpelweg 0 rijen.
        conn.execute("DELETE FROM participants WHERE status = 'geweigerd'")

        # Elke deelnemer heeft een uniek uitschrijf-token nodig voor de
        # reminder-mails; bestaande deelnemers (van vóór deze functie) hebben
        # dat nog niet, dus die vullen we hier eenmalig aan.
        zonder_token = conn.execute("SELECT id FROM participants WHERE uitschrijf_token IS NULL").fetchall()
        for rij in zonder_token:
            conn.execute(
                "UPDATE participants SET uitschrijf_token = ? WHERE id = ?",
                (secrets.token_urlsafe(24), rij["id"]),
            )

        # De klassement-view sloot eerder de beheerder-rol uit; dat is nu
        # bewust losgelaten (de beheerder mag zelf ook meespelen en meetellen).
        # Een view heeft geen eigen data, dus herbouwen is risicoloos.
        conn.execute("DROP VIEW IF EXISTS klassement")
        conn.execute("""
            CREATE VIEW klassement AS
            SELECT
                p.id    AS participant_id,
                p.naam,
                COALESCE(SUM(pr.punten), 0)
                    + COALESCE(MAX(sp.punten_na17), 0)
                    + COALESCE(MAX(sp.punten_na34), 0) AS totaal_punten
            FROM participants p
            LEFT JOIN matches m
                ON m.seizoen = (SELECT seizoen_actief FROM app_settings)
            LEFT JOIN predictions pr
                ON pr.participant_id = p.id AND pr.match_id = m.id AND pr.punten IS NOT NULL
            LEFT JOIN season_predictions sp
                ON sp.participant_id = p.id AND sp.seizoen = (SELECT seizoen_actief FROM app_settings)
            WHERE p.status = 'goedgekeurd'
            GROUP BY p.id, p.naam
            ORDER BY totaal_punten DESC
        """)
