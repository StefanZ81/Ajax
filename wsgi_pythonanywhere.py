"""
wsgi_pythonanywhere.py
------------------------------------------------------------------
Dit is GEEN los te draaien bestand — kopieer de relevante regels
hieronder in het WSGI-configuratiebestand dat PythonAnywhere al voor
je heeft aangemaakt op:
    /var/www/<jouwgebruikersnaam>_pythonanywhere_com_wsgi.py

(Web-tab -> jouw webapp -> "WSGI configuration file", link bovenaan de pagina.)
------------------------------------------------------------------
"""

import os
import sys

# Pas aan naar waar je de map j-poule-web hebt gezet:
project_home = "/home/<jouwgebruikersnaam>/j-poule-web"
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# SQLite-bestand leeft gewoon in je project-map — geen aparte database nodig.
os.environ["DB_PATH"] = f"{project_home}/jpoule.db"
os.environ["SESSION_SECRET"] = "vervang-dit-door-een-lange-willekeurige-string"
os.environ["FLASK_SECRET_KEY"] = "vervang-dit-ook-door-een-lange-willekeurige-string"

# Op het gratis plan is uitgaand internet standaard geblokkeerd, behalve via
# deze proxy naar een beperkte whitelist (o.a. github.com). De interactieve
# Bash-console heeft dit al automatisch klaarstaan, maar het webproces
# (waar deze app in draait) niet -- vandaar hier expliciet instellen. Nodig
# voor 'git push' vanuit backup_database.py/reminder_export.py; 'requests'-
# aanroepen (github_sync.py, nieuws_sync.py) werkten al zonder dit, maar
# git is een apart programma met zijn eigen proxy-instelling.
os.environ["http_proxy"] = "http://proxy.server:3128"
os.environ["https_proxy"] = "http://proxy.server:3128"

# Waar de website de door GitHub Actions bijgehouden programma/uitslagen-
# tabel vandaan haalt (via raw.githubusercontent.com, wél whitelisted
# op het gratis plan):
os.environ["GITHUB_OWNER"] = "<jouw-github-gebruikersnaam>"
os.environ["GITHUB_REPO"] = "j-poule-web"
os.environ["GITHUB_BRANCH"] = "main"

# Deze twee werken pas zodra je uitgaand internet hebt (betaald plan):
os.environ["API_FOOTBALL_KEY"] = "jouw-api-football-sleutel"
os.environ["GMAIL_ADRES"] = "jouw-gmail-adres@gmail.com"
os.environ["GMAIL_APP_WACHTWOORD"] = "jouw-16-tekens-app-wachtwoord"
# RESEND_API_KEY / EMAIL_FROM niet meer nodig sinds mail.py naar Gmail SMTP
# is overgestapt (werkt, in tegenstelling tot Resend, wél op het gratis
# plan) -- mogen blijven staan (onschadelijk) of weggehaald worden.
os.environ["APP_BASE_URL"] = "https://<jouwgebruikersnaam>.pythonanywhere.com"

from app import app as application  # noqa: E402  (PythonAnywhere verwacht exact deze naam: 'application')
