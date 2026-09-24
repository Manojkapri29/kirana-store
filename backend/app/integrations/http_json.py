"""A generic HTTPS message gateway for SMS, WhatsApp and push: POST a small JSON document to a URL the shop configures, with a bearer
token from the environment. It names no vendor and claims compatibility with none: a gateway that accepts this shape works, and a
relay can adapt any other. The body is `{"channel", "to", "title", "message"}`; a 2xx answer means accepted.

The URL is checked against server-side request forgery before every call (`safe_outbound_url`); redirects are not followed.
"""

import json
import socket
import urllib.error
import urllib.request

from app.integrations.base import ProviderError, clean_header, resolve_secret, safe_outbound_url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


class HttpJsonProvider:
    name = "http_json"

    def __init__(self, *, channel: str, url: str, token: str | None, sender: str | None = None) -> None:
        self.channel, self.url, self.token, self.sender = channel, url, token, sender

    def check(self, *, timeout: float) -> None:
        safe_outbound_url(self.url)
        if not self.token:
            raise ProviderError("credentials_missing", retryable=False)

    def send(self, *, recipient: str | None, title: str, message: str, timeout: float) -> str | None:
        if not recipient:
            raise ProviderError("invalid_recipient", retryable=False)
        safe_outbound_url(self.url)
        if not self.token:
            raise ProviderError("credentials_missing", retryable=False)
        body = json.dumps(
            {
                "channel": self.channel,
                "to": recipient,
                "title": title,
                "message": message,
                "from": self.sender,
            }
        ).encode()
        request = urllib.request.Request(  # noqa: S310  (the URL is validated above)
            self.url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {clean_header(self.token, 500)}", "User-Agent": "kirana-store"},
        )  # fmt: skip
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=timeout) as response:  # noqa: S310
                if 200 <= response.status < 300:
                    return None
                raise ProviderError(f"http_{response.status}", retryable=False)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ProviderError("invalid_credentials", retryable=False) from exc
            raise ProviderError(f"http_{exc.code}", retryable=exc.code == 429 or exc.code >= 500) from exc
        except TimeoutError as exc:
            raise ProviderError("timeout_unknown_outcome", retryable=False) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout | TimeoutError):
                raise ProviderError("timeout_unknown_outcome", retryable=False) from exc
            raise ProviderError("connection_failed", retryable=True) from exc


def build(channel: str, config: dict, secret: str | None) -> HttpJsonProvider:
    return HttpJsonProvider(
        channel=channel, url=str(config.get("url", "")), token=secret, sender=config.get("sender")
    )


def secret_of(ref: str | None) -> str | None:
    return resolve_secret(ref)
