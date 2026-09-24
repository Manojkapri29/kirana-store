"""Email over SMTP: the one vendor-neutral email transport. Any mail service that offers SMTP works; nothing here names a vendor.

Configuration (non-secret, per shop): host, port, security (`starttls`, `ssl` or `none`), sender, username. The password is the value of
the environment variable named by the integration's `credential_ref`. Header injection is prevented (no CR/LF in any header) and the
recipient must look like one address.
"""

import re
import smtplib
import socket
import ssl
from email.message import EmailMessage

from app.integrations.base import ProviderError, clean_header

_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


class SmtpEmailProvider:
    name = "smtp"

    def __init__(
        self, *, host: str, port: int, security: str, sender: str, username: str | None, password: str | None
    ) -> None:
        self.host, self.port, self.security = host, port, security
        self.sender, self.username, self.password = sender, username, password

    def _connect(self, timeout: float) -> smtplib.SMTP:
        try:
            if self.security == "ssl":
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                    self.host, self.port, timeout=timeout, context=ssl.create_default_context()
                )
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=timeout)
                if self.security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
            if self.username and self.password:
                smtp.login(self.username, self.password)
            return smtp
        except smtplib.SMTPAuthenticationError as exc:
            raise ProviderError("invalid_credentials", retryable=False) from exc
        except (smtplib.SMTPConnectError, ConnectionError, socket.gaierror) as exc:
            raise ProviderError("connection_failed", retryable=True) from exc
        except TimeoutError as exc:
            raise ProviderError("connection_timeout", retryable=True) from exc
        except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
            raise ProviderError("provider_error", retryable=False) from exc

    def check(self, *, timeout: float) -> None:
        smtp = self._connect(timeout)
        try:
            smtp.noop()
        except smtplib.SMTPException as exc:
            raise ProviderError("provider_error", retryable=False) from exc
        finally:
            try:
                smtp.quit()
            except (smtplib.SMTPException, OSError):
                pass

    def send(self, *, recipient: str | None, title: str, message: str, timeout: float) -> str | None:
        if not recipient or not _ADDRESS.match(recipient) or len(recipient) > 254:
            raise ProviderError("invalid_recipient", retryable=False)
        mail = EmailMessage()
        mail["From"] = clean_header(self.sender)
        mail["To"] = recipient
        mail["Subject"] = clean_header(title, 150)
        mail.set_content(message)
        smtp = self._connect(timeout)
        try:
            smtp.send_message(mail)
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [code for code, _ in exc.recipients.values()]
            if codes and all(
                400 <= code < 500 for code in codes
            ):  # 4xx: the server asked us to come back later
                raise ProviderError("recipient_temporary_failure", retryable=True) from exc
            raise ProviderError("recipient_rejected", retryable=False) from exc
        except smtplib.SMTPResponseException as exc:
            raise ProviderError(f"smtp_{exc.smtp_code}", retryable=400 <= exc.smtp_code < 500) from exc
        except TimeoutError as exc:
            # The server may or may not have accepted the message: not repeated automatically.
            raise ProviderError("timeout_unknown_outcome", retryable=False) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise ProviderError("provider_error", retryable=False) from exc
        finally:
            try:
                smtp.quit()
            except (smtplib.SMTPException, OSError):
                pass
        return None
