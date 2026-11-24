"""Casalo – WEG-Verwaltung mit KI-Telefon."""
import datetime
import os

from flask import Flask

from . import db, rechnen

VERSION = os.environ.get("APP_VERSION", "dev")


def create_app(test_config=None):
    app = Flask(__name__)
    if test_config:
        for k, v in test_config.items():
            if k == "DATA_DIR":
                os.environ["DATA_DIR"] = v
            app.config[k] = v
    db.init()
    app.secret_key = db.secret_key()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=14),
                      MAX_CONTENT_LENGTH=20 * 1024 * 1024)
    app.teardown_appcontext(db.close)
    with app.app_context():
        db.telefon_token()                      # Schlüssel-Datei für den Telefon-Container bereitstellen

    from . import web, v_start, v_stamm, v_finanzen, v_versammlung, v_bank, v_telefon, v_komfort, v_assistent, v_forderungen, v_betrieb, v_portal, v_umlauf  # noqa: F401  (Routen)
    app.register_blueprint(web.bp)

    @app.context_processor
    def globale():
        return {
            "VERSION": VERSION,
            "APP_ENV": os.environ.get("APP_ENV", "dev"),
            "csrf": web.csrf_token,
            "benutzer": web.aktueller_benutzer(),
            "verwaltung": db.setting("verwaltung_name"),
            "SCHLUESSEL": rechnen.SCHLUESSEL,
            "heute_jahr": datetime.date.today().year,
            **v_komfort.kontext(),
        }

    @app.template_filter("eur")
    def eur(c):
        return rechnen.fmt(c)

    @app.template_filter("betrag")
    def betrag(c):
        return rechnen.fmt(c, waehrung=False) if c else ""

    @app.template_filter("datum")
    def datum(t):
        if not t:
            return ""
        try:
            d = datetime.datetime.fromisoformat(str(t))
        except ValueError:
            return t
        return d.strftime("%d.%m.%Y %H:%M") if "T" in str(t) else d.strftime("%d.%m.%Y")

    @app.template_filter("zahl")
    def zahl(x):
        if x is None:
            return ""
        return f"{x:g}".replace(".", ",")

    if not app.config.get("TESTING") and os.environ.get("AUTO_ABRUF", "1") == "1":
        from . import hintergrund
        hintergrund.starten(app)
    return app
