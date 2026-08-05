"""
git_auth.py
------------------------------------------------------------------
Zorgt dat 'git push' vanuit backup_database.py en reminder_export.py
altijd werkt, ongeacht in welk proces (interactieve Bash-console vs.
het webproces van de site) dit draait. De eerder gebruikte aanpak
('git config credential.helper store', met eenmalig interactief
inloggen) hangt af van waar git de opgeslagen inloggegevens zoekt --
en dat bleek in het webproces een andere plek te zijn dan in de
Bash-console, met deze foutmelding tot gevolg:
    fatal: could not read Username for 'https://github.com':
    No such device or address

Deze module lost dat op door de repo-URL zélf, bij elke push, te
voorzien van het token -- dus zonder afhankelijkheid van een ergens
opgeslagen bestand.

Vereist env var: GITHUB_TOKEN (een Personal Access Token met 'repo'-
rechten, aan te maken via GitHub -> Settings -> Developer settings ->
Personal access tokens). Zonder deze variabele verandert er niets
(git gebruikt dan gewoon de bestaande, eventueel al werkende
instelling) -- dus dit is veilig om toe te voegen zonder iets te
breken.
------------------------------------------------------------------
"""

from __future__ import annotations

import os
import subprocess


def zorg_voor_geauthenticeerde_remote(project_dir: str) -> None:
    """Zet de 'origin'-remote om naar een vorm met ingebakken token, zodat
    een volgende 'git push' nooit hoeft te vragen om inloggegevens."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return  # geen token ingesteld -- niets aan te passen, laat de bestaande situatie ongemoeid

    owner = os.environ.get("GITHUB_OWNER", "")
    repo = os.environ.get("GITHUB_REPO", "")
    if not owner or not repo:
        return  # onvoldoende info om de URL op te bouwen

    nieuwe_url = f"https://{token}@github.com/{owner}/{repo}.git"
    subprocess.run(
        ["git", "remote", "set-url", "origin", nieuwe_url],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
