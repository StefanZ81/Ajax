"""
reminders.py
------------------------------------------------------------------
Stuurt elke deelnemer 24 uur vóór de aftrap van een Ajax-wedstrijd
een e-mail met een directe link naar het voorspelformulier.
Python-equivalent van reminders.js.

Kost geen extra API-Football-calls: dit draait op de programmadata
die daily_schedule_sync() al heeft opgehaald, en wordt aangeroepen
vanuit dezelfde minuutlijkse cron als check_and_sync() (zie ajax_sync.py).

E-mailprovider: Resend (resend.com) als voorbeeld — eenvoudige API,
geschikt voor transactionele mail. Postmark of SendGrid werken
volgens hetzelfde principe; alleen send_email() hoeft dan aangepast.

Vereist: package "requests", env vars RESEND_API_KEY, EMAIL_FROM, APP_BASE_URL
------------------------------------------------------------------
"""

from __future__ import annotations

import html
import os
from datetime import datetime, timedelta, timezone

import requests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "J-Poule <poule@jpoule.nl>")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://jpoule.nl")

# Voorkomt dubbele verzending als de cron elke minuut draait.
# In productie: vervang door een kolom `reminder_sent_at` in de database
# i.p.v. deze in-memory set (die leegloopt bij een herstart van de server).
_verstuurde_reminders: set[str] = set()

MAANDNAMEN = ["januari", "februari", "maart", "april", "mei", "juni",
              "juli", "augustus", "september", "oktober", "november", "december"]
DAGNAMEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]


# ---------------- Hoofdfunctie: elke minuut aan te roepen ----------------

def check_and_send_reminders(fixtures: list[dict], participants: list[dict], predictions: dict) -> None:
    nu = datetime.now(timezone.utc)
    ruim_voor_kickoff = timedelta(hours=24)
    venster = timedelta(minutes=1)  # marge zodat een cron die elke minuut draait het venster niet mist

    te_herinneren = []
    for m in fixtures:
        if m["status"] != "gepland":
            continue
        kickoff = datetime.fromisoformat(m["kickoff"])
        grens = kickoff - ruim_voor_kickoff
        if grens <= nu < grens + venster:
            te_herinneren.append(m)

    goedgekeurde_deelnemers = [p for p in participants if p["rol"] == "deelnemer" and p["status"] == "goedgekeurd"]

    for match in te_herinneren:
        for deelnemer in goedgekeurde_deelnemers:
            sleutel = f"{match['id']}:{deelnemer['id']}"
            if sleutel in _verstuurde_reminders:
                continue

            # Optioneel: geen mail sturen als de deelnemer al heeft voorspeld.
            heeft_al_voorspeld = bool(predictions.get(match["id"], {}).get(deelnemer["id"]))
            if heeft_al_voorspeld:
                _verstuurde_reminders.add(sleutel)
                continue

            send_reminder_email(deelnemer, match)
            _verstuurde_reminders.add(sleutel)
            mark_reminder_sent(match["id"], deelnemer["id"])  # -> eigen database


# ---------------- E-mail opstellen en versturen ----------------

def send_reminder_email(deelnemer: dict, match: dict) -> None:
    link = build_prediction_link(deelnemer, match)
    kickoff = datetime.fromisoformat(match["kickoff"])
    kickoff_tekst = f"{DAGNAMEN[kickoff.weekday()]} {kickoff.day} {MAANDNAMEN[kickoff.month - 1]}, {kickoff.strftime('%H:%M')}"
    tegenstander = match["uit"] if match["thuis"] == "Ajax" else match["thuis"]

    subject = f"Nog 24 uur: geef je voorspelling door voor {match['thuis']} - {match['uit']}"
    naam_safe = html.escape(deelnemer["naam"])
    competitie_safe = html.escape(match["competitie"])

    body_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <div style="background:#15161B; padding:20px; text-align:center;">
        <span style="color:#fff; font-size:20px; font-weight:bold; letter-spacing:1px;">J-POULE</span>
      </div>
      <div style="padding:24px; color:#15161B;">
        <p>Hoi {naam_safe},</p>
        <p>
          Morgen om <strong>{kickoff_tekst}</strong> speelt Ajax tegen
          {html.escape(tegenstander)} ({competitie_safe}). Geef je ruststand- en
          eindstand&shy;voorspelling door vóór de aftrap.
        </p>
        <p style="text-align:center; margin:28px 0;">
          <a href="{link}"
             style="background:#D2122E; color:#fff; padding:12px 24px; border-radius:8px;
                    text-decoration:none; font-weight:bold; display:inline-block;">
            Voorspelling invullen
          </a>
        </p>
        <p style="font-size:12px; color:#6E7180;">
          Deze link brengt je direct naar het voorspelformulier voor deze wedstrijd.
          Voorspellen kan tot de aftrap.
        </p>
      </div>
    </div>"""

    send_email(to=deelnemer["email"], subject=subject, html_body=body_html)


def build_prediction_link(deelnemer: dict, match: dict) -> str:
    # token = korte, niet te raden identifier per deelnemer, zodat de link
    # direct naar het formulier gaat zonder extra inlogstap (magic link).
    # TODO: genereer een echt (liefst tijdelijk geldig) token per deelnemer/wedstrijd
    # i.p.v. het kale participant-id, en verifieer dat token server-side bij binnenkomst.
    token = deelnemer.get("magic_token", "TODO")
    return f"{APP_BASE_URL}/voorspel/{match['id']}?deelnemer={deelnemer['id']}&token={token}"


def send_email(to: str, subject: str, html_body: str) -> None:
    res = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={"from": EMAIL_FROM, "to": to, "subject": subject, "html": html_body},
        timeout=15,
    )
    if not res.ok:
        raise RuntimeError(f"Versturen reminder-mail mislukt ({res.status_code}): {res.text}")


def mark_reminder_sent(match_id: str, participant_id: str) -> None:
    # TODO: sla op in de database, bv.:
    # db.reminders.insert(match_id=match_id, participant_id=participant_id, sent_at=datetime.utcnow())
    pass

