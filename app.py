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

import io
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, abort, flash, redirect, render_template, request, send_file, session, url_for

import auth
import backup_database
import db
import export
import github_sync
import mail
import nieuws_sync
import queries
import reminder_export
from scoring import bereken_punten

db.run_migrations()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.environ.get("SESSION_SECRET", "dev-only-niet-in-productie"))

# Zelfde soort harde weigering als bij SESSION_SECRET (zie auth.py) -- deze
# sleutel ondertekent Flask's eigen sessiecookie, een aparte laag rond het
# zelfgebouwde token. Ook hier: een bekende/lege waarde is een kritiek
# beveiligingslek, dus de app start hier bewust niet mee op.
_ONVEILIGE_SECRET_KEYS = {
    "",
    "dev-only-niet-in-productie",
    "kies-hier-een-andere-lange-willekeurige-string",
    "vervang-dit-door-een-lange-willekeurige-string",
}
if app.secret_key in _ONVEILIGE_SECRET_KEYS:
    raise RuntimeError(
        "FLASK_SECRET_KEY (en/of SESSION_SECRET) is niet goed ingesteld — de app start "
        "hierdoor bewust niet op, want dit is een kritiek beveiligingsprobleem. Genereer "
        'een echte, willekeurige sleutel: python3 -c "import secrets; print(secrets.token_hex(32))" '
        "en zet die als FLASK_SECRET_KEY in je WSGI-configuratiebestand (gebruik een ANDERE "
        "waarde dan je SESSION_SECRET)."
    )
AMSTERDAM = ZoneInfo("Europe/Amsterdam")

# ---------------- Nederlandse datumopmaak ----------------
# Bewust NIET via locale.setlocale(): die vereist dat de server een
# nl_NL-taalpakket heeft geïnstalleerd, wat op gedeelde hosting (zoals
# PythonAnywhere) niet gegarandeerd is. Deze aanpak werkt altijd.

_DAGEN_KORT = ["ma", "di", "wo", "do", "vr", "za", "zo"]
_DAGEN_VOL = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
_MAANDEN_KORT = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
_MAANDEN_VOL = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
                "augustus", "september", "oktober", "november", "december"]


def nl_datum_kort(dt):
    """bv. 'zo 09 aug · 16:30' — voor wedstrijdenlijsten en widgets."""
    if not dt:
        return ""
    return f"{_DAGEN_KORT[dt.weekday()]} {dt.day:02d} {_MAANDEN_KORT[dt.month - 1]} · {dt.strftime('%H:%M')}"


def nl_datum_lang(dt):
    """bv. 'zondag 9 augustus 2026 · 16:30' — voor de wedstrijd-detailpagina."""
    if not dt:
        return ""
    return f"{_DAGEN_VOL[dt.weekday()]} {dt.day} {_MAANDEN_VOL[dt.month - 1]} {dt.year} · {dt.strftime('%H:%M')}"


app.jinja_env.filters["nl_kort"] = nl_datum_kort
app.jinja_env.filters["nl_lang"] = nl_datum_lang


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
    seizoen = queries.get_active_season()
    reminder_export.sync_if_needed(seizoen)
    backup_database.sync_if_needed()
    return {
        "huidige_gebruiker": current_user(),
        "seizoen": seizoen,
        "registratie_gesloten": queries.registratie_gesloten(seizoen),
        "nieuws": nieuws_sync.get_nieuws(),
    }


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
    if queries.registratie_gesloten(queries.get_active_season()):
        return render_template("registreren_gesloten.html")
    if request.method == "POST":
        try:
            auth.register_participant(request.form["naam"], request.form["email"], request.form["wachtwoord"])
            flash("Aanvraag verstuurd. Je krijgt toegang zodra de beheerder je account goedkeurt.", "info")
            return redirect(url_for("login"))
        except auth.ValidationError as e:
            flash(str(e), "fout")
    return render_template("registreren.html")


@app.route("/wachtwoord-vergeten", methods=["GET", "POST"])
def wachtwoord_vergeten():
    if request.method == "POST":
        email = request.form.get("email", "")
        token = auth.request_password_reset(email)
        if token:
            reset_link = url_for("wachtwoord_resetten", token=token, _external=True)
            deelnemer = auth.find_participant_by_email(email)
            try:
                subject, html = mail.wachtwoord_reset_email(deelnemer["naam"], reset_link)
                mail.send_email(deelnemer["email"], subject, html)
            except Exception as e:
                # Nooit laten merken of het versturen lukte — dat zou alsnog
                # verraden of het e-mailadres bestaat. Wel loggen voor onszelf.
                print(f"[wachtwoord_vergeten] versturen mislukt (mogelijk gratis-plan-beperking): {e}")
        # Altijd exact dezelfde melding, ongeacht of het e-mailadres bekend was
        # of het versturen lukte — anders is dit zelf een user-enumeration-lek.
        flash("Als dit e-mailadres bij ons bekend is, ontvang je een e-mail met een link om je wachtwoord opnieuw in te stellen.", "info")
        return redirect(url_for("login"))
    return render_template("wachtwoord_vergeten.html")


@app.route("/wachtwoord-resetten/<token>", methods=["GET", "POST"])
def wachtwoord_resetten(token):
    if request.method == "POST":
        try:
            auth.reset_password_with_token(token, request.form.get("wachtwoord", ""))
            flash("Je wachtwoord is gewijzigd. Je kunt nu inloggen.", "info")
            return redirect(url_for("login"))
        except auth.ValidationError as e:
            flash(str(e), "fout")
    return render_template("wachtwoord_resetten.html", token=token)


@app.route("/uitschrijven/<token>")
def uitschrijven(token):
    deelnemer = queries.uitschrijven_reminders(token)
    if deelnemer:
        reminder_export.sync_if_needed(queries.get_active_season(), force=True)
    return render_template("uitschrijven.html", gelukt=deelnemer is not None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- Deelnemer-routes ----------------

def categoriseer_wedstrijden(matches: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Verdeelt de wedstrijden van het seizoen in drie categorieën:
    - actuele: de netgespeelde wedstrijd (indien <24u na aftrap) bovenaan,
      gevolgd door de eerstvolgende nog te spelen (of lopende) wedstrijd,
      plus eventuele wedstrijden waarvan de sync is stopgezet maar die nog
      geen uitslag hebben (bv. een gestaakte wedstrijd — zie
      queries.stop_sync_wedstrijd) — die blijven hier staan totdat de
      beheerder de uitslag alsnog handmatig verwerkt, ongeacht hun plek in
      de normale chronologische volgorde.
    - gepland: alle overige, nog niet gespeelde wedstrijden.
    - eerder_gespeeld: alle afgelopen wedstrijden die niet (meer) in
      'actuele' staan, nieuwste eerst.
    """
    nu = datetime.now(timezone.utc)
    niet_afgelopen = sorted((m for m in matches if m["status"] != "afgelopen"), key=lambda m: m["kickoff"])
    afgelopen = sorted((m for m in matches if m["status"] == "afgelopen"), key=lambda m: m["kickoff"], reverse=True)

    eerstvolgende = niet_afgelopen[0] if niet_afgelopen else None
    net_gespeeld = None
    if afgelopen and (nu - afgelopen[0]["kickoff"]) <= timedelta(hours=24):
        net_gespeeld = afgelopen[0]

    actuele = [m for m in (net_gespeeld, eerstvolgende) if m is not None]

    # Gestaakte/bevroren wedstrijden die op verwerking wachten: altijd bij
    # Actuele tonen, ook als ze niet de eerstvolgende zijn.
    wacht_op_verwerking = [
        m for m in niet_afgelopen
        if m.get("handmatig_overschreven") and m["id"] not in {x["id"] for x in actuele}
    ]
    actuele.extend(wacht_op_verwerking)

    actuele_ids = {m["id"] for m in actuele}
    gepland = [m for m in niet_afgelopen if m["id"] not in actuele_ids]
    eerder_gespeeld = [m for m in afgelopen if m["id"] not in actuele_ids]

    return actuele, gepland, eerder_gespeeld


@app.route("/")
@login_required
def programma():
    github_sync.sync_if_needed()
    gebruiker = current_user()
    seizoen = queries.get_active_season()
    matches = queries.get_matches(seizoen)
    actuele, gepland, eerder_gespeeld = categoriseer_wedstrijden(matches)
    return render_template(
        "programma.html",
        actuele=actuele,
        gepland=gepland,
        eerder_gespeeld=eerder_gespeeld,
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
        match["status"] != "gepland"
        or datetime.now(timezone.utc) >= match["kickoff"]
    )

    return render_template(
        "wedstrijd.html",
        match=match,
        kan_nog_voorspellen=kan_nog_voorspellen,
        mijn_voorspelling=queries.get_my_prediction(match_id, gebruiker["id"]),
        mag_voorspellingen_zien=mag_voorspellingen_zien,
        voorspellingen=queries.get_predictions_for_match(match_id) if mag_voorspellingen_zien else [],
        voorspelling_status=(
            queries.get_voorspelling_status(match_id)
            if gebruiker["rol"] == "beheerder" and kan_nog_voorspellen
            else None
        ),
    )


@app.route("/klassement")
@login_required
def klassement():
    seizoen = queries.get_active_season()
    return render_template("klassement.html", klassement=queries.get_klassement_met_delta(seizoen))


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


@app.route("/statistieken")
@login_required
def statistieken():
    gebruiker = current_user()
    seizoen = queries.get_active_season()
    return render_template("statistieken.html", stats=queries.get_mijn_statistieken(gebruiker["id"], seizoen))


@app.route("/spelregels")
@login_required
def spelregels():
    seizoen = queries.get_active_season()
    return render_template(
        "spelregels.html",
        rules=queries.get_rules(seizoen),
        registratie_sluit_na_wedstrijd=queries.get_registratie_sluit_na_wedstrijd(),
        seizoenspunten_per_onderdeel=queries.SEIZOENSPUNTEN_PER_ONDERDEEL,
    )


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
        registratie_sluit_na_wedstrijd=queries.get_registratie_sluit_na_wedstrijd(),
        reset_link_reveal=session.pop("reset_link_reveal", None),
        season_result=queries.get_season_result(seizoen),
        mogelijke_duplicaten=queries.vind_mogelijke_duplicaten(seizoen),
    )


@app.route("/beheerder/seizoensuitkomst", methods=["POST"])
@admin_required
def beheerder_seizoensuitkomst():
    checkpoint = request.form.get("checkpoint")
    if checkpoint not in ("na17", "na34"):
        abort(400)
    try:
        positie = int(request.form["positie"])
        punten = int(request.form["punten"])
        seizoen = queries.get_active_season()
        queries.set_season_result(seizoen, checkpoint, positie, punten)
        flash(f"Seizoensuitkomst {checkpoint} opgeslagen, punten van alle deelnemers doorgerekend.", "info")
    except (KeyError, ValueError) as e:
        flash(f"Ongeldige invoer: {e}", "fout")
    return redirect(url_for("beheerder"))


@app.route("/beheerder/wedstrijd-toevoegen", methods=["POST"])
@admin_required
def beheerder_wedstrijd_toevoegen():
    seizoen = queries.get_active_season()
    try:
        kickoff = datetime.fromisoformat(request.form["kickoff"]).replace(tzinfo=AMSTERDAM).astimezone(timezone.utc).isoformat()
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


@app.route("/beheerder/sync-stopzetten/<match_id>", methods=["POST"])
@admin_required
def beheerder_sync_stopzetten(match_id):
    match_id = int(match_id)
    queries.stop_sync_wedstrijd(match_id)
    flash("Automatische sync stopgezet voor deze wedstrijd. Vul de uitslag hieronder handmatig in zodra bekend.", "info")
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


@app.route("/beheerder/registratie-instelling", methods=["POST"])
@admin_required
def beheerder_registratie_instelling():
    try:
        n = int(request.form["registratie_sluit_na_wedstrijd"])
        if n < 1:
            raise ValueError("moet minimaal 1 zijn")
        queries.set_registratie_sluit_na_wedstrijd(n)
        flash("Registratie-instelling opgeslagen.", "info")
    except (KeyError, ValueError) as e:
        flash(f"Ongeldige waarde: {e}", "fout")
    return redirect(url_for("beheerder"))


@app.route("/beheerder/reset-link", methods=["POST"])
@admin_required
def beheerder_reset_link():
    email = request.form.get("email", "").strip()
    token = auth.request_password_reset(email)
    if not token:
        flash(f"Geen account gevonden met e-mailadres {email}.", "fout")
        return redirect(url_for("beheerder"))
    reset_link = url_for("wachtwoord_resetten", token=token, _external=True)
    # Eenmalig tonen via de session (niet permanent bewaard) -- zo kan de link
    # in een kopieerbaar veld getoond worden i.p.v. in een vluchtige flash-tekst.
    session["reset_link_reveal"] = {"email": email, "link": reset_link}
    return redirect(url_for("beheerder"))


@app.route("/beheerder/export-excel")
@admin_required
def beheerder_export_excel():
    seizoen = queries.get_active_season()
    data = export.bouw_export(seizoen)
    bestandsnaam = f"jpoule-puntenopbouw-{seizoen.replace('/', '-')}.xlsx"
    return send_file(
        io.BytesIO(data),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=bestandsnaam,
    )


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
    if besluit == "geweigerd":
        # Geen data van geweigerde aanmeldingen bewaren: direct volledig verwijderen
        # in plaats van alleen de status te wijzigen.
        queries.delete_participant(participant_id)
    else:
        queries.set_participant_status(participant_id, besluit)
        reminder_export.sync_if_needed(queries.get_active_season(), force=True)
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
