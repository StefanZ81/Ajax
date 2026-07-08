"""
scoring.py
------------------------------------------------------------------
Puntenlogica, gedeeld tussen de backend (ajax_sync.py, na afloop van
een wedstrijd) en de front-end/app-laag. Identiek aan bereken_punten()
uit app.py — hou die twee synchroon als de spelregels ooit veranderen,
of laat app.py voortaan ook uit deze module importeren.
------------------------------------------------------------------
"""

from __future__ import annotations


def bereken_punten(voorspelling: dict | None, uitslag: dict, rules: dict) -> tuple[int, list[str]]:
    if not voorspelling or not uitslag or not uitslag.get("eind"):
        return 0, []

    detail = []
    punten = 0

    juiste_rust = (
        voorspelling.get("rust")
        and voorspelling["rust"]["thuis"] == uitslag["rust"]["thuis"]
        and voorspelling["rust"]["uit"] == uitslag["rust"]["uit"]
    )
    juiste_eind = (
        voorspelling["eind"]["thuis"] == uitslag["eind"]["thuis"]
        and voorspelling["eind"]["uit"] == uitslag["eind"]["uit"]
    )

    def uitkomst(s):
        if s["thuis"] > s["uit"]:
            return "thuis"
        if s["thuis"] < s["uit"]:
            return "uit"
        return "gelijk"

    juiste_uitkomst = uitkomst(voorspelling["eind"]) == uitkomst(uitslag["eind"])

    if juiste_rust:
        punten += rules["halftime_punten"]
        detail.append(f"Juiste ruststand (+{rules['halftime_punten']})")
    if juiste_eind:
        punten += rules["fulltime_punten"]
        detail.append(f"Juiste eindstand (+{rules['fulltime_punten']})")
    elif juiste_uitkomst:
        punten += rules["outcome_punten"]
        detail.append(f"Juiste winnaar/gelijkspel (+{rules['outcome_punten']})")

    if voorspelling.get("joker"):
        punten = punten * rules["joker_vermenigvuldiger"] - rules["joker_kosten"]
        detail.append(f"Joker ingezet (×{rules['joker_vermenigvuldiger']}, −{rules['joker_kosten']})")

    return punten, detail

