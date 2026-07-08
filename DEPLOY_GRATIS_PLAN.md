# J-Poule op een gratis PythonAnywhere-account

Deze versie gebruikt **SQLite** in plaats van PostgreSQL (geen aparte databaseserver
nodig) en wordt **handmatig** bijgewerkt via de browser-console, zonder SSH.

## Wat werkt, wat niet

| Functie | Werkt op het gratis plan? |
|---|---|
| De website zelf (inloggen, voorspellen, klassement, beheerscherm) | ✅ Ja |
| Database (SQLite) | ✅ Ja |
| Programma/uitslagen automatisch bijgewerkt | ✅ Ja — via GitHub Actions + `raw.githubusercontent.com` (zie stap 4) |
| Automatisch bijwerken van de website-code via GitHub Actions | ❌ Nee — geen SSH op gratis plan (wel via `git pull` in de console, zie stap 6) |
| E-mailherinneringen (`reminders.py`) | ❌ Nee — `resend.com` staat niet op de gratis whitelist |

Kortom: dankzij de GitHub-omweg werkt vrijwel alles al op het gratis plan — alleen
automatisch deployen van code-wijzigingen en e-mailherinneringen vereisen nog een
upgrade (zie stap 7).

## 1. Code op de server zetten

Consoles-tab → **Bash** (gewoon een browser-console, geen SSH nodig):
```bash
git clone <jouw-git-repository-url> ~/j-poule-web
cd ~/j-poule-web
mkvirtualenv --python=python3.12 jpoule
pip install -r requirements.txt
```
Lukt `git clone` niet (whitelist-foutmelding)? Gebruik dan de **Files-tab** om de
bestanden handmatig te uploaden — GitHub zelf staat meestal wel op de whitelist,
maar dit verschilt soms per account; probeer het gewoon.

## 2. Database opzetten

Zelfde Bash-console:
```bash
cd ~/j-poule-web
python3 -c "from db import init_db; init_db()"
```
Dit maakt `jpoule.db` aan met alle tabellen (zie `schema_sqlite.sql`).

Maak daarna de eerste beheerder aan:
```bash
ADMIN_NAAM="Jouw naam" ADMIN_EMAIL="jij@voorbeeld.nl" ADMIN_WACHTWOORD="EenSterkWachtwoord123" \
    python3 bootstrap_admin.py
```

## 3. De webapp aanmaken

- Web-tab → "Add a new web app" → **Manual configuration** → Python 3.12.
- **Virtualenv**: `/home/<jouwgebruikersnaam>/.virtualenvs/jpoule`
- **WSGI configuration file**: inhoud vervangen door die uit `wsgi_pythonanywhere.py`
  (met je eigen gebruikersnaam ingevuld — de `DB_PATH` hoeft niet aangepast,
  die wijst automatisch naar `jpoule.db` in je projectmap).
- **Static files**: URL `/static/` → Directory `/home/<jouwgebruikersnaam>/j-poule-web/static`
- Klik **Reload**.

Je site draait nu op `https://<jouwgebruikersnaam>.pythonanywhere.com`.

## 4. Programma en uitslagen: automatisch via GitHub

Sinds de toevoeging van `scripts/ajax_data_sync.py` (draait in GitHub Actions,
zie `.github/workflows/ajax-data-sync.yml`) hoef je dit niet meer handmatig te doen.
De website haalt bij elk bezoek aan het programma of een wedstrijdpagina (met een
minimum van 1×/minuut, zie `GITHUB_SYNC_MIN_INTERVAL_S`) de actuele tabel op via
`raw.githubusercontent.com` — dat domein staat wél op de gratis whitelist.

Zet in `wsgi_pythonanywhere.py` je eigen `GITHUB_OWNER`/`GITHUB_REPO`/`GITHUB_BRANCH`,
en in je GitHub-repository de secret `API_FOOTBALL_KEY`. Verder is er niets te doen —
de beheerder heeft in het beheerscherm ook een "Nu handmatig verversen"-knop voor
als je niet op de volgende paginaweergave wilt wachten.

Wil je toch liever alles zelf blijven invoeren (bijvoorbeeld voor oefenwedstrijden,
die `ajax_data_sync.py` bewust overslaat): dat kan gewoon naast elkaar, zie stap 5.

## 5. Handmatig wedstrijden en uitslagen invoeren (optioneel, bv. voor oefenwedstrijden)
```python
from db import get_connection
with get_connection() as conn:
    conn.execute("""
        INSERT INTO matches (id, seizoen, competitie, ronde, thuis, uit, kickoff, status)
        VALUES (1, '2026/2027', 'Eredivisie', 'Regular Season - 1',
                'PEC Zwolle', 'Ajax', '2026-08-09T14:30:00+00:00', 'gepland')
    """)
```
En na afloop, om de uitslag in te vullen én punten te laten uitrekenen:
```python
from db import get_connection
from scoring import bereken_punten
import queries

match_id = 1
with get_connection() as conn:
    conn.execute("""
        UPDATE matches SET status='afgelopen',
            uitslag_rust_thuis=0, uitslag_rust_uit=1,
            uitslag_eind_thuis=1, uitslag_eind_uit=3
        WHERE id = ?
    """, (match_id,))

match = queries.get_match(match_id)
rules = queries.get_rules(queries.get_active_season())
uitslag = {"rust": {"thuis": match["uitslag_rust_thuis"], "uit": match["uitslag_rust_uit"]},
           "eind": {"thuis": match["uitslag_eind_thuis"], "uit": match["uitslag_eind_uit"]}}

with get_connection() as conn:
    for rij in conn.execute("SELECT * FROM predictions WHERE match_id = ?", (match_id,)):
        voorspelling = {"rust": {"thuis": rij["rust_thuis"], "uit": rij["rust_uit"]},
                         "eind": {"thuis": rij["eind_thuis"], "uit": rij["eind_uit"]},
                         "joker": bool(rij["joker"])}
        punten, _ = bereken_punten(voorspelling, uitslag, rules)
        conn.execute("UPDATE predictions SET punten = ? WHERE id = ?", (punten, rij["id"]))
```
Dit kan ook als los `.py`-bestand dat je telkens aanpast en draait — of, als je wilt,
bouw ik dit om tot een extra beheerderscherm in de website zelf (formulier i.p.v. code).

## 6. De site bijwerken na wijzigingen

Geen SSH, dus geen automatische GitHub Actions-deploy. Wel eenvoudig handmatig,
in dezelfde Bash-console:
```bash
cd ~/j-poule-web && git pull
```
Daarna: Web-tab → **Reload**-knop. Dat is alles.

## 7. Later alsnog upgraden?

Als je ooit naar het Hacker-plan gaat, zijn er maar twee dingen die dan verandert
worden om weer de volledige PostgreSQL/live-sync/GitHub-Actions-opzet te gebruiken
die we eerder hebben gebouwd:
1. `db.py` terugzetten naar de PostgreSQL-versie (met `psycopg`/`DATABASE_URL`).
2. De SSH-secrets instellen zoals eerder beschreven, en `deploy.yml` weer actief laten.

Ik heb beide versies klaarstaan, dus dat is dan een kwestie van bestanden terugwisselen.

