"""
auth.py
------------------------------------------------------------------
Registratie en login op basis van e-mail + wachtwoord.
Python-equivalent van auth.js.

Wachtwoorden worden nooit als platte tekst opgeslagen. Ze worden
gehasht met scrypt (onderdeel van Python's standaardbibliotheek
`hashlib`, geen extra package nodig) — een algoritme dat bewust
traag en kostbaar is om te kraken, met een unieke salt per gebruiker.

Flow:
    1. register_participant() -> account met status "aangevraagd"
    2. beheerder keurt goed    -> status "goedgekeurd"
    3. login()                 -> alleen mogelijk bij status "goedgekeurd"

Vereist: Python 3.8+, env var SESSION_SECRET (lange, willekeurige string)
------------------------------------------------------------------
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Optional

from db import get_connection

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

# Weiger BEWUST op te starten met een lege of nog-niet-vervangen sleutel --
# een voorspelbare/publiek bekende sleutel betekent dat iedereen een geldig
# beheerder-sessietoken kan vervalsen zonder in te loggen. Dit voorkomt dat
# zo'n misconfiguratie ooit stilzwijgend blijft staan (zoals eerder gebeurde
# met de placeholder-tekst uit wsgi_pythonanywhere.py).
_ONVEILIGE_SESSION_SECRETS = {"", "vervang-dit-door-een-lange-willekeurige-string"}
if SESSION_SECRET in _ONVEILIGE_SESSION_SECRETS:
    raise RuntimeError(
        "SESSION_SECRET is niet (goed) ingesteld — de app start hierdoor bewust niet op, "
        "want dit is een kritiek beveiligingsprobleem (iedereen zou anders een beheerder-"
        "sessie kunnen vervalsen). Genereer een echte, willekeurige sleutel:\n"
        '  python3 -c "import secrets; print(secrets.token_hex(32))"\n'
        "en zet die als SESSION_SECRET in je WSGI-configuratiebestand."
    )

SESSION_GELDIGHEID_S = 30 * 24 * 60 * 60  # 30 dagen

EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ValidationError(Exception):
    """Fout die veilig als foutmelding aan de gebruiker getoond mag worden."""


# ---------------- Wachtwoord hashen & verifiëren ----------------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    derived = hashlib.scrypt(password.encode(), salt=salt.encode(), n=2**14, r=8, p=1, dklen=64)
    return f"{salt}:{derived.hex()}"


def verify_password(password: str, opgeslagen_hash: str) -> bool:
    salt, hash_hex = opgeslagen_hash.split(":")
    derived = hashlib.scrypt(password.encode(), salt=salt.encode(), n=2**14, r=8, p=1, dklen=64)
    opgeslagen = bytes.fromhex(hash_hex)
    # compare_digest voorkomt dat een aanvaller via responstijd kan afleiden
    # hoeveel tekens van het wachtwoord al kloppen.
    return hmac.compare_digest(derived, opgeslagen)


# ---------------- Validatie ----------------

def valideer_email(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email))


def valideer_wachtwoord(password: str) -> list[str]:
    fouten = []
    if len(password) < 10:
        fouten.append("Wachtwoord moet minimaal 10 tekens bevatten.")
    if not re.search(r"[A-Z]", password):
        fouten.append("Wachtwoord moet minimaal 1 hoofdletter bevatten.")
    if not re.search(r"[0-9]", password):
        fouten.append("Wachtwoord moet minimaal 1 cijfer bevatten.")
    return fouten


# ---------------- Registratie ----------------

def register_participant(naam: str, email: str, password: str) -> dict:
    email = email.strip().lower()

    if not naam or len(naam.strip()) < 2:
        raise ValidationError("Vul je volledige naam in.")
    if not valideer_email(email):
        raise ValidationError("Vul een geldig e-mailadres in.")
    fouten = valideer_wachtwoord(password)
    if fouten:
        raise ValidationError(" ".join(fouten))

    bestaand = find_participant_by_email(email)  # -> eigen database
    if bestaand:
        # Bewust geen onderscheid tussen "bestaat al" en andere fouten,
        # om user enumeration te voorkomen.
        raise ValidationError("Registreren is niet gelukt. Controleer je gegevens of neem contact op met de beheerder.")

    nieuwe_deelnemer = {
        "id": secrets.token_hex(16),
        "naam": naam.strip(),
        "email": email,
        "password_hash": hash_password(password),
        "rol": "deelnemer",  # hardcoded — een client kan dit nooit overschrijven, zie ook onderaan dit bestand
        "status": "aangevraagd",  # wacht op goedkeuring door beheerder
        "uitschrijf_token": secrets.token_urlsafe(24),
    }
    opgeslagen = save_participant(nieuwe_deelnemer)  # -> database, geeft de volledige rij terug

    # TODO: stuur een bevestigingsmail en/of een meldingsmail naar de beheerder.

    return {"id": opgeslagen["id"], "status": opgeslagen["status"]}


# ---------------- Login ----------------

def login(email: str, password: str) -> dict:
    email = email.strip().lower()
    deelnemer = find_participant_by_email(email)  # -> eigen database

    def generieke_fout() -> ValidationError:
        return ValidationError("E-mailadres of wachtwoord onjuist.")

    if not deelnemer:
        raise generieke_fout()
    if not verify_password(password, deelnemer["password_hash"]):
        raise generieke_fout()

    if deelnemer["status"] == "aangevraagd":
        raise ValidationError("Je account wacht nog op goedkeuring door de beheerder.")
    if deelnemer["status"] == "geweigerd":
        raise ValidationError("Je aanvraag is niet goedgekeurd. Neem contact op met de beheerder.")

    token = create_session_token(deelnemer)
    return {
        "token": token,
        "deelnemer": {"id": deelnemer["id"], "naam": deelnemer["naam"], "email": deelnemer["email"], "rol": deelnemer["rol"]},
    }


# ---------------- Sessietoken (zelf-ondertekend, geen extra library) ----------------

def create_session_token(deelnemer: dict) -> str:
    payload = {"sub": deelnemer["id"], "rol": deelnemer["rol"], "exp": time.time() + SESSION_GELDIGHEID_S}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    handtekening = hmac.new(SESSION_SECRET.encode(), payload_b64, hashlib.sha256).digest()
    handtekening_b64 = base64.urlsafe_b64encode(handtekening).rstrip(b"=")
    return f"{payload_b64.decode()}.{handtekening_b64.decode()}"


def verify_session_token(token: str) -> Optional[dict]:
    try:
        payload_b64, handtekening_b64 = token.split(".")
    except ValueError:
        return None

    verwacht = hmac.new(SESSION_SECRET.encode(), payload_b64.encode(), hashlib.sha256).digest()
    verwacht_b64 = base64.urlsafe_b64encode(verwacht).rstrip(b"=").decode()
    if not hmac.compare_digest(handtekening_b64, verwacht_b64):
        return None

    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    if payload["exp"] < time.time():
        return None  # verlopen sessie
    return payload


# ---------------- Wachtwoord wijzigen (schets) ----------------

def wijzig_wachtwoord(deelnemer_id: str, huidig_wachtwoord: str, nieuw_wachtwoord: str) -> None:
    deelnemer = find_participant_by_id(deelnemer_id)  # -> eigen database
    if not verify_password(huidig_wachtwoord, deelnemer["password_hash"]):
        raise ValidationError("Huidig wachtwoord onjuist.")

    fouten = valideer_wachtwoord(nieuw_wachtwoord)
    if fouten:
        raise ValidationError(" ".join(fouten))

    deelnemer["password_hash"] = hash_password(nieuw_wachtwoord)
    save_participant(deelnemer)


# TODO "wachtwoord vergeten": genereer een eenmalig, kortlopend token (zelfde
# principe als create_session_token maar bv. 30 min geldig), mail daar een
# resetlink mee, en verifieer het token op de resetpagina.


# ---------------- Wachtwoord vergeten ----------------
# Hergebruikt de magic_token/magic_token_verloopt_op-kolommen die al in het
# schema stonden (oorspronkelijk bedoeld voor e-mail-reminderlinks).
#
# Belangrijk: request_password_reset() geeft BEWUST nooit terug of het
# e-mailadres bestaat — de aanroepende route toont altijd dezelfde generieke
# melding, ongeacht het resultaat, om user enumeration te voorkomen (zelfde
# principe als bij login()).

WACHTWOORD_RESET_GELDIGHEID_S = 30 * 60  # 30 minuten


def request_password_reset(email: str) -> Optional[str]:
    """Maakt (indien het e-mailadres bestaat) een resettoken aan en geeft die
    terug zodat de aanroeper 'm kan e-mailen. Geeft None terug als het
    e-mailadres niet bestaat — de aanroeper moet dit verschil NOOIT aan de
    gebruiker laten zien."""
    email = email.strip().lower()
    deelnemer = find_participant_by_email(email)
    if not deelnemer:
        return None

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    verloopt_op = time.time() + WACHTWOORD_RESET_GELDIGHEID_S

    with get_connection() as conn:
        conn.execute(
            "UPDATE participants SET magic_token = ?, magic_token_verloopt_op = ? WHERE id = ?",
            (token_hash, str(verloopt_op), deelnemer["id"]),
        )
    return token


def reset_password_with_token(token: str, nieuw_wachtwoord: str) -> None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM participants WHERE magic_token = ?", (token_hash,)
        ).fetchone()

    if not row:
        raise ValidationError("Deze link is ongeldig of al gebruikt. Vraag een nieuwe aan.")

    deelnemer = dict(row)
    if not deelnemer["magic_token_verloopt_op"] or float(deelnemer["magic_token_verloopt_op"]) < time.time():
        raise ValidationError("Deze link is verlopen. Vraag een nieuwe aan.")

    fouten = valideer_wachtwoord(nieuw_wachtwoord)
    if fouten:
        raise ValidationError(" ".join(fouten))

    deelnemer["password_hash"] = hash_password(nieuw_wachtwoord)
    with get_connection() as conn:
        conn.execute(
            "UPDATE participants SET password_hash = ?, magic_token = NULL, magic_token_verloopt_op = NULL WHERE id = ?",
            (deelnemer["password_hash"], deelnemer["id"]),
        )


# ---------------- Database-koppelpunten ----------------

def find_participant_by_email(email: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM participants WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def find_participant_by_id(participant_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM participants WHERE id = ?", (participant_id,)).fetchone()
        return dict(row) if row else None


def save_participant(deelnemer: dict) -> dict:
    """Upsert op id: nieuw account -> insert, bestaand account -> update.
    uitschrijf_token wordt bewust NOOIT via de UPDATE-tak aangepast --
    die moet blijvend hetzelfde blijven, anders breekt een eerder
    verstuurde uitschrijflink."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO participants (id, naam, email, password_hash, rol, status, uitschrijf_token)
            VALUES (:id, :naam, :email, :password_hash, :rol, :status, :uitschrijf_token)
            ON CONFLICT (id) DO UPDATE SET
                naam = excluded.naam, email = excluded.email, password_hash = excluded.password_hash,
                rol = excluded.rol, status = excluded.status
            """,
            deelnemer,
        )
        row = conn.execute("SELECT * FROM participants WHERE id = ?", (deelnemer["id"],)).fetchone()
        return dict(row)


# ---------------- Beheerders configureren ----------------
#
# Er zijn precies twee manieren om iemand beheerder te maken — bewust NIET
# via het publieke registratieformulier (register_participant() hardcodeert
# rol="deelnemer" en accepteert geen rol-parameter van de client, zie hierboven):
#
#   1. bootstrap_admin.py  — maakt de allereerste beheerder aan, één keer
#      te draaien bij het opzetten van de database (zie dat bestand).
#   2. promote_to_admin()  — hieronder; alleen een reeds ingelogde beheerder
#      kan een goedgekeurde deelnemer promoveren. Koppel dit in de app aan
#      een knop "Maak beheerder" die alleen zichtbaar is in de beheerder-UI.

def promote_to_admin(uitvoerder_sessie: dict, doel_participant_id: str) -> dict:
    if uitvoerder_sessie.get("rol") != "beheerder":
        raise ValidationError("Alleen een beheerder kan andere accounts beheerder maken.")

    doel = find_participant_by_id(doel_participant_id)
    if not doel:
        raise ValidationError("Account niet gevonden.")
    if doel["status"] != "goedgekeurd":
        raise ValidationError("Alleen een goedgekeurd account kan beheerder worden.")

    doel["rol"] = "beheerder"
    return save_participant(doel)


def demote_to_participant(uitvoerder_sessie: dict, doel_participant_id: str) -> dict:
    if uitvoerder_sessie.get("rol") != "beheerder":
        raise ValidationError("Alleen een beheerder kan deze wijziging maken.")
    if uitvoerder_sessie.get("sub") == doel_participant_id:
        raise ValidationError("Je kunt jezelf niet degraderen — vraag een andere beheerder.")

    doel = find_participant_by_id(doel_participant_id)
    if not doel:
        raise ValidationError("Account niet gevonden.")

    doel["rol"] = "deelnemer"
    return save_participant(doel)
