"""Read-only data integrity check. It reports problems and never changes anything.

python -m app.integrity_cli [--shop 1] [--json]

Exit code 0: no errors found. Exit code 1: at least one error-level finding (something that should be impossible).
"""

import argparse
import json

from app.db.session import read_session
from app.reporting import integrity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="integrity_cli")
    parser.add_argument("--shop", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    with read_session() as session:
        report = integrity.summary(integrity.run(session, args.shop))
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    elif report["ok"] and not report["findings"]:
        print("No integrity problems found.")
    else:
        for f in report["findings"]:
            print(
                f"[{f['severity']}] {f['check']}: {f['count']} (e.g. ids {f['sample_ids']}). {f['description']}"
            )
            print(f"    {f['suggestion']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
