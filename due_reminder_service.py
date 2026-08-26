from __future__ import annotations

import base64
import html
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import TASK_ACTIVITY_HEADERS, TASK_HEADERS
from repository import TaskRepository

DATE_FORMAT = "%Y-%m-%d"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
COMPLETED_STATUS = "completed"
PENDING_CHECKING_STATUS = "pending for checking"
PENDING_APPROVAL_STATUS = "pending completion approval"
INACTIVE_VALUES = {"no", "false", "0", "inactive"}
TRUE_VALUES = {"yes", "true", "1", "y"}
SYSTEM_ACTOR_NAME = "Task Reminder Service"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"

try:
    OFFICE_TIMEZONE = ZoneInfo("Asia/Kolkata")
except ZoneInfoNotFoundError:
    OFFICE_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="IST")


@dataclass
class ReminderRunResult:
    today: date
    due_date: date
    eligible_tasks: int = 0
    recipient_count: int = 0
    sent_emails: int = 0
    updated_tasks: int = 0
    skipped_tasks: int = 0
    failed_recipients: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.failed_recipients


def current_work_date() -> date:
    return datetime.now(OFFICE_TIMEZONE).date()


def parse_stored_date(value: Any) -> date | None:
    clean = str(value or "").strip()
    if not clean:
        return None

    # Task Manager normally stores YYYY-MM-DD. The additional formats make
    # the reminder process tolerant of manually-entered Google Sheet dates.
    if len(clean) >= 10:
        try:
            return datetime.strptime(clean[:10], DATE_FORMAT).date()
        except ValueError:
            pass

    for stored_format in (
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d-%b-%Y",
        "%d %b %Y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(clean, stored_format).date()
        except ValueError:
            continue
    return None


def _is_active(value: Any) -> bool:
    return str(value or "Yes").strip().lower() not in INACTIVE_VALUES


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def _task_status(task: Mapping[str, Any]) -> str:
    return str(task.get("Status", "")).strip().lower()


def _is_deleted(task: Mapping[str, Any]) -> bool:
    return _is_true(task.get("Deleted", ""))


def _is_archived(task: Mapping[str, Any]) -> bool:
    return _is_true(task.get("Archived", ""))


def _recipient_for_task(
    task: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    """Return (email, name, responsibility label) for the current owner."""
    status = _task_status(task)

    if status in {COMPLETED_STATUS, PENDING_APPROVAL_STATUS}:
        return None

    if status == PENDING_CHECKING_STATUS:
        email = str(task.get("Checker Email", "")).strip().lower()
        name = str(task.get("Checker Name", "")).strip()
        responsibility = "For Checking"
    else:
        email = str(task.get("Assigned To Email", "")).strip().lower()
        name = str(task.get("Assigned To", "")).strip()
        responsibility = "Assigned Task"

    if not email:
        return None
    return email, name, responsibility


def collect_due_tomorrow(
    repository: TaskRepository,
    *,
    today: date | None = None,
) -> tuple[dict[str, list[dict[str, str]]], list[str], date]:
    """Collect one-day-prior reminders grouped by active user email."""
    office_today = today or current_work_date()
    due_tomorrow = office_today + timedelta(days=1)
    due_key = due_tomorrow.isoformat()

    active_users = {
        str(user.get("Email", "")).strip().lower(): user
        for user in repository.get_users()
        if str(user.get("Email", "")).strip()
        and _is_active(user.get("Active", "Yes"))
    }

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    warnings: list[str] = []

    for task in repository.get_tasks():
        task_id = str(task.get("Task ID", "")).strip() or "(no Task ID)"

        if _is_deleted(task) or _is_archived(task):
            continue

        status = _task_status(task)
        if status in {COMPLETED_STATUS, PENDING_APPROVAL_STATUS}:
            continue

        due_date = parse_stored_date(task.get("Due Date", ""))
        if due_date != due_tomorrow:
            continue

        if (
            str(task.get("Due Reminder Sent For", "")).strip()
            == due_key
        ):
            continue

        recipient = _recipient_for_task(task)
        if recipient is None:
            warnings.append(
                f"Skipped {task_id}: no current responsible email is available."
            )
            continue

        email, task_name, responsibility = recipient
        user = active_users.get(email)
        if not user:
            warnings.append(
                f"Skipped {task_id}: {email} is not an active Task Manager user."
            )
            continue

        item = {
            header: str(task.get(header, ""))
            for header in TASK_HEADERS
        }
        item["_recipient_email"] = email
        item["_recipient_name"] = (
            str(user.get("Name", "")).strip() or task_name or email
        )
        item["_responsibility"] = responsibility
        item["_due_key"] = due_key
        grouped[email].append(item)

    for items in grouped.values():
        items.sort(
            key=lambda item: (
                str(item.get("Priority", "")).strip().lower() != "urgent",
                str(item.get("Client Name", "")).strip().lower(),
                str(item.get("Matter / Project", "")).strip().lower(),
                str(item.get("Task ID", "")).strip().lower(),
            )
        )

    return dict(grouped), warnings, due_tomorrow


def _task_summary(task: Mapping[str, Any]) -> str:
    pieces = [
        str(task.get("Client Name", "")).strip()
        or str(task.get("Client Code", "")).strip(),
        str(task.get("Matter / Project", "")).strip(),
        str(task.get("Task Description", "")).strip(),
    ]
    return " - ".join(piece for piece in pieces if piece) or str(
        task.get("Task ID", "Task")
    )


def build_reminder_message(
    *,
    recipient_email: str,
    recipient_name: str,
    tasks: list[dict[str, str]],
    due_date: date,
    config: Mapping[str, Any],
) -> EmailMessage:
    count = len(tasks)
    subject = (
        f"Task Reminder – {count} Task{'s' if count != 1 else ''} Due Tomorrow"
    )
    due_display = due_date.strftime("%d-%b-%Y")
    task_manager_url = str(config.get("TASK_MANAGER_URL", "")).strip().rstrip("/")
    my_tasks_url = f"{task_manager_url}/my-tasks" if task_manager_url else ""

    text_lines = [
        f"Dear {recipient_name},",
        "",
        (
            "This is a reminder that the following task is due tomorrow:"
            if count == 1
            else "This is a reminder that the following tasks are due tomorrow:"
        ),
        "",
    ]

    html_rows: list[str] = []
    for index, task in enumerate(tasks, start=1):
        summary = _task_summary(task)
        status = str(task.get("Status", "")).strip() or "—"
        responsibility = str(task.get("_responsibility", "")).strip()
        priority = str(task.get("Priority", "")).strip() or "Normal"
        task_id = str(task.get("Task ID", "")).strip()

        text_lines.extend(
            [
                f"{index}. {summary}",
                f"   Task ID: {task_id}",
                f"   Responsibility: {responsibility}",
                f"   Status: {status}",
                f"   Priority: {priority}",
                f"   Due Date: {due_display}",
                "",
            ]
        )

        html_rows.append(
            "<tr>"
            f"<td>{html.escape(task_id)}</td>"
            f"<td>{html.escape(summary)}</td>"
            f"<td>{html.escape(responsibility)}</td>"
            f"<td>{html.escape(status)}</td>"
            f"<td>{html.escape(priority)}</td>"
            f"<td>{html.escape(due_display)}</td>"
            "</tr>"
        )

    text_lines.append("Please review and update the tasks in the Task Manager.")
    if my_tasks_url:
        text_lines.extend(["", f"My Tasks: {my_tasks_url}"])
    text_lines.extend(["", "Anand Akut & Associates"])

    link_html = ""
    if my_tasks_url:
        link_html = (
            '<p><a href="'
            + html.escape(my_tasks_url, quote=True)
            + '" style="display:inline-block;padding:9px 14px;'
            'background:#1d4ed8;color:#fff;text-decoration:none;'
            'border-radius:6px;font-weight:700;">Open My Tasks</a></p>'
        )

    html_body = f"""\
<html>
  <body style="font-family:Arial,sans-serif;color:#17243d;line-height:1.45;">
    <p>Dear {html.escape(recipient_name)},</p>
    <p>{'This is a reminder that the following task is due tomorrow:' if count == 1 else 'This is a reminder that the following tasks are due tomorrow:'}</p>
    <table style="border-collapse:collapse;width:100%;max-width:980px;">
      <thead>
        <tr style="background:#17243d;color:#fff;">
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Task ID</th>
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Task</th>
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Responsibility</th>
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Status</th>
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Priority</th>
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Due Date</th>
        </tr>
      </thead>
      <tbody>{''.join(html_rows)}</tbody>
    </table>
    <p>Please review and update the tasks in the Task Manager.</p>
    {link_html}
    <p>Regards,<br><strong>Anand Akut &amp; Associates</strong></p>
  </body>
</html>
"""

    message = EmailMessage()
    from_name = str(
        config.get("GMAIL_FROM_NAME", "Anand Akut & Associates")
    ).strip()
    from_email = str(config.get("GMAIL_FROM_EMAIL", "")).strip()
    if not from_email:
        raise RuntimeError("GMAIL_FROM_EMAIL is not configured.")

    message["From"] = formataddr((from_name, from_email))
    message["To"] = recipient_email
    message["Subject"] = subject
    message.set_content("\n".join(text_lines))
    message.add_alternative(html_body, subtype="html")
    return message


def _gmail_credentials(config: Mapping[str, Any]) -> Credentials:
    """Load Gmail OAuth credentials from JSON text or an authorized-user token file.

    Local development is easiest with GMAIL_OAUTH_TOKEN_FILE pointing to the
    gmail_token.json generated by generate_gmail_token.py. On Render/production,
    GMAIL_OAUTH_TOKEN_JSON can contain the full JSON as a secret environment
    variable.
    """
    token_json = str(config.get("GMAIL_OAUTH_TOKEN_JSON", "")).strip()
    token_file = str(config.get("GMAIL_OAUTH_TOKEN_FILE", "")).strip()

    if token_json:
        try:
            token_info = json.loads(token_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "GMAIL_OAUTH_TOKEN_JSON contains invalid JSON."
            ) from exc

        try:
            credentials = Credentials.from_authorized_user_info(
                token_info,
                scopes=[GMAIL_SEND_SCOPE],
            )
        except ValueError as exc:
            raise RuntimeError(
                "GMAIL_OAUTH_TOKEN_JSON is not a valid Google OAuth authorized-user token."
            ) from exc

    elif token_file:
        try:
            credentials = Credentials.from_authorized_user_file(
                token_file,
                scopes=[GMAIL_SEND_SCOPE],
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Could not load Gmail OAuth token file: {token_file}"
            ) from exc

    else:
        raise RuntimeError(
            "Configure either GMAIL_OAUTH_TOKEN_JSON or GMAIL_OAUTH_TOKEN_FILE."
        )

    # Access tokens are short-lived. The refresh token lets the scheduled job
    # obtain a fresh access token without interactive login.
    if credentials.expired:
        if not credentials.refresh_token:
            raise RuntimeError(
                "Google OAuth token has expired and no refresh token is available."
            )
        try:
            credentials.refresh(Request())
        except Exception as exc:
            raise RuntimeError(
                "Could not refresh the Google OAuth access token."
            ) from exc

    if not credentials.valid:
        raise RuntimeError("Google OAuth credentials are not valid.")

    return credentials


def _send_with_gmail_api(
    message: EmailMessage,
    config: Mapping[str, Any],
) -> str:
    """Send one MIME message through Gmail API and return Gmail's message ID."""
    credentials = _gmail_credentials(config)

    service = build(
        "gmail",
        "v1",
        credentials=credentials,
        cache_discovery=False,
    )

    encoded_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode("utf-8")

    result = (
        service.users()
        .messages()
        .send(
            userId="me",
            body={"raw": encoded_message},
        )
        .execute()
    )

    return str(result.get("id", ""))

def _activity_row(
    *,
    task: Mapping[str, Any],
    recipient_email: str,
    responsibility: str,
    due_date: date,
    actor_email: str,
    activity_at: str,
) -> dict[str, str]:
    row = {header: "" for header in TASK_ACTIVITY_HEADERS}
    row.update(
        {
            "Activity ID": uuid.uuid4().hex,
            "Task ID": str(task.get("Task ID", "")).strip(),
            "Activity Type": "Due Date Reminder Email Sent",
            "Previous Status": str(task.get("Status", "")),
            "New Status": str(task.get("Status", "")),
            "Updated By": SYSTEM_ACTOR_NAME,
            "Updated By Email": actor_email,
            "Activity At": activity_at,
            "Previous Assignee": str(task.get("Assigned To", "")),
            "New Assignee": str(task.get("Assigned To", "")),
            "Previous Due Date": str(task.get("Due Date", "")),
            "New Due Date": str(task.get("Due Date", "")),
            "Checker Name": str(task.get("Checker Name", "")),
            "Additional Information": (
                f"Recipient: {recipient_email}; "
                f"Due Date: {due_date.isoformat()}; "
                f"Responsibility: {responsibility}; "
                "Reminder Type: One day prior; "
                "Delivery: consolidated user reminder."
            ),
        }
    )
    return row


def _mark_tasks_sent(
    repository: TaskRepository,
    *,
    tasks: list[dict[str, str]],
    recipient_email: str,
    due_date: date,
    actor_email: str,
) -> tuple[int, list[str]]:
    sent_at = datetime.now(OFFICE_TIMEZONE).strftime(DATETIME_FORMAT)
    activity_at = datetime.now(OFFICE_TIMEZONE).isoformat(timespec="seconds")
    due_key = due_date.isoformat()
    updated_count = 0
    activities: list[dict[str, str]] = []
    warnings: list[str] = []

    for task in tasks:
        task_id = str(task.get("Task ID", "")).strip()
        updated = {
            header: str(task.get(header, ""))
            for header in TASK_HEADERS
        }
        updated["Due Reminder Sent For"] = due_key
        updated["Due Reminder Sent At"] = sent_at

        try:
            repository.update_task(task_id, updated)
        except Exception as exc:  # Keep processing other tasks in the digest.
            warnings.append(
                f"Email sent to {recipient_email}, but {task_id} could not be marked as sent: {exc}"
            )
            continue

        updated_count += 1
        activities.append(
            _activity_row(
                task=updated,
                recipient_email=recipient_email,
                responsibility=str(task.get("_responsibility", "")),
                due_date=due_date,
                actor_email=actor_email,
                activity_at=activity_at,
            )
        )

    if activities:
        try:
            repository.add_task_activities(activities)
        except Exception as exc:
            warnings.append(
                "Reminder fields were saved, but Task Activity Log could not be updated: "
                + str(exc)
            )

    return updated_count, warnings


def run_due_reminders(
    repository: TaskRepository,
    config: Mapping[str, Any],
    *,
    today: date | None = None,
    dry_run: bool = False,
) -> ReminderRunResult:
    office_today = today or current_work_date()
    grouped, warnings, due_date = collect_due_tomorrow(
        repository,
        today=office_today,
    )
    result = ReminderRunResult(
        today=office_today,
        due_date=due_date,
        eligible_tasks=sum(len(items) for items in grouped.values()),
        recipient_count=len(grouped),
        skipped_tasks=len(warnings),
        warnings=list(warnings),
    )

    # Dry-run performs all task/recipient eligibility checks but does not call
    # Gmail and does not update Due Reminder Sent For / Sent At.
    if dry_run or not grouped:
        return result

    actor_email = str(config.get("GMAIL_FROM_EMAIL", "")).strip()
    if not actor_email:
        raise RuntimeError("GMAIL_FROM_EMAIL is not configured.")

    for recipient_email, tasks in grouped.items():
        recipient_name = (
            str(tasks[0].get("_recipient_name", "")).strip()
            or recipient_email
        )
        message = build_reminder_message(
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            tasks=tasks,
            due_date=due_date,
            config=config,
        )

        try:
            _send_with_gmail_api(message, config)
        except Exception as exc:
            result.failed_recipients.append(recipient_email)
            result.warnings.append(
                f"Could not send reminder to {recipient_email}: {exc}"
            )
            continue

        # Only mark tasks after Gmail confirms that the send API call succeeded.
        result.sent_emails += 1
        updated_count, update_warnings = _mark_tasks_sent(
            repository,
            tasks=tasks,
            recipient_email=recipient_email,
            due_date=due_date,
            actor_email=actor_email,
        )
        result.updated_tasks += updated_count
        result.warnings.extend(update_warnings)

    return result
