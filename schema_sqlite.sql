-- ============================================================
-- J-Poule — SQLite-schema (variant voor een gratis PythonAnywhere-account)
-- ============================================================
-- Functioneel identiek aan schema.sql (PostgreSQL), met aanpassingen:
--   - geen ENUM's -> TEXT + CHECK
--   - geen UUID-generatie in de database -> id's worden in Python
--     gegenereerd (uuid4().hex) vóór het INSERT-statement
--   - TIMESTAMPTZ -> TEXT (ISO 8601), conversie gebeurt in queries.py
--   - CITEXT bestaat niet -> e-mail wordt altijd lowercase opgeslagen
--     door de applicatiecode (zie auth.py), dus een gewone UNIQUE volstaat
-- ============================================================

PRAGMA foreign_keys = ON;

CREATE TABLE participants (
    id              TEXT PRIMARY KEY,
    naam            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    rol             TEXT NOT NULL DEFAULT 'deelnemer' CHECK (rol IN ('deelnemer', 'beheerder')),
    status          TEXT NOT NULL DEFAULT 'aangevraagd' CHECK (status IN ('aangevraagd', 'goedgekeurd', 'geweigerd')),
    magic_token     TEXT,
    magic_token_verloopt_op TEXT,
    aangemaakt_op   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    bijgewerkt_op   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_participants_status ON participants (status);

CREATE TRIGGER trg_participants_bijgewerkt_op
AFTER UPDATE ON participants
BEGIN
    UPDATE participants SET bijgewerkt_op = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id;
END;

CREATE TABLE rules (
    seizoen                 TEXT PRIMARY KEY,
    halftime_punten         INTEGER NOT NULL DEFAULT 2,
    fulltime_punten         INTEGER NOT NULL DEFAULT 4,
    outcome_punten          INTEGER NOT NULL DEFAULT 1,
    joker_kosten             INTEGER NOT NULL DEFAULT 1,
    joker_vermenigvuldiger  INTEGER NOT NULL DEFAULT 2,
    bijgewerkt_op           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE matches (
    id                  INTEGER PRIMARY KEY,   -- API-Football fixture-id
    seizoen             TEXT NOT NULL,
    competitie          TEXT NOT NULL,
    ronde               TEXT,
    thuis               TEXT NOT NULL,
    uit                 TEXT NOT NULL,
    kickoff             TEXT NOT NULL,          -- ISO 8601
    status              TEXT NOT NULL DEFAULT 'gepland' CHECK (status IN ('gepland', 'live', 'afgelopen')),
    oefenwedstrijd      INTEGER NOT NULL DEFAULT 0,
    uitslag_rust_thuis  INTEGER,
    uitslag_rust_uit    INTEGER,
    uitslag_eind_thuis  INTEGER,
    uitslag_eind_uit    INTEGER,
    handmatig_overschreven INTEGER NOT NULL DEFAULT 0,  -- zie queries.set_match_result() / github_sync.py
    bijgewerkt_op       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    CHECK ((uitslag_rust_thuis IS NULL) = (uitslag_rust_uit IS NULL)),
    CHECK ((uitslag_eind_thuis IS NULL) = (uitslag_eind_uit IS NULL))
);

CREATE INDEX idx_matches_kickoff ON matches (kickoff);
CREATE INDEX idx_matches_status  ON matches (status);
CREATE INDEX idx_matches_seizoen ON matches (seizoen);

CREATE TABLE predictions (
    id              TEXT PRIMARY KEY,
    match_id        INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    participant_id  TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    rust_thuis      INTEGER NOT NULL,
    rust_uit        INTEGER NOT NULL,
    eind_thuis      INTEGER NOT NULL,
    eind_uit        INTEGER NOT NULL,
    joker           INTEGER NOT NULL DEFAULT 0,
    punten          INTEGER,
    aangemaakt_op   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    bijgewerkt_op   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    UNIQUE (match_id, participant_id)
);

CREATE INDEX idx_predictions_participant ON predictions (participant_id);

CREATE TRIGGER trg_predictions_bijgewerkt_op
AFTER UPDATE ON predictions
BEGIN
    UPDATE predictions SET bijgewerkt_op = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id;
END;

CREATE TABLE season_predictions (
    id              TEXT PRIMARY KEY,
    participant_id  TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    seizoen         TEXT NOT NULL,
    na17_positie    INTEGER NOT NULL CHECK (na17_positie BETWEEN 1 AND 18),
    na17_punten     INTEGER NOT NULL CHECK (na17_punten >= 0),
    na34_positie    INTEGER NOT NULL CHECK (na34_positie BETWEEN 1 AND 18),
    na34_punten     INTEGER NOT NULL CHECK (na34_punten >= 0),
    punten_na17     INTEGER,
    punten_na34     INTEGER,
    aangemaakt_op   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    bijgewerkt_op   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    UNIQUE (participant_id, seizoen)
);

CREATE TABLE season_results (
    seizoen         TEXT PRIMARY KEY,
    na17_positie    INTEGER,
    na17_punten     INTEGER,
    na34_positie    INTEGER,
    na34_punten     INTEGER,
    bijgewerkt_op   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE standings (
    seizoen         TEXT NOT NULL,
    positie         INTEGER NOT NULL,
    team            TEXT NOT NULL,
    punten          INTEGER NOT NULL,
    gespeeld        INTEGER NOT NULL DEFAULT 0,
    bijgewerkt_op   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    PRIMARY KEY (seizoen, team)
);

CREATE TABLE app_settings (
    singleton       INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton = 1),
    seizoen_actief  TEXT NOT NULL,
    registratie_sluit_na_wedstrijd INTEGER NOT NULL DEFAULT 2,
    bijgewerkt_op   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO app_settings (singleton, seizoen_actief) VALUES (1, '2026/2027');

CREATE TABLE reminders_sent (
    match_id        INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    participant_id  TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    verstuurd_op    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    PRIMARY KEY (match_id, participant_id)
);

INSERT INTO rules (seizoen) VALUES ('2026/2027');

-- ============================================================
-- Klassement-view, gefilterd op het ene actieve seizoen
-- ============================================================

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
ORDER BY totaal_punten DESC;
