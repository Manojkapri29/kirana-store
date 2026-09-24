"""Real local servers for integration tests: a tiny SMTP server, an HTTP gateway and an S3-style store that re-computes the AWS
signature. They prove the adapters speak the actual protocols; they say nothing about any live provider."""

import base64
import hashlib
import json
import socketserver
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.integrations import payments
from app.integrations.base import PaymentProvider, ProviderError, ProviderPayment, WebhookNotice
from app.integrations.s3_store import authorization
from app.models.enums import OnlinePaymentStatus

# --- SMTP ---------------------------------------------------------------------------------------------------------


class _SmtpHandler(socketserver.StreamRequestHandler):
    def send(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        srv = self.server
        self.send("220 fake ESMTP")
        message: list[str] = []
        in_data = False
        recipient = None
        for raw in self.rfile:
            line = raw.decode(errors="replace").rstrip("\r\n")
            if in_data:
                if line == ".":
                    in_data = False
                    srv.received.append({"to": recipient, "data": "\n".join(message)})
                    self.send("250 queued")
                    message = []
                else:
                    message.append(line[1:] if line.startswith("..") else line)
                continue
            cmd = line.split(" ")[0].upper()
            if cmd in ("EHLO", "HELO"):
                self.wfile.write(b"250-fake\r\n250-AUTH PLAIN\r\n250 8BITMIME\r\n")
            elif cmd == "AUTH":
                parts = line.split(" ")
                creds = base64.b64decode(parts[2]).split(b"\0") if len(parts) > 2 else []
                ok = len(creds) == 3 and (creds[1].decode(), creds[2].decode()) == srv.credentials
                self.send("235 ok" if ok else "535 authentication failed")
            elif cmd == "MAIL":
                self.send("250 ok")
            elif cmd == "RCPT":
                recipient = line.split(":", 1)[1].strip(" <>")
                self.send({"ok": "250 ok", "temp": "450 try later", "reject": "550 no such user"}[srv.mode])
            elif cmd == "DATA":
                in_data = True
                self.send("354 go")
            elif cmd == "NOOP":
                self.send("250 ok")
            elif cmd == "QUIT":
                self.send("221 bye")
                return
            else:
                self.send("250 ok")


class FakeSmtp(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, credentials: tuple[str, str] = ("mailer", "s3cret-pass")) -> None:
        super().__init__(("127.0.0.1", 0), _SmtpHandler)
        self.received: list[dict] = []
        self.mode = "ok"
        self.credentials = credentials
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


# --- HTTP gateway -------------------------------------------------------------------------------------------------


class FakeGateway:
    def __init__(self, token: str = "gw-token") -> None:
        self.requests: list[dict] = []
        self.status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append(
                    {"auth": self.headers.get("Authorization"), "body": json.loads(body or b"{}")}
                )
                ok = self.headers.get("Authorization") == f"Bearer {token}"
                self.send_response(outer.status if ok else 401)
                self.end_headers()

            def log_message(self, *args) -> None:  # noqa: ANN002
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/send"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# --- S3-style store -----------------------------------------------------------------------------------------------


class FakeS3:
    def __init__(
        self,
        access: str = "AKIDEXAMPLE",
        secret: str = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        region: str = "us-east-1",
    ) -> None:
        self.objects: dict[str, bytes] = {}
        self.rejected = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _check(self, payload: bytes) -> bool:
                host = self.headers["Host"]
                amz_date = self.headers["x-amz-date"]
                expected = authorization(
                    method=self.command, path=self.path, host=host, payload_hash=hashlib.sha256(payload).hexdigest(), amz_date=amz_date,
                    region=region, access_key=access, secret=secret,
                )  # fmt: skip
                return (
                    self.headers.get("Authorization") == expected
                    and self.headers["x-amz-content-sha256"] == hashlib.sha256(payload).hexdigest()
                )

            def _reply(self, code: int, body: bytes = b"") -> None:
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_PUT(self) -> None:  # noqa: N802
                data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if not self._check(data):
                    outer.rejected += 1
                    return self._reply(403)
                outer.objects[self.path] = data
                self._reply(200)

            def do_GET(self) -> None:  # noqa: N802
                if not self._check(b""):
                    outer.rejected += 1
                    return self._reply(403)
                if self.path in outer.objects:
                    return self._reply(200, outer.objects[self.path])
                self._reply(404)

            def do_DELETE(self) -> None:  # noqa: N802
                if not self._check(b""):
                    outer.rejected += 1
                    return self._reply(403)
                outer.objects.pop(self.path, None)
                self._reply(204)

            def log_message(self, *args) -> None:  # noqa: ANN002
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# --- A payment provider that talks to no one (tests only: it is never registered in the application) ------------------


class FakeGatewayPayments(PaymentProvider):
    """An 'external' provider with a scripted world, used to exercise the lifecycle, retries and unknown outcomes."""

    name = "fake_gateway"
    external = True
    world: dict[str, ProviderPayment] = {}
    create_error: ProviderError | None = None
    create_calls = 0
    refund_calls = 0
    status_calls = 0
    status_errors: list[ProviderError] = []

    def create(
        self, *, amount: Decimal, method: str, reference: str, idempotency_key: str, timeout: float
    ) -> ProviderPayment:
        type(self).create_calls += 1
        if type(self).create_error is not None:
            raise type(self).create_error
        payment = ProviderPayment(f"fg_{type(self).create_calls}", OnlinePaymentStatus.PENDING.value, amount)
        type(self).world[payment.txn_id] = payment
        return payment

    def fetch_status(self, txn_id: str, *, timeout: float) -> ProviderPayment:
        type(self).status_calls += 1
        if type(self).status_errors:
            raise type(self).status_errors.pop(0)
        return type(self).world[txn_id]

    def refund(
        self, txn_id: str, *, amount: Decimal, idempotency_key: str, timeout: float
    ) -> ProviderPayment:
        type(self).refund_calls += 1
        return ProviderPayment(txn_id, OnlinePaymentStatus.REFUNDED.value, None, amount)

    def parse_webhook(self, headers: dict[str, str], body: bytes, *, secret: str) -> WebhookNotice:
        return payments.verify_and_parse(headers, body, secret)

    @classmethod
    def reset(cls) -> None:
        (
            cls.world,
            cls.create_error,
            cls.create_calls,
            cls.refund_calls,
            cls.status_calls,
            cls.status_errors,
        ) = {}, None, 0, 0, 0, []


def signed(secret: str, event: dict, *, timestamp: int | None = None) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(event).encode()
    ts = str(int(time.time()) if timestamp is None else timestamp)
    return body, {
        "X-Kirana-Timestamp": ts,
        "X-Kirana-Signature": payments.sign(secret, ts, body),
        "Content-Type": "application/json",
    }
