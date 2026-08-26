from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from config import Config  # noqa: E402
from pending_client_digest_service import run_pending_client_digest  # noqa: E402
from repository import RepositoryError, build_repository  # noqa: E402


def config_dict() -> dict[str, Any]:
    return {
        key: value
        for key, value in vars(Config).items()
        if key.isupper()
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send one management email containing person-wise tasks currently "
            "Pending for Client Input or Pending for Client Confirmation."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and validate the report without sending email.",
    )
    parser.add_argument(
        "--date",
        help="Override the office date for testing, in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow a manual run on a day other than the 1st, 11th, or 21st.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = config_dict()

    report_date = None
    if args.date:
        try:
            report_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("ERROR: --date must be YYYY-MM-DD", file=sys.stderr)
            return 2

    try:
        repository = build_repository(settings)
        result = run_pending_client_digest(
            repository,
            settings,
            report_date=report_date,
            dry_run=args.dry_run,
            enforce_schedule_day=not args.force,
        )
    except (RepositoryError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: unexpected pending-client digest failure: {exc}", file=sys.stderr)
        return 1

    mode = "DRY RUN" if args.dry_run else "SEND"
    print(
        f"[{mode}] office date={result.report_date.isoformat()} "
        f"eligible tasks={result.eligible_tasks} "
        f"assignees={result.assignee_count} "
        f"recipients={result.recipient_count} "
        f"emails sent={result.sent_emails} "
        f"skipped={'yes' if result.skipped else 'no'}"
    )

    for warning in result.warnings:
        print(f"WARNING: {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
