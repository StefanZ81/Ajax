"""
scripts/reminder_sync.py
------------------------------------------------------------------
Draait in GitHub Actions (zie .github/workflows/reminder-sync.yml).
Leest data/reminder_data.json (deelnemers + aankomende wedstrijden,
geëxporteerd door de PythonAnywhere-app via reminder_export.py) en
verstuurt, precies 24 uur voor aftrap, een wedstrijdaankondiging per
e-mail aan alle deelnemers die dat willen ontvangen.

Bijhouden welke wedstrijden al zijn aangekondigd gebeurt in
data/reminders_verzonden.json — dat bestand leeft in dezelfde repo en
wordt door dit script zelf bijgewerkt en teruggecommit, zodat een
wedstrijd nooit twee keer wordt aangekondigd, ook al draait deze
workflow elke paar minuten opnieuw.

Verstuurt via Gmail SMTP (smtp.gmail.com) — geen eigen domein of externe
dienst zoals Resend nodig, en er is geen beperking op wie de mail mag
ontvangen (in tegenstelling tot een niet-geverifieerd Resend-account, dat
alleen naar het eigen aanmeldadres mag versturen).

Vereiste secrets (Settings → Secrets and variables → Actions):
  GMAIL_ADRES            het Gmail-adres waarmee verstuurd wordt
  GMAIL_APP_WACHTWOORD   een 16-tekens 'app-wachtwoord' (NIET het gewone
                          Gmail-wachtwoord) -- aan te maken via
                          myaccount.google.com/security, vereist dat
                          tweestapsverificatie aanstaat op dat account.

Gratis Gmail staat ~500 e-mails per dag toe, ruim voldoende voor een
besloten poule.
------------------------------------------------------------------
"""

from __future__ import annotations

import json
import os
import smtplib
import subprocess
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

REMINDER_DATA_PATH = "data/reminder_data.json"
VERZONDEN_PATH = "data/reminders_verzonden.json"

GMAIL_ADRES = os.environ["GMAIL_ADRES"]
GMAIL_APP_WACHTWOORD = os.environ["GMAIL_APP_WACHTWOORD"]
APP_URL = os.environ.get("APP_URL", "https://ajaxpoule.pythonanywhere.com")

# Marge rond het 24-uursmoment, om dezelfde reden als bij de scores-probe:
# scheduled workflows draaien 'best effort', niet gegarandeerd op de minuut.
# Niet langer een smal venster rond precies 24u vóór aftrap (dat bleek te
# krap: GitHub Actions' planning is "best effort", niet gegarandeerd precies
# op tijd -- eenzelfde soort onbetrouwbaarheid die we ook al bij het
# wedstrijd-probevenster tegenkwamen). In plaats daarvan: een ruime periode
# waarbinnen een reminder als "op tijd" geldt, met de deduplicatie via
# verzonden.json (zie laad_verzonden/bewaar_verzonden) als vangnet tegen
# dubbele verzending als een wedstrijd toevallig in meerdere cycli binnen
# deze periode valt.
VENSTER_UREN = 24
VENSTER_MARGE = timedelta(hours=2)

_DAGEN_VOL = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
_MAANDEN_VOL = [
    "januari", "februari", "maart", "april", "mei", "juni", "juli",
    "augustus", "september", "oktober", "november", "december",
]


def nl_datum_lang(dt_utc: datetime) -> tuple[str, str]:
    """Geeft (datum, tijd) terug in het Nederlands, in de Amsterdamse
    tijdzone -- géén afhankelijkheid van de systeemlocale van de
    GitHub Actions-runner."""
    dt = dt_utc.astimezone(ZoneInfo("Europe/Amsterdam"))
    datum = f"{_DAGEN_VOL[dt.weekday()]} {dt.day} {_MAANDEN_VOL[dt.month - 1]}"
    tijd = dt.strftime("%H:%M")
    return datum, tijd


def laad_reminder_data() -> dict:
    with open(REMINDER_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def laad_verzonden() -> set[int]:
    if not os.path.exists(VERZONDEN_PATH):
        return set()
    with open(VERZONDEN_PATH, "r", encoding="utf-8") as f:
        return set(json.load(f))


def bewaar_verzonden(verzonden: set[int]) -> None:
    with open(VERZONDEN_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(verzonden), f)


def binnen_venster(kickoff: datetime, nu: datetime) -> bool:
    moment = kickoff - timedelta(hours=VENSTER_UREN)
    return moment - VENSTER_MARGE <= nu <= moment + VENSTER_MARGE


def stel_mail_samen(deelnemer: dict, wedstrijd: dict) -> tuple[str, str]:
    thuis = wedstrijd["thuis"]
    uit = wedstrijd["uit"]
    thuis_of_uit = "thuis" if "Ajax" in thuis else "uit"
    tegenstander = uit if "Ajax" in thuis else thuis
    competitie = wedstrijd["competitie"]

    kickoff = datetime.fromisoformat(wedstrijd["kickoff"])
    datum, tijd = nl_datum_lang(kickoff)

    link_voorspelling = f"{APP_URL}/wedstrijd/{wedstrijd['id']}"
    link_uitschrijven = f"{APP_URL}/uitschrijven/{deelnemer['uitschrijf_token']}"

    onderwerp = f"{thuis} – {uit} · {competitie}"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <div style="background:#15161B; padding:20px; text-align:center;">
        <span style="color:#fff; font-size:20px; font-weight:bold; letter-spacing:1px;">J-POULE</span>
      </div>
      <div style="padding:24px; color:#15161B;">
        <p>Hoi {deelnemer['naam']},</p>
        <p>
          Ajax speelt op <b>{datum} om {tijd} uur {thuis_of_uit} tegen {tegenstander}</b>,
          in de {competitie}.
        </p>
        <p>Je hebt tot het aanvangstijdstip de tijd om je ruststand en eindstand door te geven.</p>
        <p style="text-align:center; margin:28px 0;">
          <a href="{link_voorspelling}"
             style="background:#D2122E; color:#fff; padding:12px 24px; border-radius:8px;
                    text-decoration:none; font-weight:bold; display:inline-block;">
            Geef je voorspelling door
          </a>
        </p>
        <p>Denk eraan dat je de joker kunt inzetten voor dubbele punten.</p>
        <p>Veel succes en plezier met de wedstrijd!</p>
        <p>— J-Poule</p>
        <p style="font-size:11px; color:#6E7180; margin-top:32px;">
          Wil je deze e-mails niet meer ontvangen?
          <a href="{link_uitschrijven}" style="color:#6E7180;">Uitschrijven</a>
        </p>
      </div>
    </div>"""
    return onderwerp, html


def verstuur_mail(naar: str, onderwerp: str, html: str) -> None:
    bericht = MIMEMultipart("alternative")
    bericht["Subject"] = onderwerp
    bericht["From"] = f"J-Poule <{GMAIL_ADRES}>"
    bericht["To"] = naar
    bericht.attach(MIMEText(html, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
        server.starttls()
        server.login(GMAIL_ADRES, GMAIL_APP_WACHTWOORD)
        server.sendmail(GMAIL_ADRES, naar, bericht.as_string())


def main() -> None:
    data = laad_reminder_data()
    verzonden = laad_verzonden()
    nu = datetime.now(timezone.utc)

    nieuw_verzonden = False
    for wedstrijd in data["wedstrijden"]:
        if wedstrijd["id"] in verzonden:
            continue
        kickoff = datetime.fromisoformat(wedstrijd["kickoff"])
        if not binnen_venster(kickoff, nu):
            continue

        print(f"[reminder_sync] Wedstrijd {wedstrijd['id']} ({wedstrijd['thuis']}-{wedstrijd['uit']}) "
              f"binnen het 24u-venster, {len(data['deelnemers'])} deelnemers aanschrijven.")
        for deelnemer in data["deelnemers"]:
            onderwerp, html = stel_mail_samen(deelnemer, wedstrijd)
            try:
                verstuur_mail(deelnemer["email"], onderwerp, html)
                print(f"  -> verstuurd aan {deelnemer['email']}")
            except Exception as e:
                # Eén mislukte e-mail mag de rest niet blokkeren.
                print(f"  -> MISLUKT voor {deelnemer['email']}: {e}")

        verzonden.add(wedstrijd["id"])
        nieuw_verzonden = True

    if not nieuw_verzonden:
        print("[reminder_sync] Geen wedstrijd binnen het 24u-venster deze cyclus.")
        return

    bewaar_verzonden(verzonden)
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"])
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    subprocess.run(["git", "add", VERZONDEN_PATH])
    subprocess.run(["git", "commit", "-m", "Reminders verzonden bijgewerkt [skip ci]"])
    # Meerdere workflows (ajax-data-sync, nieuws-sync) committen onafhankelijk
    # naar dezelfde main-branch -- eerst rebasen voorkomt een mislukte push
    # als een van de andere workflows net iets eerder pushte.
    subprocess.run(["git", "pull", "--rebase", "--autostash"])
    subprocess.run(["git", "push"])


if __name__ == "__main__":
    main()
