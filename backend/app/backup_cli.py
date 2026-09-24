"""Operator tool for backups and restores. Meant to be run by cron (create, retention) or by an operator (restore).

python -m app.backup_cli create [--scheduled]
python -m app.backup_cli list
python -m app.backup_cli verify BACKUP_KEY
python -m app.backup_cli rehearse BACKUP_KEY            (a trial restore on a temporary copy; the live database is untouched)
python -m app.backup_cli retention [--apply]            (a dry run unless --apply)
python -m app.backup_cli restore BACKUP_KEY --confirm "RESTORE BACKUP_KEY"   (STOP THE APPLICATION FIRST)

Exit code 0 means success; 1 means a backup or restore failed (the reason is printed and recorded).
"""

import argparse

from app.core.config import get_settings
from app.db.engine import get_engine
from app.db.session import read_session, write_transaction
from app.models.enums import BackupKind
from app.services import backup_service, restore_service


def _record(key: str):  # noqa: ANN202
    with read_session() as session:
        record = backup_service.get_record(session, key)
        if record is not None:
            session.expunge(record)  # keep its loaded values: closing the session would otherwise expire them
    if record is None:
        raise SystemExit(f"No backup named {key}.")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backup_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--scheduled", action="store_true")
    sub.add_parser("list")
    for name in ("verify", "rehearse"):
        sub.add_parser(name).add_argument("key")
    retention = sub.add_parser("retention")
    retention.add_argument("--apply", action="store_true")
    restore = sub.add_parser("restore")
    restore.add_argument("key")
    restore.add_argument("--confirm", required=True)
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "create":
        outcome = backup_service.perform_backup(
            BackupKind.SCHEDULED if args.scheduled else BackupKind.MANUAL, "cli", settings
        )
        with write_transaction() as session:
            backup_service.record_outcome(session, outcome, settings.backup_storage_provider)
        print(f"{'OK' if outcome.ok else 'FAILED'} {outcome.key} {outcome.error_message or ''}".strip())
        return 0 if outcome.ok else 1
    if args.command == "list":
        with read_session() as session:
            for r in backup_service.list_records(session, limit=200):
                print(
                    f"{r.backup_key}  {r.status.value:9}  {r.kind.value:11}  {r.size_bytes or '-':>10}  {r.schema_revision or '-'}  {r.created_at:%Y-%m-%d %H:%M}"
                )
        return 0
    if args.command == "verify":
        with write_transaction() as session:
            record = backup_service.get_record(session, args.key)
            if record is None:
                raise SystemExit(f"No backup named {args.key}.")
            result = backup_service.verify_record(session, record)
        print("OK" if result.ok else "FAILED", result.checks)
        return 0 if result.ok else 1
    if args.command == "rehearse":
        result = restore_service.rehearse(_record(args.key), "cli")
        print("OK" if result.ok else "FAILED", result.detail, result.report or "")
        return 0 if result.ok else 1
    if args.command == "retention":
        with write_transaction() as session:
            plan = backup_service.apply_retention(
                session, actor="cli", dry_run=not args.apply, settings=settings
            )
        print(
            ("Deleted" if args.apply else "Would delete"),
            plan["deleted" if args.apply else "delete"],
            "| keeping",
            len(plan["keep"]),
        )
        return 0
    record = _record(args.key)
    get_engine().dispose()  # this process must not hold the database open: a restore needs it to itself
    result = restore_service.restore(
        record, confirmation=args.confirm, actor="cli", via_api=False, settings=settings
    )
    if result.ok:
        restore_service.record_restore(result, "cli", settings)
    print("OK" if result.ok else "REFUSED/FAILED", result.detail)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
