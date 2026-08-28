"""
scripts/wedstrijden_batch_toevoegen.py
------------------------------------------------------------------
Voegt in één keer meerdere handmatige wedstrijden toe (Champions/Europa/
Conference League, KNVB Beker, etc.), op basis van een tekstbestand met
één wedstrijd per regel. Hergebruikt exact dezelfde tijdzone-omzetting en
opslaglogica als het "Wedstrijd toevoegen"-formulier in het beheerscherm
(app.py -> beheerder_wedstrijd_toevoegen), dus geen apart, ongeteste pad.

Gebruik:
    python3 wedstrijden_batch_toevoegen.py wedstrijden.txt

Invoerformaat -- één wedstrijd per regel, velden gescheiden door " | ":
    competitie | ronde | thuisploeg | uitploeg | kickoff (Amsterdamse tijd, JJJJ-MM-DDTUU:MM)

Voorbeeld:
    Conference League | Groepsfase, speelronde 1 | AFC Ajax | FC Sion | 2026-09-16T21:00
    Europa League | Achtste finale, heenwedstrijd | AS Roma | AFC Ajax | 2026-10-02T18:45
    KNVB Beker | Tweede ronde | FC Emmen | AFC Ajax | 2026-11-01T20:00

Lege regels en regels die met # beginnen worden overgeslagen.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import queries

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def parseer_regel(regel: str, regelnummer: int) -> dict:
    delen = [d.strip() for d in regel.split("|")]
    if len(delen) != 5:
        raise ValueError(
            f"regel {regelnummer}: verwacht 5 velden (competitie | ronde | thuis | uit | kickoff), "
            f"kreeg er {len(delen)}: {regel!r}"
        )
    competitie, ronde, thuis, uit, kickoff_lokaal = delen
    if not competitie or not thuis or not uit or not kickoff_lokaal:
        raise ValueError(f"regel {regelnummer}: competitie, thuis, uit en kickoff mogen niet leeg zijn")

    try:
        kickoff_utc = (
            datetime.fromisoformat(kickoff_lokaal)
            .replace(tzinfo=AMSTERDAM)
            .astimezone(timezone.utc)
            .isoformat()
        )
    except ValueError as e:
        raise ValueError(
            f"regel {regelnummer}: kickoff {kickoff_lokaal!r} is geen geldige datum/tijd "
            f"(verwacht JJJJ-MM-DDTUU:MM, bv. 2026-09-16T21:00) -- {e}"
        )

    return {
        "competitie": competitie,
        "ronde": ronde,
        "thuis": thuis,
        "uit": uit,
        "kickoff_iso": kickoff_utc,
        "kickoff_weergave": f"{kickoff_lokaal} (Amsterdamse tijd)",
    }


def main() -> None:
    if len(sys.argv) != 2:
        print("Gebruik: python3 wedstrijden_batch_toevoegen.py <bestand.txt>")
        sys.exit(1)

    pad = sys.argv[1]
    with open(pad, encoding="utf-8") as f:
        regels = f.readlines()

    seizoen = queries.get_active_season()
    print(f"Actief seizoen: {seizoen}\n")

    te_verwerken = []
    fouten = []
    for i, regel in enumerate(regels, start=1):
        regel = regel.strip()
        if not regel or regel.startswith("#"):
            continue
        try:
            te_verwerken.append(parseer_regel(regel, i))
        except ValueError as e:
            fouten.append(str(e))

    if fouten:
        print("FOUTEN gevonden -- er is nog NIETS toegevoegd, eerst het bestand corrigeren:")
        for fout in fouten:
            print(f"  - {fout}")
        sys.exit(1)

    if not te_verwerken:
        print("Geen enkele geldige wedstrijdregel gevonden in het bestand.")
        sys.exit(0)

    print(f"{len(te_verwerken)} wedstrijd(en) gevonden, wordt toegevoegd:\n")
    for w in te_verwerken:
        queries.add_manual_match(
            seizoen=seizoen,
            competitie=w["competitie"],
            ronde=w["ronde"],
            thuis=w["thuis"],
            uit=w["uit"],
            kickoff_iso=w["kickoff_iso"],
            oefenwedstrijd=False,
        )
        print(f"  + {w['thuis']} - {w['uit']} ({w['competitie']}, {w['ronde']}) -- aftrap {w['kickoff_weergave']}")

    print(f"\nKlaar: {len(te_verwerken)} wedstrijd(en) toegevoegd.")


if __name__ == "__main__":
    main()
