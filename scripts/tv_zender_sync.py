"""
scripts/tv_zender_sync.py
------------------------------------------------------------------
Haalt op welke tv-zender aankomende Ajax-wedstrijden uitzendt, via
sport-tv-gids.nl/team/ajax EN aanvullend via de zeven dagpagina's
(sport-tv-gids.nl/kijk-live-sport/zat, zon, maa, din, woe, don, vri),
en schrijft dit naar data/tv_zenders.json. De teampagina meldt een
wedstrijd soms nog als "Tv-gids niet beschikbaar" terwijl de zender op
de dagpagina al wel staat.
Draait in GitHub Actions (waar wél internettoegang is), zie
.github/workflows/tv-zender-sync.yml.

Neemt zowel de zendernaam als de logo-URL mee (de teampagina gebruikt
de grotere logo's, niet de '-sm-'-varianten die elders op de site
staan, dus dit is meteen de betere resolutie).

LET OP -- dit is webscraping, geen officiële API: sport-tv-gids.nl kan
op elk moment zijn opmaak wijzigen, waardoor dit script niets meer
vindt. Daarom bewust defensief opgezet:
  - Herkent de pagina op zichtbare TEKST (dag/maand-patronen) in
    plaats van op interne CSS-klassen, die sneller wijzigen dan de
    zichtbare tekst.
  - Bij twijfel of een match niet gevonden wordt: sla die wedstrijd
    gewoon over (leeg blijft leeg) in plaats van te gokken.
  - Een volledig mislukte poging (bv. de site is onbereikbaar, of de
    opmaak is compleet gewijzigd) commit gewoon niets nieuws -- de
    vorige, laatst bekende data blijft dan gewoon staan.
------------------------------------------------------------------
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TEAM_PAGINA = "https://sport-tv-gids.nl/team/ajax"
OUTPUT_PATH = "data/tv_zenders.json"

_MAANDEN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # Nederlandse schrijfwijzen die afwijken van de Engelse (de overige
    # maanden zijn in beide talen hetzelfde). Zonder deze werd een datum
    # als "10. Okt" niet als nieuwe wedstrijd herkend: die wedstrijd
    # verdween, en de zender ervan lekte door naar de vorige wedstrijd.
    "okt": 10, "mrt": 3, "maa": 3, "mei": 5,
}
_DATUM_PATROON = re.compile(
    r"(\d{1,2})\.\s*(" + "|".join(_MAANDEN.keys()) + r")", re.IGNORECASE
)

# sport-tv-gids.nl gebruikt op sommige plekken nog een verouderde zendernaam
# die niet meer overeenkomt met de huidige indeling. Handmatig bevestigde
# correcties (naam + logo), toegepast ná het scrapen, vóórdat de data wordt
# opgeslagen. "Ziggo Voetbal" was de oude naam van het huidige "Ziggo Sport 2",
# maar de bron koppelt dat label nog aan uitzendingen die in werkelijkheid op
# "Ziggo Sport 1" te zien zijn -- vandaar deze correctie op naam én logo.
# Zenders die de bron soms meeneemt, maar die voor dit doel niet relevant
# zijn -- bv. buitenlandse zendgemachtigden zonder Nederlandse uitzending.
# Worden volledig genegeerd, ongeacht wat er verder op de pagina staat.
_ZENDER_UITSLUITINGEN = {
    "DAZN",
    "Ziggo Sport 14",
}


def _is_uitgesloten(naam: str) -> bool:
    """Exacte vergelijking (dus 'Ziggo Sport 1' valt níet onder 'Ziggo Sport 14'),
    maar ongevoelig voor hoofdletters en omringende spaties."""
    return naam.strip().lower() in {n.lower() for n in _ZENDER_UITSLUITINGEN}

_ZENDER_CORRECTIES = {
    "Ziggo Voetbal": {
        "naam": "Ziggo Sport 1",
        "logo": "/static/zenders/ziggo-sport-1.jpg",
    },
    # "Ziggo Sport" zonder nummer is, net als "Ziggo Voetbal", een oudere/
    # alternatieve aanduiding voor hetzelfde kanaal.
    "Ziggo Sport": {
        "naam": "Ziggo Sport 1",
        "logo": "/static/zenders/ziggo-sport-1.jpg",
    },
    # Ook als de bron het label zelf al correct "Ziggo Sport 1" noemt, toch
    # ons eigen, geverifieerd actuele logo gebruiken -- het bijbehorende
    # logo-bestand bij de bron bleek namelijk ook verouderd te zijn.
    "Ziggo Sport 1": {
        "naam": "Ziggo Sport 1",
        "logo": "/static/zenders/ziggo-sport-1.jpg",
    },
    "Ziggo Sport 2": {
        "naam": "Ziggo Sport 2",
        "logo": "/static/zenders/ziggo-sport-2.jpg",
    },
    "Ziggo Sport 3": {
        "naam": "Ziggo Sport 3",
        "logo": "/static/zenders/ziggo-sport-3.jpg",
    },
}


def _is_zenderlogo(img) -> bool:
    """Onderscheidt een zenderlogo van een teamlogo/sport-icoon (die ook als
    <img> op de pagina staan) -- zenderlogo's staan in de map 'sportzender'."""
    src = img.get("src") or ""
    return "/sportzender/" in src and img.get("title")


def _sluit_af(huidige: dict | None, resultaat: list[dict]) -> None:
    """Voegt een gescrapete wedstrijd toe als hij bruikbaar is, en logt in
    BEIDE gevallen wat er is gevonden -- zodat in het workflow-log altijd te
    zien is waarom een wedstrijd (niet) in tv_zenders.json terechtkwam."""
    if not huidige:
        return
    beschrijving = (f"{huidige['dag']}/{huidige['maand']} tegenstander={huidige['tegenstander']!r} "
                    f"teams={huidige['_teams']} zenders={[z['naam'] for z in huidige['zenders']]} "
                    f"uitgesloten_zenders={huidige['_uitgesloten']}")
    if huidige["tegenstander"] and huidige["zenders"]:
        print(f"[tv_zender_sync] gevonden: {beschrijving}")
        resultaat.append(huidige)
    else:
        print(f"[tv_zender_sync] AFGEVALLEN (geen tegenstander of geen herkende zender): {beschrijving} "
              f"niet-herkende plaatjes={huidige['_plaatjes']}")
        print(f"[tv_zender_sync] STRUCTUUR van afgevallen wedstrijd {huidige['dag']}/{huidige['maand']}:")
        for regel in huidige["_structuur"]:
            print(f"    {regel}")


def haal_op() -> list[dict]:
    resp = requests.get(TEAM_PAGINA, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    kop = soup.find(string=re.compile(r"Volgende wedstrijden live op\s*TV", re.IGNORECASE))
    if not kop:
        raise RuntimeError(
            "Kop 'Volgende wedstrijden live op TV' niet gevonden -- de opmaak van "
            "sport-tv-gids.nl is vermoedelijk gewijzigd, de scraper moet worden aangepast."
        )

    kop_element = kop.find_parent()
    if not kop_element:
        raise RuntimeError("Kon geen bovenliggend element bij de kop vinden.")

    # BELANGRIJK (bevestigd via handmatige diagnose op de echte pagina):
    # wedstrijden staan NIET elk in hun eigen kaart/wrapper -- datum, teams
    # en zenderlogo's van ALLE aankomende wedstrijden staan plat naast
    # elkaar in dezelfde grote lijst-container. Daarom reconstrueren we
    # elke wedstrijd door de container in documentvolgorde te doorlopen en
    # alles wat ná een datum komt bij die datum te groeperen, tot de
    # eerstvolgende datum begint.
    container = kop_element
    for _ in range(6):
        if len(_DATUM_PATROON.findall(container.get_text(" "))) >= 2:
            break
        if container.parent is None:
            break
        container = container.parent

    resultaat = []
    huidige: dict | None = None

    for node in container.descendants:
        naam_tag = getattr(node, "name", None)

        if naam_tag in ("h1", "h2", "h3") and "Recente resultaten" in node.get_text():
            break

        if isinstance(node, str):
            tekst = node.strip()
            datum_match = _DATUM_PATROON.search(tekst)
            # Alleen een tekstnode die (nagenoeg) UITSLUITEND de datum is
            # telt als een nieuw wedstrijd-startpunt -- dat voorkomt dat een
            # datum die toevallig ergens middenin een langere zin voorkomt
            # verkeerd als nieuwe wedstrijd wordt gezien.
            if huidige is not None and tekst and not datum_match and len(huidige["_structuur"]) < 60:
                huidige["_structuur"].append(f"tekst:{tekst[:40]!r}")
            if datum_match and len(tekst) <= len(datum_match.group(0)) + 15:
                _sluit_af(huidige, resultaat)
                huidige = {
                    "dag": int(datum_match.group(1)),
                    "maand": _MAANDEN[datum_match.group(2).lower()],
                    "tegenstander": None,
                    "_teams": [],
                    "zenders": [],
                    "_plaatjes": [],
                    "_uitgesloten": [],
                    "_structuur": [],
                }
            continue

        if huidige is None:
            continue

        # Diagnose: leg de structuur tussen deze datum en de volgende vast,
        # zodat bij een afgevallen wedstrijd te zien is waar de zender staat.
        if naam_tag and len(huidige["_structuur"]) < 60:
            attrs = {k: (v if isinstance(v, str) else " ".join(v))[:60]
                     for k, v in (node.attrs or {}).items()
                     if k in ("class", "src", "data-src", "data-lazy-src", "srcset", "alt", "title", "href", "style", "aria-label")}
            huidige["_structuur"].append(f"<{naam_tag} {attrs}>" if attrs else f"<{naam_tag}>")

        if naam_tag in ("h6", "h5", "b", "strong"):
            tekst = node.get_text(strip=True)
            if tekst and tekst.lower() != "ajax" and tekst not in huidige["_teams"]:
                huidige["_teams"].append(tekst)
                huidige["tegenstander"] = huidige["_teams"][0]

        elif naam_tag == "img" and not _is_zenderlogo(node):
            # Voor de diagnose: welke plaatjes zijn NIET als zenderlogo herkend?
            # (Een nieuwe zender met een andere logo-map zou anders ongezien
            # blijven en de hele wedstrijd laten wegvallen.)
            if len(huidige["_plaatjes"]) < 8:
                huidige["_plaatjes"].append(f"{node.get('title')!r} <{node.get('src')}>")

        elif naam_tag == "img" and _is_zenderlogo(node):
            zendernaam = node.get("title")
            if zendernaam and _is_uitgesloten(zendernaam) and zendernaam not in huidige["_uitgesloten"]:
                huidige["_uitgesloten"].append(zendernaam)
            if (
                zendernaam
                and not _is_uitgesloten(zendernaam)
                and zendernaam not in [z["naam"] for z in huidige["zenders"]]
            ):
                src = node.get("src") or ""
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://sport-tv-gids.nl" + src

                if zendernaam in _ZENDER_CORRECTIES:
                    correctie = _ZENDER_CORRECTIES[zendernaam]
                    zendernaam, src = correctie["naam"], correctie["logo"]
                    # Kan na correctie alsnog een duplicaat zijn (bv. als de
                    # juiste naam toevallig al apart in de lijst stond) --
                    # dan niet nogmaals toevoegen.
                    if zendernaam in [z["naam"] for z in huidige["zenders"]]:
                        continue

                huidige["zenders"].append({"naam": zendernaam, "logo": src})

    _sluit_af(huidige, resultaat)

    for w in resultaat:
        del w["_teams"]
        del w["_plaatjes"]
        del w["_uitgesloten"]
        del w["_structuur"]

    return resultaat


# ---------------- Aanvullende bron: de zeven dagpagina's ----------------

DAGPAGINA_BASIS = "https://sport-tv-gids.nl/kijk-live-sport/"
# slug -> weekdagnummer (maandag = 0). Elke dagpagina toont de eerstvolgende
# keer dat die weekdag voorkomt (vandaag meegerekend).
DAGPAGINA_SLUGS = {"maa": 0, "din": 1, "woe": 2, "don": 3, "vri": 4, "zat": 5, "zon": 6}


def _datum_voor_weekdag(slug: str, nu: datetime | None = None):
    vandaag = (nu or datetime.now(ZoneInfo("Europe/Amsterdam"))).date()
    return vandaag + timedelta(days=(DAGPAGINA_SLUGS[slug] - vandaag.weekday()) % 7)


def _is_ajax_team(naam: str) -> bool:
    # Exacte vergelijking: 'Jong Ajax', 'Ajax Vrouwen', 'Ajax O21' enz. horen er niet bij.
    return naam.strip().lower() in {"ajax", "afc ajax"}


def _schone_zendernaam(titel: str | None) -> str:
    """Op de dagpagina's heet een zender in de title 'Live ESPN 2' / 'Live
    Ziggo Sport' -- het voorvoegsel 'Live ' hoort niet bij de zendernaam."""
    return re.sub(r"^\s*live\s+", "", (titel or ""), flags=re.IGNORECASE).strip()


def _zender_uit_img(img) -> dict | None:
    """Zelfde zendernaam-/logoverwerking als op de teampagina (uitsluitingen,
    correcties), maar voor een los <img>-element. None = niet gebruiken."""
    if not _is_zenderlogo(img):
        return None
    naam = _schone_zendernaam(img.get("title"))
    if not naam or _is_uitgesloten(naam):
        return None
    src = img.get("src") or ""
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = "https://sport-tv-gids.nl" + src
    if naam in _ZENDER_CORRECTIES:
        c = _ZENDER_CORRECTIES[naam]
        naam, src = c["naam"], c["logo"]
    return {"naam": naam, "logo": src}


def _structuur_van(element, maxn: int = 40) -> list[str]:
    """Compacte weergave van de opbouw van een element (tags, belangrijkste
    attributen, tekst) voor diagnose in het workflow-log."""
    regels = []
    for node in element.descendants:
        if len(regels) >= maxn:
            regels.append("...")
            break
        if isinstance(node, str):
            tekst = node.strip()
            if tekst:
                regels.append(f"tekst:{tekst[:50]!r}")
            continue
        attrs = {k: (v if isinstance(v, str) else " ".join(v))[:70]
                 for k, v in (node.attrs or {}).items()
                 if k in ("class", "src", "data-src", "alt", "title", "href", "aria-label", "data-team", "data-name")}
        regels.append(f"<{node.name} {attrs}>" if attrs else f"<{node.name}>")
    return regels


_MAAND_NAAR_NUMMER = {
    "jan": 1, "feb": 2, "mrt": 3, "mar": 3, "maa": 3, "apr": 4, "mei": 5, "may": 5,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dec": 12,
}
_DATUM_IN_RIJ = re.compile(r"(\d{1,2})\s*\.?\s*(" + "|".join(_MAAND_NAAR_NUMMER) + r")", re.IGNORECASE)


def _datum_uit_rij(tekst: str, nu: datetime | None = None):
    """Leest de datum uit de tijdregel van een dagpagina-rij: 'Vandaag vanaf
    21:00', 'Morgen vanaf 20:00' of 'Don. 5 Nov vanaf 18:45'. None = onbekend."""
    vandaag = (nu or datetime.now(ZoneInfo("Europe/Amsterdam"))).date()
    laag = tekst.strip().lower()
    if laag.startswith("vandaag"):
        return vandaag
    if laag.startswith("morgen"):
        return vandaag + timedelta(days=1)
    m = _DATUM_IN_RIJ.search(tekst)
    if m:
        try:
            return vandaag.replace(month=_MAAND_NAAR_NUMMER[m.group(2).lower()], day=int(m.group(1)))
        except ValueError:  # bv. 29 februari in een jaar zonder schrikkeldag
            return None
    return None


def _wedstrijd_uit_rij(rij) -> tuple[str, str] | None:
    """Zoekt in de tekst van de rij naar '| Thuis - Uit' en geeft (thuis, uit)."""
    for t in rij.stripped_strings:
        m = re.match(r"^\|?\s*(.+?)\s+-\s+(.+)$", t)
        if m and "vanaf" not in t.lower():
            return m.group(1).strip(), m.group(2).strip()
    return None


def haal_op_dagpagina(slug: str, nu: datetime | None = None) -> list[dict]:
    fallback_datum = _datum_voor_weekdag(slug, nu)
    url = DAGPAGINA_BASIS + slug
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rijen = soup.select("div.list-group-item")
    resultaat = []
    ajax_rijen = 0
    for rij in rijen:
        wedstrijd = _wedstrijd_uit_rij(rij)
        if not wedstrijd or not any(_is_ajax_team(t) for t in wedstrijd):
            continue
        ajax_rijen += 1
        tegenstander = next(t for t in wedstrijd if not _is_ajax_team(t)) \
            if not all(_is_ajax_team(t) for t in wedstrijd) else None
        if not tegenstander:
            continue

        tijdregel = next((t for t in rij.stripped_strings if "vanaf" in t.lower()), "")
        datum = _datum_uit_rij(tijdregel, nu)
        competitie_el = rij.find("i")
        competitie = competitie_el.get_text(strip=True) if competitie_el else "?"

        zenders, uitgesloten = [], []
        for img in rij.find_all("img"):
            if _is_zenderlogo(img) and _is_uitgesloten(_schone_zendernaam(img.get("title"))):
                uitgesloten.append(_schone_zendernaam(img.get("title")))
            z = _zender_uit_img(img)
            if z and z["naam"] not in [x["naam"] for x in zenders]:
                zenders.append(z)

        beschrijving = (f"{datum.day if datum else '?'}/{datum.month if datum else '?'} ({slug}) "
                        f"tijdregel={tijdregel!r} wedstrijd={wedstrijd} competitie={competitie!r} "
                        f"zenders={[z['naam'] for z in zenders]} uitgesloten_zenders={uitgesloten}")
        if not datum:
            print(f"[tv_zender_sync] dagpagina AFGEVALLEN (datum niet te bepalen): {beschrijving}")
        elif not zenders:
            print(f"[tv_zender_sync] dagpagina AFGEVALLEN (geen zender): {beschrijving}")
        else:
            print(f"[tv_zender_sync] dagpagina gevonden: {beschrijving}")
            resultaat.append({"dag": datum.day, "maand": datum.month,
                              "tegenstander": tegenstander, "zenders": zenders})

    if rijen and ajax_rijen == 0:
        met_ajax_tekst = [r for r in rijen if "ajax" in r.get_text(" ").lower()]
        print(f"[tv_zender_sync] dagpagina {slug}: {len(met_ajax_tekst)} rij(en) bevatten 'ajax' maar zijn niet als "
              f"Ajax-wedstrijd herkend.")
        for nr, r in enumerate(met_ajax_tekst[:2], 1):
            print(f"[tv_zender_sync]   STRUCTUUR rij {nr}:")
            for regel in _structuur_van(r):
                print(f"      {regel}")
    elif not rijen:
        print(f"[tv_zender_sync] dagpagina {slug}: GEEN rijen (div.list-group-item) gevonden -- "
              f"opmaak anders dan verwacht? Aantal <img>: {len(soup.find_all('img'))}.")
    print(f"[tv_zender_sync] dagpagina {slug} ({fallback_datum}): {len(rijen)} rijen, {ajax_rijen} Ajax-wedstrijd(en) herkend.")
    return resultaat


def _zelfde_tegenstander(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    return bool(a and b) and (a in b or b in a)


def haal_op_dagpaginas(nu: datetime | None = None) -> list[dict]:
    """Alle zeven dagpagina's; een mislukte pagina wordt gelogd en overgeslagen."""
    alles = []
    for slug in DAGPAGINA_SLUGS:
        try:
            alles.extend(haal_op_dagpagina(slug, nu))
        except Exception as e:
            print(f"[tv_zender_sync] dagpagina {slug} mislukt: {e}")
    return alles


def voeg_samen(basis: list[dict], extra: list[dict]) -> list[dict]:
    """Teampagina heeft voorrang; een wedstrijd van de dagpagina's komt er
    alleen bij als dezelfde dag+maand+tegenstander er nog niet in staat."""
    resultaat = list(basis)
    for e in extra:
        bestaat = any(
            b["dag"] == e["dag"] and b["maand"] == e["maand"]
            and _zelfde_tegenstander(b["tegenstander"], e["tegenstander"])
            for b in resultaat
        )
        if bestaat:
            continue
        print(f"[tv_zender_sync] aangevuld vanaf dagpagina: {e['dag']}/{e['maand']} {e['tegenstander']} "
              f"{[z['naam'] for z in e['zenders']]}")
        resultaat.append(e)
    return resultaat


def bewaar(wedstrijden: list[dict]) -> None:
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"wedstrijden": wedstrijden}, f, indent=2, ensure_ascii=False)
    print(f"[tv_zender_sync] {len(wedstrijden)} wedstrijd(en) met bekende zender weggeschreven.")


def git_commit_en_push() -> bool:
    def run(cmd):
        return subprocess.run(cmd, capture_output=True, text=True)

    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    run(["git", "add", OUTPUT_PATH])
    diff = run(["git", "diff", "--staged", "--quiet"])
    if diff.returncode == 0:
        print("[tv_zender_sync] Geen wijzigingen -- niets te committen.")
        return True
    run(["git", "commit", "-m", "Tv-zenders bijgewerkt [skip ci]"])
    run(["git", "pull", "--rebase", "--autostash"])
    push = run(["git", "push"])
    if push.returncode != 0:
        print(f"[tv_zender_sync] Push mislukt: {push.stderr.strip()}", file=sys.stderr)
        return False
    return True


if __name__ == "__main__":
    try:
        wedstrijden = voeg_samen(haal_op(), haal_op_dagpaginas())
    except Exception as e:
        print(f"[tv_zender_sync] Ophalen mislukt: {e}", file=sys.stderr)
        sys.exit(1)  # laat de workflow duidelijk rood zien -- niet stil doorlopen

    bewaar(wedstrijden)
    if not git_commit_en_push():
        sys.exit(1)  # zelfde: een mislukte push mag niet als 'succesvol' ogen
