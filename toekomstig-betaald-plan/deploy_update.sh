#!/usr/bin/env bash
# ============================================================
# deploy_update.sh
# ------------------------------------------------------------
# Ter plekke uitvoeren in een PythonAnywhere Bash-console (Consoles-tab
# -> Bash), of aanroepen vanaf je eigen machine via SSH (SSH-toegang
# is een functie van betaalde PythonAnywhere-plannen).
#
# Doet 3 dingen:
#   1. Haalt de laatste code op (git pull)
#   2. Installeert eventueel gewijzigde dependencies
#   3. Herlaadt de webapp via de PythonAnywhere API, zodat de
#      wijziging direct live staat — geen handmatige klik op
#      "Reload" in de Web-tab nodig.
#
# Vereist eenmalig:
#   - Dit project as git-repo gekloond op PythonAnywhere:
#       git clone <jouw-repo-url> ~/j-poule-web
#   - Een PythonAnywhere API-token (Account -> API Token) gezet als
#     env var PYTHONANYWHERE_API_TOKEN (zie onderaan hoe).
# ============================================================

set -euo pipefail

PROJECT_DIR="$HOME/j-poule-web"
VENV_DIR="$HOME/.virtualenvs/jpoule"
PA_USERNAME="${PA_USERNAME:-<jouwgebruikersnaam>}"
PA_DOMAIN="${PA_USERNAME}.pythonanywhere.com"
PA_HOST="${PA_HOST:-www.pythonanywhere.com}"   # of eu.pythonanywhere.com bij een EU-account

echo "==> Nieuwste code ophalen..."
cd "$PROJECT_DIR"
git pull

echo "==> Dependencies bijwerken..."
"$VENV_DIR/bin/pip" install -r requirements.txt --quiet

echo "==> Webapp herladen via de PythonAnywhere API..."
curl -s -X POST \
  -H "Authorization: Token ${PYTHONANYWHERE_API_TOKEN}" \
  "https://${PA_HOST}/api/v0/user/${PA_USERNAME}/webapps/${PA_DOMAIN}/reload/" \
  && echo "==> Klaar. De site draait nu de laatste versie." \
  || echo "==> Reload via API mislukt — herlaad handmatig via de Web-tab."

