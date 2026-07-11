"""
mail.py
------------------------------------------------------------------
Verstuurt transactionele e-mail (op dit moment: wachtwoord-reset-links)
via Resend (resend.com).

LET OP — werkt niet op het gratis PythonAnywhere-plan: api.resend.com
staat niet op de gratis outbound-whitelist. send_email() faalt dan met
een verbindingsfout; de aanroepende code vangt dat af (zie app.py) zodat
de gebruiker nooit een harde foutmelding krijgt — maar de e-mail komt
dus niet aan totdat je account naar een betaald plan gaat (zie
DEPLOY_GRATIS_PLAN.md, stap 7).

Vereist (op een betaald plan): env vars RESEND_API_KEY, EMAIL_FROM
------------------------------------------------------------------
"""

from __future__ import annotations

import os

import requests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "J-Poule <poule@jpoule.nl>")


def send_email(to: str, subject: str, html: str) -> None:
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is niet ingesteld — e-mail kan niet verstuurd worden.")
    res = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={"from": EMAIL_FROM, "to": to, "subject": subject, "html": html},
        timeout=10,
    )
    if not res.ok:
        raise RuntimeError(f"Versturen e-mail mislukt ({res.status_code}): {res.text}")


def wachtwoord_reset_email(naam: str, reset_link: str) -> tuple[str, str]:
    """Geeft (onderwerp, html) terug voor de wachtwoord-reset-mail."""
    subject = "Wachtwoord opnieuw instellen — J-Poule"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <div style="background:#15161B; padding:20px; text-align:center;">
        <span style="color:#fff; font-size:20px; font-weight:bold; letter-spacing:1px;">J-POULE</span>
      </div>
      <div style="padding:24px; color:#15161B;">
        <p>Hoi {naam},</p>
        <p>Je hebt een nieuw wachtwoord aangevraagd voor J-Poule. Klik op de knop hieronder om een nieuw wachtwoord in te stellen. Deze link is 30 minuten geldig.</p>
        <p style="text-align:center; margin:28px 0;">
          <a href="{reset_link}"
             style="background:#D2122E; color:#fff; padding:12px 24px; border-radius:8px;
                    text-decoration:none; font-weight:bold; display:inline-block;">
            Nieuw wachtwoord instellen
          </a>
        </p>
        <p style="font-size:12px; color:#6E7180;">
          Heb je dit niet aangevraagd? Dan kun je deze e-mail gewoon negeren — je wachtwoord blijft ongewijzigd.
        </p>
      </div>
    </div>"""
    return subject, html
