"""Backups now carry the kept photos, restore puts them back, and a restore no longer forgets newer backups."""

import gc
import hashlib
import io
import json
import sqlite3
import tarfile

import pytest

from app.core.config import get_settings
from app.services import backup_service, restore_service
from tests.test_phase11_backup import env, make  # noqa: F401

KEY_A = "7/" + hashlib.sha256(b"a").hexdigest() + ".png"
KEY_B = "7/" + hashlib.sha256(b"b").hexdigest() + ".jpg"


@pytest.fixture
def photos(env, monkeypatch, tmp_path):  # noqa: F811
    folder = tmp_path / "photos"
    monkeypatch.setenv("KIRANA_IMAGE_STORAGE_DIR", str(folder))
    get_settings.cache_clear()
    for key, data in ((KEY_A, b"A-bytes"), (KEY_B, b"B-bytes")):
        (folder / key).parent.mkdir(parents=True, exist_ok=True)
        (folder / key).write_bytes(data)
    (folder / "notes.txt").write_text("not a photo")  # never archived
    return folder


def _restore(session_factory, session, engine, key):
    with session_factory() as s:
        record = backup_service.get_record(s, key)
    session.close()
    gc.collect()
    engine.dispose()
    return restore_service.restore(
        record, confirmation=restore_service.confirmation_phrase(key), actor="t@test", via_api=False
    )


def test_the_backup_archives_only_valid_photos_and_describes_them(env, photos, session_factory):  # noqa: F811
    outcome, key = make(session_factory)
    manifest = json.loads((env / f"{key}.json").read_text())
    assert outcome.ok and manifest["images_count"] == 2 and manifest["images_file"] == f"{key}.images.tar.gz"
    assert manifest["images_sha256"] == hashlib.sha256((env / manifest["images_file"]).read_bytes()).hexdigest()
    with tarfile.open(env / manifest["images_file"]) as tar:
        assert sorted(tar.getnames()) == sorted([KEY_A, KEY_B])


def test_no_photos_means_no_archive(env, monkeypatch, tmp_path, session_factory):  # noqa: F811
    monkeypatch.setenv("KIRANA_IMAGE_STORAGE_DIR", str(tmp_path / "empty"))
    get_settings.cache_clear()
    _, key = make(session_factory)
    manifest = json.loads((env / f"{key}.json").read_text())
    assert manifest["images_file"] is None and manifest["images_count"] == 0
    assert not (env / f"{key}.images.tar.gz").exists()


def test_a_restore_puts_lost_photos_back_and_keeps_newer_ones(env, photos, session_factory, session, engine):  # noqa: F811
    _, key = make(session_factory)
    (photos / KEY_A).unlink()  # disaster: one photo lost
    newer = "7/" + hashlib.sha256(b"c").hexdigest() + ".webp"
    (photos / newer).write_bytes(b"C-bytes")  # a photo added after the backup
    (photos / KEY_B).write_bytes(b"B-changed")  # an existing file is never overwritten
    result = _restore(session_factory, session, engine, key)
    assert result.ok and "1 photo(s) restored" in result.detail
    assert (photos / KEY_A).read_bytes() == b"A-bytes"
    assert (photos / newer).read_bytes() == b"C-bytes" and (photos / KEY_B).read_bytes() == b"B-changed"


def test_a_tampered_photo_archive_is_refused_but_the_database_still_restores(env, photos, session_factory, session, engine):  # noqa: F811
    _, key = make(session_factory)
    (env / f"{key}.images.tar.gz").write_bytes(b"not the archive")
    (photos / KEY_A).unlink()
    result = _restore(session_factory, session, engine, key)
    assert result.ok and "failed its checksum" in result.detail
    assert not (photos / KEY_A).exists()


def test_unsafe_archive_members_are_never_extracted(env, photos, session_factory, session, engine, tmp_path):  # noqa: F811
    _, key = make(session_factory)
    evil = env / f"{key}.images.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        for name in ("../escape.png", "7/not-a-hash.png", KEY_A):
            info = tarfile.TarInfo(name)
            info.size = 3
            tar.addfile(info, io.BytesIO(b"xyz"))
    manifest = json.loads((env / f"{key}.json").read_text())
    manifest["images_sha256"] = hashlib.sha256(evil.read_bytes()).hexdigest()
    (env / f"{key}.json").write_text(json.dumps(manifest))
    (photos / KEY_A).unlink()
    result = _restore(session_factory, session, engine, key)
    assert result.ok
    assert not (tmp_path / "escape.png").exists() and not (photos.parent / "escape.png").exists()
    assert not (photos / "7" / "not-a-hash.png").exists() and (photos / KEY_A).read_bytes() == b"xyz"


def test_a_restore_lists_the_newer_backups_again(env, photos, session_factory, session, engine, db_url):  # noqa: F811
    _, older = make(session_factory)
    _, newer = make(session_factory)
    result = _restore(session_factory, session, engine, older)
    assert result.ok
    con = sqlite3.connect(db_url.removeprefix("sqlite:///"))
    keys = {r[0] for r in con.execute("SELECT backup_key FROM backup_records")}
    con.close()
    assert {older, newer, result.pre_restore_key} <= keys
    with session_factory() as s:
        assert backup_service.get_record(s, newer).status.value == "VERIFIED"
        assert backup_service.verify_record(s, backup_service.get_record(s, newer)).ok


def test_retention_deletion_removes_the_photo_archive_too(env, photos, session_factory):  # noqa: F811
    _, key = make(session_factory)
    backup_service.LocalBackupStorage(env).delete(f"{key}.db")
    assert not (env / f"{key}.images.tar.gz").exists() and not (env / f"{key}.json").exists()


def test_every_nginx_location_that_sets_headers_also_includes_the_security_headers():
    """nginx drops server-level add_header in a location that has its own; the snippet must be included there (checked live with nginx in the follow-up QA)."""
    import re
    from pathlib import Path

    conf = (Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf").read_text()
    blocks = re.findall(r"location [^{]+\{(.*?)\n    \}", conf, re.S)
    assert blocks
    for block in blocks:
        if "add_header" in block:
            assert "include /etc/nginx/snippets/security-headers.conf;" in block, block
