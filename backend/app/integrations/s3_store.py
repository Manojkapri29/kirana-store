"""File storage on an S3-compatible service (AWS Signature Version 4, path-style or virtual-host), with no SDK dependency.

STATUS: implemented and unit-tested (the signature against the published AWS derivation steps, and a full put/get/delete round trip
against a local server that re-computes the signature). It has NOT been run against a real S3-compatible service in this
environment, so it is reported as "not verified against a live provider" until someone does that with real credentials.

The interface is the same as the local store (`put`, `get`, `delete` on a validated key), so the image code does not change. Keys are
validated exactly as for the local store, so a key can never contain a path separator from user input. Objects are private; nothing
here creates a public URL.
"""

import hashlib
import hmac
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from app.integrations.base import ProviderError, safe_outbound_url

_EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def signing_key(secret: str, date: str, region: str, service: str = "s3") -> bytes:
    return _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode(), date), region), service), "aws4_request")


def authorization(
    *,
    method: str,
    path: str,
    host: str,
    payload_hash: str,
    amz_date: str,
    region: str,
    access_key: str,
    secret: str,
) -> str:
    """The `Authorization` header for a request with no query string and only host, x-amz-content-sha256 and x-amz-date signed."""
    date = amz_date[:8]
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical = "\n".join(
        [method, urllib.parse.quote(path, safe="/-_.~"), "", canonical_headers, signed, payload_hash]
    )
    scope = f"{date}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signature = hmac.new(signing_key(secret, date, region), to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


class S3Store:
    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        region: str,
        access_key: str,
        secret: str,
        path_style: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.endpoint, self.bucket, self.region = endpoint.rstrip("/"), bucket, region
        self.access_key, self.secret, self.path_style, self.timeout = access_key, secret, path_style, timeout

    def _target(self, key: str) -> tuple[str, str, str]:
        parsed = urllib.parse.urlparse(self.endpoint)
        host = parsed.netloc if self.path_style else f"{self.bucket}.{parsed.netloc}"
        path = f"/{self.bucket}/{key}" if self.path_style else f"/{key}"
        return f"{parsed.scheme}://{host}{path}", host, path

    def _call(self, method: str, key: str, data: bytes | None = None) -> bytes | None:
        from app.services.image_store import validate_key

        validate_key(key)
        url, host, path = self._target(key)
        safe_outbound_url(url)
        payload_hash = hashlib.sha256(data).hexdigest() if data is not None else _EMPTY_SHA
        amz_date = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        headers = {
            "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash,
            "Authorization": authorization(method=method, path=path, host=host, payload_hash=payload_hash, amz_date=amz_date, region=self.region, access_key=self.access_key, secret=self.secret),
        }  # fmt: skip
        request = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
        try:
            with urllib.request.build_opener(_NoRedirect).open(request, timeout=self.timeout) as response:  # noqa: S310
                return response.read() if method == "GET" else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                return None
            if exc.code in (401, 403):
                raise ProviderError("invalid_credentials", retryable=False) from exc
            raise ProviderError(f"http_{exc.code}", retryable=exc.code >= 500) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            raise ProviderError("connection_failed", retryable=True) from exc

    def put(self, key: str, data: bytes) -> None:
        self._call("PUT", key, data)

    def get(self, key: str) -> bytes | None:
        return self._call("GET", key)

    def delete(self, key: str) -> None:
        self._call("DELETE", key)
