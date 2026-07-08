"""
app.py
------------------------------------------------------------------
J-Poule als Flask-website (WSGI) — draait op PythonAnywhere.

Lokaal draaien:
    pip install -r requirements.txt
    export DATABASE_URL=postgresql://...
    export SESSION_SECRET=...
    flask --app app run --debug

Op PythonAnywhere: zie README_PYTHONANYWHERE.md.
------------------------------------------------------------------
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

import auth
import github_sync
import queries
from scoring import bereken_punten

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.environ.get("SESSION_SECRET", "dev-only-niet-in-productie"))


# ---------------- Auth-helpers ----------------

def current_user():
    token = session.get("token")
    if not token:
        return None
    payload = auth.verify_session_token(token)
    if not payload:
        session.clear()
        return None
    return auth.find_participant_by_id(payload["sub"])


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        gebruiker = current_user()
        if not gebruiker or gebruiker["rol"] != "beheerder":
            abort(403)
        return f(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_user():
    return {"huidige_gebruiker": current_user(), "seizoen": queries.get_active_season()}


# ---------------- Auth-routes ----------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            resultaat = auth.login(request.form["email"], request.form["wachtwoord"])
            session["token"] = resultaat["token"]
            return redirect(request.args.get("next") or url_for("programma"))
        except auth.ValidationError as e:
            flash(str(e), "fout")
    return render_template("login.html")


@app.route("/registreren", methods=["GET", "POST"])
def registreren():
    if request.method == "POST":
        try:
            auth.register_participant(request.form["naam"], request.form["email"], request.form["wachtwoord"])
            flash("Aanvraag verstuurd. Je krijgt toegang zodra de beheerder je account goedkeurt.", "info")
            return redirect(url_for("login"))
        except auth.ValidationError as e:
            flash(str(e), "fout")
    return render_template("registreren.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- Deelnemer-routes ----------------

@app.route("/")
@login_required
def programma():
    github_sync.sync_if_needed()
    gebruiker = current_user()
    seizoen = queries.get_active_season()
    matches = queries.get_matches(seizoen)
    return render_template(
        "programma.html",
        matches=matches,
        standings=queries.get_standings_widget(seizoen),
        upcoming=[m for m in matches if m["status"] != "afgelopen"][:3],
    )


@app.route("/wedstrijd/<match_id>", methods=["GET", "POST"])
@login_required
def wedstrijd(match_id):
    match_id = int(match_id)
    github_sync.sync_if_needed()
    gebruiker = current_user()
    match = queries.get_match(match_id)
    if not match:
        abort(404)

    kan_nog_voorspellen = datetime.now(timezone.utc) < match["kickoff"]

    if request.method == "POST" and kan_nog_voorspellen:
        rust = (int(request.form["rust_thuis"]), int(request.form["rust_uit"]))
        eind = (int(request.form["eind_thuis"]), int(request.form["eind_uit"]))
        joker = "joker" in request.form
        queries.upsert_prediction(match_id, gebruiker["id"], rust, eind, joker)
        flash("Voorspelling opgeslagen.", "info")
        return redirect(url_for("wedstrijd", match_id=match_id))

    mag_voorspellingen_zien = (
        gebruiker["rol"] == "beheerder"
        or match["status"] != "gepland"
        or datetime.now(timezone.utc) >= match["kickoff"]
    )

    return render_template(
        "wedstrijd.html",
        match=match,
        kan_nog_voorspellen=kan_nog_voorspellen,
        mijn_voorspelling=queries.get_my_prediction(match_id, gebruiker["id"]),
        mag_voorspellingen_zien=mag_voorspellingen_zien,
        voorspellingen=queries.get_predictions_for_match(match_id) if mag_voorspellingen_zien else [],
    )


@app.route("/klassement")
@login_required
def klassement():
    return render_template("klassement.html", klassement=queries.get_klassement())


@app.route("/seizoen", methods=["GET", "POST"])
@login_required
def seizoen_voorspelling():
    gebruiker = current_user()
    seizoen = queries.get_active_season()
    gesloten = queries.eerste_competitiewedstrijd_gestart(seizoen)

    if request.method == "POST" and not gesloten:
        na17 = (int(request.form["na17_positie"]), int(request.form["na17_punten"]))
        na34 = (int(request.form["na34_positie"]), int(request.form["na34_punten"]))
        queries.upsert_season_prediction(gebruiker["id"], seizoen, na17, na34)
        flash("Seizoensvoorspelling opgeslagen.", "info")
        return redirect(url_for("seizoen_voorspelling"))

    return render_template(
        "seizoen.html",
        gesloten=gesloten,
        mijn_voorspelling=queries.get_season_prediction(gebruiker["id"], seizoen),
    )


@app.route("/profiel")
@login_required
def profiel():
    return render_template("profiel.html")


# ---------------- Beheerder-routes ----------------

@app.route("/beheerder")
@admin_required
def beheerder():
    seizoen = queries.get_active_season()
    return render_template(
        "beheerder.html",
        aanvragen=queries.get_pending_participants(),
        deelnemers=queries.get_all_participants(),
        rules=queries.get_rules(seizoen),
        matches=queries.get_matches(seizoen),
        github_data_url=github_sync.GITHUB_DATA_URL,
    )


@app.route("/beheerder/wedstrijd-toevoegen", methods=["POST"])
@admin_required
def beheerder_wedstrijd_toevoegen():
    seizoen = queries.get_active_season()
    try:
        kickoff = datetime.fromisoformat(request.form["kickoff"]).replace(tzinfo=timezone.utc).isoformat()
        queries.add_manual_match(
            seizoen=seizoen,
            competitie=request.form["competitie"],
            ronde=request.form.get("ronde", "").strip(),
            thuis=request.form["thuis"].strip(),
            uit=request.form["uit"].strip(),
            kickoff_iso=kickoff,
            oefenwedstrijd="oefenwedstrijd" in request.form,
        )
        flash("Wedstrijd toegevoegd.", "info")
    except (KeyError, ValueError) as e:
        flash(f"Kon wedstrijd niet toevoegen: {e}", "fout")
    return redirect(url_for("beheerder"))


@app.route("/beheerder/uitslag/<match_id>", methods=["POST"])
@admin_required
def beheerder_uitslag(match_id):
    try:
        match_id = int(match_id)
        rust = (int(request.form["rust_thuis"]), int(request.form["rust_uit"]))
        eind = (int(request.form["eind_thuis"]), int(request.form["eind_uit"]))
        queries.set_match_result(match_id, rust, eind)
        flash("Uitslag opgeslagen en punten doorgerekend.", "info")
    except (KeyError, ValueError) as e:
        flash(f"Kon uitslag niet opslaan: {e}", "fout")
    return redirect(url_for("beheerder"))


@app.route("/beheerder/sync-nu", methods=["POST"])
@admin_required
def beheerder_sync_nu():
    github_sync.sync_if_needed(force=True)
    flash("Programma/uitslagen ververst vanuit GitHub.", "info")
    return redirect(url_for("beheerder"))


@app.route("/beheerder/aanvraag/<participant_id>/<besluit>", methods=["POST"])
@admin_required
def beheerder_aanvraag(participant_id, besluit):
    if besluit not in ("goedgekeurd", "geweigerd"):
        abort(400)
    queries.set_participant_status(participant_id, besluit)
    return redirect(url_for("beheerder"))


@app.route("/beheerder/promoveer/<participant_id>", methods=["POST"])
@admin_required
def beheerder_promoveer(participant_id):
    token = session["token"]
    payload = auth.verify_session_token(token)
    try:
        auth.promote_to_admin(payload, participant_id)
    except auth.ValidationError as e:
        flash(str(e), "fout")
    return redirect(url_for("beheerder"))


@app.route("/beheerder/degradeer/<participant_id>", methods=["POST"])
@admin_required
def beheerder_degradeer(participant_id):
    token = session["token"]
    payload = auth.verify_session_token(token)
    try:
        auth.demote_to_participant(payload, participant_id)
    except auth.ValidationError as e:
        flash(str(e), "fout")
    return redirect(url_for("beheerder"))


@app.route("/beheerder/regels", methods=["POST"])
@admin_required
def beheerder_regels():
    seizoen = queries.get_active_season()
    rules = {
        "halftime_punten": int(request.form["halftime_punten"]),
        "fulltime_punten": int(request.form["fulltime_punten"]),
        "outcome_punten": int(request.form["outcome_punten"]),
        "joker_kosten": int(request.form["joker_kosten"]),
        "joker_vermenigvuldiger": int(request.form["joker_vermenigvuldiger"]),
    }
    queries.update_rules(seizoen, rules)
    flash("Spelregels opgeslagen.", "info")
    return redirect(url_for("beheerder"))


if __name__ == "__main__":
    app.run(debug=True)
