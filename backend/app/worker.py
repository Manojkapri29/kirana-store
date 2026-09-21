"""The background worker. Runs due jobs (see `background_job_service`). Not started by the web server.

python -m app.worker --once            run what is due now, then exit (good for cron)
python -m app.worker --loop            keep running; look for work every --interval seconds
python -m app.worker --schedule        also queue the routine jobs (backup, retention, session cleanup, notification retries) on each pass
python -m app.worker --list            show recent jobs

Exit code 0 unless a job failed permanently in this run.
"""

import argparse
import time

from app.db.session import read_session, write_transaction
from app.services import background_job_service as jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--schedule", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)

    if args.list:
        with read_session() as session:
            for j in jobs.list_jobs(session, limit=30):
                print(
                    f"#{j.id:<5} {j.job_type:<32} {j.status.value:<10} attempts={j.attempts}/{j.max_attempts} {j.error_code or ''}"
                )
        return 0
    failed = False
    while True:
        if (
            args.schedule
        ):  # every pass: the routine jobs carry a date/minute key, so asking again queues nothing new
            with write_transaction() as session:
                jobs.schedule_periodic(session)
        for outcome in jobs.run_due(limit=args.limit):
            print(f"job {outcome['job_id']} {outcome['type']}: {outcome['status']}")
            failed = failed or outcome["status"] == "FAILED"
        if not args.loop:
            break
        time.sleep(args.interval)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
