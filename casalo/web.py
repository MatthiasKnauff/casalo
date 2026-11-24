"""Gemeinsame Bausteine der Weboberfläche: Blueprint, Anmeldung, CSRF, Formular-Helfer."""
import datetime
import secrets
from functools import wraps

from flask import Blueprint, abort, flash, redirect, request, session

from . import db, rechnen

bp = Blueprint("web", __name__)

OFFEN = ("/anmelden", "/health", "/static/", "/portal")     # /portal: eigene Anmeldung und CSRF-Prüfung (v_portal)


def aktueller_benutzer():
    uid = session.get("uid")
    if not uid:
        return None
    return db.q("SELECT * FROM benutzer WHERE id = ?", (uid,), one=True)


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


@bp.before_app_request
def schutz():
    p = request.path
    if p.startswith("/api/telefon/"):
        return None                                   # eigene Token-Prüfung
    if any(p.startswith(o) for o in OFFEN):
        return None
    if not aktueller_benutzer():
        return redirect(f"/anmelden?next={p}")
    if request.method == "POST":
        tok = request.form.get("_csrf") or request.headers.get("X-CSRF")
        if not tok or not secrets.compare_digest(tok, session.get("csrf", "")):
            abort(400, "Sitzung abgelaufen – bitte Seite neu laden.")
    return None


def nur_admin(f):
    @wraps(f)
    def w(*a, **kw):
        u = aktueller_benutzer()
        if not u or u["rolle"] != "admin":
            abort(403)
        return f(*a, **kw)
    return w


def zurueck(standard="/"):
    ziel = request.form.get("_zurueck") or request.referrer or standard
    return redirect(ziel if ziel.startswith("/") or ziel.startswith(request.host_url) else standard)


def f(name, standard=""):
    return (request.form.get(name, standard) or "").strip()


def f_betrag(name):
    try:
        return rechnen.parse_betrag(request.form.get(name, ""))
    except ValueError as e:
        flash(str(e), "fehler")
        raise


def f_zahl(name):
    return rechnen.parse_zahl(request.form.get(name, ""))


def f_int(name, standard=0):
    try:
        return int(request.form.get(name, standard))
    except (TypeError, ValueError):
        return standard


def heute():
    return datetime.date.today()


def gemeinschaft_oder_404(gid):
    g = db.q("SELECT * FROM gemeinschaft WHERE id = ?", (gid,), one=True)
    if not g:
        abort(404)
    return g
