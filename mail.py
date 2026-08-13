"""
mail.py
------------------------------------------------------------------
Verstuurt transactionele e-mail (wachtwoord-reset-links) via Gmail SMTP
-- hetzelfde adres en dezelfde methode als scripts/reminder_sync.py
gebruikt voor de wedstrijdaankondigingen.

In tegenstelling tot de eerder gebruikte Resend-aanpak werkt dit WEL op
het gratis PythonAnywhere-plan: PythonAnywhere staat voor gratis
accounts geen willekeurige SMTP-servers toe, maar maakt daar specifiek
een uitzondering voor smtp.gmail.com (bevestigd door PythonAnywhere
zelf, zie hun forum/documentatie).

Vereist: dezelfde env vars als de GitHub Actions-workflow al gebruikt --
GMAIL_ADRES, GMAIL_APP_WACHTWOORD -- maar dan ALS WSGI-omgevingsvariabele
op PythonAnywhere zelf (naast de bestaande GitHub Actions-secrets, dit
zijn twee aparte plekken waar dezelfde twee waarden moeten staan).
------------------------------------------------------------------
"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

GMAIL_ADRES = os.environ.get("GMAIL_ADRES", "")
GMAIL_APP_WACHTWOORD = os.environ.get("GMAIL_APP_WACHTWOORD", "")


def send_email(to: str, subject: str, html: str) -> None:
    if not GMAIL_ADRES or not GMAIL_APP_WACHTWOORD:
        raise RuntimeError(
            "GMAIL_ADRES/GMAIL_APP_WACHTWOORD zijn niet ingesteld — e-mail kan niet verstuurd worden."
        )

    bericht = MIMEMultipart("alternative")
    bericht["Subject"] = subject
    bericht["From"] = f"J-Poule <{GMAIL_ADRES}>"
    bericht["To"] = to
    bericht.attach(MIMEText(html, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
        server.starttls()
        server.login(GMAIL_ADRES, GMAIL_APP_WACHTWOORD)
        server.sendmail(GMAIL_ADRES, to, bericht.as_string())


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
