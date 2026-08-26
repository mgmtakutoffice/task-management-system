import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


def run_script(script_name, extra_args=None):
    args = [sys.executable, script_name]

    if extra_args:
        args.extend(extra_args)

    print(f"\nRunning: {' '.join(args)}")

    result = subprocess.run(args)

    if result.returncode != 0:
        print(
            f"ERROR: {script_name} finished with "
            f"exit code {result.returncode}"
        )
        return False

    print(f"SUCCESS: {script_name}")
    return True


def main():
    now = datetime.now(IST)

    print(
        f"Scheduled email controller started "
        f"at {now:%Y-%m-%d %I:%M %p} IST"
    )

    success = True

    # ---------------------------------------------------------
    # 8:00 AM IST
    # Overdue task reminder
    # ---------------------------------------------------------
    if now.hour == 8:
        success &= run_script(
            "send_overdue_reminders.py"
        )

    # ---------------------------------------------------------
    # 9:00 AM IST
    # 1-day-prior reminder
    # ---------------------------------------------------------
    elif now.hour == 9:
        success &= run_script(
            "send_due_reminders.py"
        )

        # Pending Client Digest only on 1st, 11th and 21st
        if now.day in {1, 11, 21}:
            success &= run_script(
                "send_pending_client_digest.py"
            )
        else:
            print(
                "Pending Client Digest not required today. "
                "Runs only on 1st, 11th and 21st."
            )

    else:
        print(
            "No scheduled email routine for this IST hour."
        )

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()