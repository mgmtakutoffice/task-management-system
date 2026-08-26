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
from due_reminder_service import run_due_reminders  # noqa: E402
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
            "Send one consolidated reminder per active user for tasks "
            "due tomorrow."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without sending email or updating tasks.",
    )
    parser.add_argument(
        "--date",
        help=(
            "Override today's office date for testing, in YYYY-MM-DD format. "
            "Do not use this option in the daily scheduler."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = config_dict()

    today = None
    if args.date:
        try:
            today = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("ERROR: --date must be YYYY-MM-DD", file=sys.stderr)
            return 2

    try:
        repository = build_repository(settings)
        result = run_due_reminders(
            repository,
            settings,
            today=today,
            dry_run=args.dry_run,
        )
    except (RepositoryError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: unexpected reminder failure: {exc}", file=sys.stderr)
        return 1

    mode = "DRY RUN" if args.dry_run else "SEND"
    print(
        f"[{mode}] office date={result.today.isoformat()} "
        f"due tomorrow={result.due_date.isoformat()} "
        f"eligible tasks={result.eligible_tasks} "
        f"recipients={result.recipient_count} "
        f"emails sent={result.sent_emails} "
        f"tasks marked sent={result.updated_tasks}"
    )

    for warning in result.warnings:
        print(f"WARNING: {warning}")

    if result.failed_recipients:
        print(
            "ERROR: reminder delivery failed for: "
            + ", ".join(result.failed_recipients),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
