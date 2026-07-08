"""
bootstrap_admin.py
------------------------------------------------------------------
Maakt de allereerste beheerder aan, of zet een bestaand account om
tot beheerder. Eenmalig te draaien bij het opzetten van de database
— hierna verloopt alles via promote_to_admin()/demote_to_participant()
in auth.py, uitgevoerd door een reeds ingelogde beheerder.

Gebruik:
    ADMIN_NAAM="Jouw naam" \\
    ADMIN_EMAIL="jij@voorbeeld.nl" \\
    ADMIN_WACHTWOORD="EenSterkWachtwoord123" \\
    python3 bootstrap_admin.py

Waarom een apart script i.p.v. een rol-veld in het registratieformulier:
zo kan niemand zich via de publieke aanmeldflow beheerder maken — dat
kanaal accepteert principieel geen rol-parameter van de client.
------------------------------------------------------------------
"""

import os
import sys

from auth import ValidationError, hash_password, valideer_email, valideer_wachtwoord
from db import get_connection, init_db


def main() -> None:
    naam = os.environ.get("ADMIN_NAAM", "").strip()
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    wachtwoord = os.environ.get("ADMIN_WACHTWOORD", "")

    if not naam or len(naam) < 2:
        sys.exit("Fout: ADMIN_NAAM ontbreekt of is te kort.")
    if not valideer_email(email):
        sys.exit("Fout: ADMIN_EMAIL is geen geldig e-mailadres.")
    fouten = valideer_wachtwoord(wachtwoord)
    if fouten:
        sys.exit("Fout: " + " ".join(fouten))

    from db import DB_PATH
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} bestaat nog niet — schema aanmaken...")
        init_db()

    password_hash = hash_password(wachtwoord)

    with get_connection() as conn:
        bestaand = conn.execute("SELECT id FROM participants WHERE email = ?", (email,)).fetchone()
        if bestaand:
            conn.execute(
                "UPDATE participants SET rol='beheerder', status='goedgekeurd', naam=? WHERE email=?",
                (naam, email),
            )
            participant_id = bestaand["id"]
        else:
            import secrets
            participant_id = secrets.token_hex(16)
            conn.execute(
                "INSERT INTO participants (id, naam, email, password_hash, rol, status) "
                "VALUES (?, ?, ?, ?, 'beheerder', 'goedgekeurd')",
                (participant_id, naam, email, password_hash),
            )

    print(f"Klaar. Beheerder '{naam}' <{email}> is aangemaakt/bijgewerkt (id={participant_id}).")
    print("Log hiermee in via de app; het wachtwoord is nu al versleuteld opgeslagen.")


if __name__ == "__main__":
    main()
