import smtplib

from moonwing.services.notifications import SmtpSettings, _send_email_sync


def test_send_email_sync_raises_when_smtp_send_fails(monkeypatch):
    class FailingSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            return None

        def starttls(self):
            return None

        def login(self, username, password):
            return None

        def sendmail(self, from_address, recipients, message):
            raise smtplib.SMTPException("relay rejected")

        def quit(self):
            return None

    monkeypatch.setattr(smtplib, "SMTP", FailingSMTP)
    settings = SmtpSettings(
        host="smtp.example.com",
        port=587,
        username="user@example.com",
        password="secret",
        use_tls=True,
        from_address="moonwing@example.com",
        from_name="Moonwing",
    )

    try:
        _send_email_sync(settings, ["admin@example.com"], "subject", "<p>body</p>")
    except smtplib.SMTPException as exc:
        assert "relay rejected" in str(exc)
    else:
        raise AssertionError("SMTP send failure should be raised to caller")



def test_send_email_sync_uses_smtp_ssl_for_port_465(monkeypatch):
    calls = []

    class SSLOnlySMTP:
        def __init__(self, host, port, timeout):
            calls.append(("ssl", host, port, timeout))

        def ehlo(self):
            calls.append(("ehlo",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def sendmail(self, from_address, recipients, message):
            calls.append(("sendmail", from_address, recipients))

        def quit(self):
            calls.append(("quit",))

    def plain_smtp(*args, **kwargs):
        raise AssertionError("port 465 should use SMTP_SSL, not SMTP")

    monkeypatch.setattr(smtplib, "SMTP", plain_smtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", SSLOnlySMTP)
    settings = SmtpSettings(
        host="smtp.gmail.com",
        port=465,
        username="user@example.com",
        password="secret",
        use_tls=True,
        from_address="moonwing@example.com",
        from_name="Moonwing",
    )

    _send_email_sync(settings, ["admin@example.com"], "subject", "<p>body</p>")

    assert calls[0] == ("ssl", "smtp.gmail.com", 465, 10)
    assert ("login", "user@example.com", "secret") in calls
    assert any(call[0] == "sendmail" for call in calls)
