"""E-Mail-Versand über SMTP (Zugang in den Einstellungen)."""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import db


class MailFehler(Exception):
    pass


def eingerichtet():
    return bool(db.setting("smtp_host") and db.setting("smtp_absender"))


def senden(an, betreff, text, anhaenge=()):
    """anhaenge: [(dateiname, bytes, mime)]"""
    if not eingerichtet():
        raise MailFehler("E-Mail-Versand ist noch nicht eingerichtet (Einstellungen → E-Mail).")
    msg = EmailMessage()
    msg["From"] = formataddr((db.setting("verwaltung_name"), db.setting("smtp_absender")))
    msg["To"] = an
    msg["Subject"] = betreff
    msg["Message-ID"] = make_msgid(domain=db.setting("smtp_absender").split("@")[-1])
    msg.set_content(text)
    for name, daten, mime in anhaenge:
        haupt, unter = mime.split("/", 1)
        msg.add_attachment(daten, maintype=haupt, subtype=unter, filename=name)
    host = db.setting("smtp_host")
    port = int(db.setting("smtp_port") or 587)
    user, pw = db.setting("smtp_user"), db.decrypt(db.setting("smtp_pass"))
    try:
        if port == 465:
            s = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30)
        else:
            s = smtplib.SMTP(host, port, timeout=30)
            s.starttls(context=ssl.create_default_context())
        with s:
            if user:
                s.login(user, pw)
            s.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise MailFehler(f"Versand an {an} fehlgeschlagen: {e}")
