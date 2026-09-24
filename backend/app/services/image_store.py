"""Where kept photos live. A private folder now; the same small interface fits object storage later.

Files are never served straight from disk and never have a public address. They are written under a key that
contains the shop and the file's hash (`<shop_id>/<sha256>.<ext>`), read back only through an
authenticated, shop- scoped endpoint, and the folder is outside anything the web server exposes. A key can
never contain a path separator from user input: it is built from a number and a hex digest.
"""

import os
import re
from pathlib import Path
from typing import Protocol

from app.core.config import BACKEND_DIR, Settings

_KEY = re.compile(r"^\d{1,12}/[0-9a-f]{64}\.(jpg|png|webp)$")


class ImageStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes | None: ...

    def delete(self, key: str) -> None: ...


def validate_key(key: str) -> None:
    """Raises ValueError unless `key` is `<shop id>/<sha256>.<jpg|png|webp>`: shared by every store."""
    if not _KEY.match(key):
        raise ValueError("invalid storage key")


def storage_key(shop_id: int, sha256: str, extension: str) -> str:
    return f"{shop_id}/{sha256}.{extension}"


class LocalImageStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self.root / key).resolve()
        if self.root not in path.parents:  # never outside the folder, whatever the key says
            raise ValueError("invalid storage key")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".part")
        temp.write_bytes(data)
        os.replace(temp, path)  # a reader never sees half a file

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def default_store(settings: Settings) -> ImageStore:
    if settings.storage_provider == "s3":
        from app.integrations.base import resolve_secret
        from app.integrations.s3_store import S3Store

        access, _, secret = (resolve_secret(settings.storage_credentials_ref) or "").partition(":")
        if not (settings.storage_s3_endpoint and settings.storage_s3_bucket and access and secret):
            raise RuntimeError("S3 storage is selected but is not fully configured")
        return S3Store(
            endpoint=settings.storage_s3_endpoint,
            bucket=settings.storage_s3_bucket,
            region=settings.storage_s3_region,
            access_key=access,
            secret=secret,
            path_style=settings.storage_s3_path_style,
        )
    root = Path(settings.image_storage_dir)
    return LocalImageStore(root if root.is_absolute() else BACKEND_DIR / root)
