# J-Poule op PythonAnywhere — stappenplan

## 0. Welk PythonAnywhere-plan?

Deze app heeft nodig:
- **Onbeperkt uitgaand internet** (voor API-Football en Resend) → vereist een **betaald** plan; het gratis plan staat alleen een whitelist van sites toe en `api-sports.io`/`api.resend.com` staan daar niet standaard op.
- **Een Always-on task** (voor de per-minuut synchronisatie) → beschikbaar vanaf het **Hacker-plan ($5/mnd)**.
- **PostgreSQL** → PythonAnywhere's eigen gehoste Postgres vereist het duurdere **Custom-plan**. Goedkoper alternatief: een gratis/goedkope externe Postgres bij **Neon** of **Supabase** — die werkt prima vanaf het Hacker-plan, want dat heeft al onbeperkt uitgaand verkeer nodig.

**Advies:** Hacker-plan + externe Postgres (Neon/Supabase free tier).

## 1. Code op de server zetten

In een PythonAnywhere Bash-console:
```bash
git clone <jouw-git-repository-url> ~/j-poule-web
cd ~/j-poule-web
mkvirtualenv --python=python3.12 jpoule
pip install -r requirements.txt
```

## 2. Database opzetten

```bash
psql "<jouw-connectiestring-van-neon-of-supabase>" -f schema.sql
```

Vul daarna de eerste beheerder in (zie ook "Hoe configureren we de beheerders?" eerder in dit gesprek):
```bash
ADMIN_NAAM="Jouw naam" ADMIN_EMAIL="jij@voorbeeld.nl" ADMIN_WACHTWOORD="EenSterkWachtwoord123" \
    DATABASE_URL="<jouw-connectiestring>" \
    python3 bootstrap_admin.py
```

## 3. De webapp aanmaken

- Web-tab → "Add a new web app" → **Manual configuration** (niet "Flask", want dan overschrijft PythonAnywhere je bestandsstructuur) → juiste Python-versie.
- Bij **"Virtualenv"**: vul `/home/<jouwgebruikersnaam>/.virtualenvs/jpoule` in.
- Bij **"Code" → "WSGI configuration file"**: open het bestand en vervang de inhoud door die uit `wsgi_pythonanywhere.py` (met je eigen gebruikersnaam en secrets ingevuld).
- Bij **"Static files"**: voeg toe: URL `/static/` → Directory `/home/<jouwgebruikersnaam>/j-poule-web/static`.
- Klik **Reload**.

Je site draait nu op `https://<jouwgebruikersnaam>.pythonanywhere.com`.

## 4. Always-on task voor de synchronisatie

Tasks-tab → "Always-on tasks" → nieuwe task met als command:
```
/home/<jouwgebruikersnaam>/.virtualenvs/jpoule/bin/python /home/<jouwgebruikersnaam>/j-poule-web/always_on_sync.py
```
Dit proces blijft continu draaien en bepaalt zelf (zie `ajax_sync.py`) of een gegeven minuut een echte API-Football-call oplevert of niet.

## 5. De app direct kunnen bijwerken

Gebruik `deploy_update.sh` (in deze map) — vul je gebruikersnaam in en zet eenmalig je API-token klaar:
```bash
# eenmalig: token aanmaken via Account -> API Token op pythonanywhere.com
export PYTHONANYWHERE_API_TOKEN="<jouw-token>"
```
Daarna, telkens als je een update wilt doorvoeren (lokaal wijzigingen gepusht naar git):
```bash
bash ~/j-poule-web/deploy_update.sh
```
Dat haalt de nieuwste code op, installeert nieuwe dependencies, en herlaadt de webapp via de PythonAnywhere API — geen handmatige klik in de UI nodig. Wil je dit vanaf je eigen laptop kunnen draaien in plaats van in de PythonAnywhere-console: dat kan via SSH, ook een functie van betaalde plannen.

## 6. Automatisch bijwerken bij elke `git push` (GitHub Actions)

`.github/workflows/deploy.yml` logt via SSH in op PythonAnywhere en draait daar `deploy_update.sh`, bij elke push naar `main`.

**Eenmalig instellen:**

1. **SSH-sleutelpaar voor GitHub** (los van je eigen sleutel):
   ```bash
   ssh-keygen -t ed25519 -C "github-actions-jpoule" -f github_actions_key -N ""
   ```
   Zet de **publieke** sleutel (`github_actions_key.pub`) op PythonAnywhere in een Bash-console:
   ```bash
   cat >> ~/.ssh/authorized_keys < github_actions_key.pub   # of plak de inhoud handmatig
   ```
2. **API-token**: PythonAnywhere → Account → "API Token"-tab → token aanmaken.
3. In je GitHub-repository: **Settings → Secrets and variables → Actions → New repository secret**, en voeg toe:

   | Secret | Waarde |
   |---|---|
   | `PA_SSH_HOST` | `ssh.pythonanywhere.com` (of `ssh.eu.pythonanywhere.com` bij een EU-account) |
   | `PA_API_HOST` | `www.pythonanywhere.com` (of `eu.pythonanywhere.com`) |
   | `PA_USERNAME` | je PythonAnywhere-gebruikersnaam |
   | `PA_SSH_PRIVATE_KEY` | de **private** sleutel (`github_actions_key`, hele bestand inclusief `-----BEGIN...-----`-regels) |
   | `PA_API_TOKEN` | het API-token uit stap 2 |

Vanaf dat moment: elke `git push` naar `main` haalt de code automatisch op de server op, installeert nieuwe dependencies, en herlaadt de webapp — zonder dat je zelf hoeft in te loggen.

## 7. Env-variabelen, samengevat


Allemaal in te vullen in `wsgi_pythonanywhere.py` (voor de webapp) — de Always-on task en de Bash-console lezen dezelfde variabelen als je ze ook in je `~/.bashrc` zet:

| Variabele | Waarvoor |
|---|---|
| `DATABASE_URL` | connectiestring naar je Postgres |
| `SESSION_SECRET` | ondertekenen van sessietokens (auth.py) |
| `FLASK_SECRET_KEY` | Flask session-cookie |
| `API_FOOTBALL_KEY` | ajax_sync.py |
| `RESEND_API_KEY`, `EMAIL_FROM`, `APP_BASE_URL` | reminders.py |

