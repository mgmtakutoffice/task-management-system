from __future__ import annotations

import base64
import html
import json
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

from repository import TaskRepository

PENDING_CLIENT_INPUT = "Pending for Client Input"
PENDING_CLIENT_CONFIRMATION = "Pending for Client Confirmation"
PENDING_CLIENT_STATUSES = {
    PENDING_CLIENT_INPUT.lower(),
    PENDING_CLIENT_CONFIRMATION.lower(),
}
TRUE_VALUES = {"yes", "true", "1", "y", "deleted", "archived"}
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"

try:
    OFFICE_TIMEZONE = ZoneInfo("Asia/Kolkata")
except ZoneInfoNotFoundError:
    OFFICE_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="IST")


@dataclass
class PendingClientDigestResult:
    report_date: date
    eligible_tasks: int = 0
    assignee_count: int = 0
    recipient_count: int = 0
    sent_emails: int = 0
    skipped: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.sent_emails > 0 or self.skipped


def current_work_date() -> date:
    return datetime.now(OFFICE_TIMEZONE).date()


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def _status(task: Mapping[str, Any]) -> str:
    return str(task.get("Status", "")).strip()


def _normalize_email_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = str(value or "").split(",")

    seen: set[str] = set()
    result: list[str] = []
    for item in raw_items:
        email = str(item).strip().lower()
        if email and email not in seen:
            seen.add(email)
            result.append(email)
    return result


def _parse_activity_datetime(value: Any) -> datetime | None:
    clean = str(value or "").strip()
    if not clean:
        return None

    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=OFFICE_TIMEZONE)
        return parsed.astimezone(OFFICE_TIMEZONE)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ):
        try:
            parsed = datetime.strptime(clean, fmt).replace(tzinfo=OFFICE_TIMEZONE)
            return parsed
        except ValueError:
            continue
    return None


def _status_since_map(
    activities: list[dict[str, str]],
) -> dict[tuple[str, str], datetime]:
    """Return latest genuine transition time for each task/status pair.

    We require Previous Status != New Status. This prevents later description,
    comment, due-date, or other edits from resetting Status Since while a task
    remains in the same client-pending status.
    """
    latest: dict[tuple[str, str], datetime] = {}

    for activity in activities:
        task_id = str(activity.get("Task ID", "")).strip()
        previous_status = str(activity.get("Previous Status", "")).strip().lower()
        new_status = str(activity.get("New Status", "")).strip().lower()

        if not task_id or new_status not in PENDING_CLIENT_STATUSES:
            continue
        if previous_status == new_status:
            continue

        activity_at = _parse_activity_datetime(activity.get("Activity At", ""))
        if activity_at is None:
            continue

        key = (task_id, new_status)
        existing = latest.get(key)
        if existing is None or activity_at > existing:
            latest[key] = activity_at

    return latest


def collect_pending_client_tasks(
    repository: TaskRepository,
) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    """Collect the two client-pending statuses, grouped person-wise."""
    activities = repository.get_task_activities()
    transition_dates = _status_since_map(activities)

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    warnings: list[str] = []

    for task in repository.get_tasks():
        if _is_true(task.get("Deleted", "")) or _is_true(task.get("Archived", "")):
            continue

        current_status = _status(task)
        normalized_status = current_status.lower()
        if normalized_status not in PENDING_CLIENT_STATUSES:
            continue

        task_id = str(task.get("Task ID", "")).strip()
        assignee_name = str(task.get("Assigned To", "")).strip()
        assignee_email = str(task.get("Assigned To Email", "")).strip().lower()
        assignee_key = assignee_name or assignee_email or "Unassigned"

        status_since_dt = transition_dates.get((task_id, normalized_status))
        status_since_display = (
            status_since_dt.strftime("%d-%b-%Y") if status_since_dt else "—"
        )

        if status_since_dt is None:
            warnings.append(
                f"{task_id or '(no Task ID)'} has no matching Activity Log "
                f"transition for {current_status}; Status Since will show —."
            )

        item = {
            "Task ID": task_id,
            "Client Code": str(task.get("Client Code", "")).strip(),
            "Client Name": str(task.get("Client Name", "")).strip(),
            "Matter / Project": str(task.get("Matter / Project", "")).strip(),
            "Task Description": str(task.get("Task Description", "")).strip(),
            "Status": current_status,
            "Status Since": status_since_display,
            "_status_since_iso": status_since_dt.isoformat() if status_since_dt else "",
            "_assignee_name": assignee_name,
            "_assignee_email": assignee_email,
        }
        grouped[assignee_key].append(item)

    status_order = {
        PENDING_CLIENT_INPUT.lower(): 0,
        PENDING_CLIENT_CONFIRMATION.lower(): 1,
    }

    for items in grouped.values():
        items.sort(
            key=lambda item: (
                status_order.get(str(item.get("Status", "")).lower(), 99),
                # Oldest known pending items first; unknown dates last.
                not bool(item.get("_status_since_iso")),
                str(item.get("_status_since_iso", "")),
                str(item.get("Client Name", "")).lower(),
                str(item.get("Matter / Project", "")).lower(),
                str(item.get("Task ID", "")).lower(),
            )
        )

    return dict(sorted(grouped.items(), key=lambda pair: pair[0].lower())), warnings


def _escape(value: Any) -> str:
    clean = str(value or "").strip()
    return html.escape(clean if clean else "—")


def build_pending_client_digest_message(
    *,
    recipients: list[str],
    grouped_tasks: dict[str, list[dict[str, str]]],
    report_date: date,
    config: Mapping[str, Any],
) -> EmailMessage:
    if not recipients:
        raise RuntimeError("PENDING_CLIENT_DIGEST_RECIPIENTS is not configured.")

    report_display = report_date.strftime("%d-%b-%Y")
    subject = f"Pending Client Tasks Report – {report_display}"

    text_lines = [
        "Dear Team,",
        "",
        f"Please find below the person-wise pending client tasks as on {report_display}.",
        "",
    ]

    html_sections: list[str] = []

    if not grouped_tasks:
        text_lines.append(
            "There are no tasks currently marked Pending for Client Input or "
            "Pending for Client Confirmation."
        )
        html_sections.append(
            "<p>There are no tasks currently marked <strong>Pending for Client Input</strong> "
            "or <strong>Pending for Client Confirmation</strong>.</p>"
        )
    else:
        for assignee, tasks in grouped_tasks.items():
            text_lines.extend([assignee, "-" * len(assignee)])
            rows: list[str] = []

            for index, task in enumerate(tasks, start=1):
                text_lines.extend(
                    [
                        f"{index}. {task.get('Client Code') or '—'} | "
                        f"{task.get('Client Name') or '—'} | "
                        f"{task.get('Matter / Project') or '—'} | "
                        f"{task.get('Task Description') or '—'} | "
                        f"{task.get('Status') or '—'} | "
                        f"{task.get('Status Since') or '—'}"
                    ]
                )

                rows.append(
                    "<tr>"
                    f"<td style=\"padding:7px;border:1px solid #cbd5e1;text-align:center;\">{index}</td>"
                    f"<td style=\"padding:7px;border:1px solid #cbd5e1;\">{_escape(task.get('Client Code'))}</td>"
                    f"<td style=\"padding:7px;border:1px solid #cbd5e1;\">{_escape(task.get('Client Name'))}</td>"
                    f"<td style=\"padding:7px;border:1px solid #cbd5e1;\">{_escape(task.get('Matter / Project'))}</td>"
                    f"<td style=\"padding:7px;border:1px solid #cbd5e1;\">{_escape(task.get('Task Description'))}</td>"
                    f"<td style=\"padding:7px;border:1px solid #cbd5e1;white-space:nowrap;\">{_escape(task.get('Status'))}</td>"
                    f"<td style=\"padding:7px;border:1px solid #cbd5e1;white-space:nowrap;\">{_escape(task.get('Status Since'))}</td>"
                    "</tr>"
                )

            text_lines.append("")
            html_sections.append(
                f"<h3 style=\"margin:22px 0 8px;color:#17243d;\">{html.escape(assignee)}</h3>"
                "<table style=\"border-collapse:collapse;width:100%;max-width:1200px;font-size:13px;\">"
                "<thead><tr style=\"background:#17243d;color:#fff;\">"
                "<th style=\"padding:7px;border:1px solid #cbd5e1;\">Sr.</th>"
                "<th style=\"padding:7px;border:1px solid #cbd5e1;text-align:left;\">Client Code</th>"
                "<th style=\"padding:7px;border:1px solid #cbd5e1;text-align:left;\">Client Name</th>"
                "<th style=\"padding:7px;border:1px solid #cbd5e1;text-align:left;\">Matter / Project</th>"
                "<th style=\"padding:7px;border:1px solid #cbd5e1;text-align:left;\">Task Description</th>"
                "<th style=\"padding:7px;border:1px solid #cbd5e1;text-align:left;\">Status</th>"
                "<th style=\"padding:7px;border:1px solid #cbd5e1;text-align:left;\">Status Since</th>"
                f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            )

    task_manager_url = str(config.get("TASK_MANAGER_URL", "")).strip().rstrip("/")
    if task_manager_url:
        text_lines.extend(["", f"Task Manager: {task_manager_url}"])

    text_lines.extend(["", "Regards,", "Anand Akut & Associates"])

    link_html = ""
    if task_manager_url:
        link_html = (
            '<p style="margin-top:20px;"><a href="'
            + html.escape(task_manager_url, quote=True)
            + '" style="display:inline-block;padding:9px 14px;'
            'background:#1d4ed8;color:#fff;text-decoration:none;'
            'border-radius:6px;font-weight:700;">Open Task Manager</a></p>'
        )

    html_body = f"""\
<html>
  <body style="font-family:Arial,sans-serif;color:#17243d;line-height:1.45;">
    <p>Dear Team,</p>
    <p>Please find below the person-wise pending client tasks as on <strong>{html.escape(report_display)}</strong>.</p>
    {''.join(html_sections)}
    {link_html}
    <p style="margin-top:22px;">Regards,<br><strong>Anand Akut &amp; Associates</strong></p>
  </body>
</html>
"""

    from_name = str(
        config.get("GMAIL_FROM_NAME", "Anand Akut & Associates")
    ).strip()
    from_email = str(config.get("GMAIL_FROM_EMAIL", "")).strip()
    if not from_email:
        raise RuntimeError("GMAIL_FROM_EMAIL is not configured.")

    message = EmailMessage()
    message["From"] = formataddr((from_name, from_email))
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content("\n".join(text_lines))
    message.add_alternative(html_body, subtype="html")
    return message


def _gmail_credentials(config: Mapping[str, Any]) -> Credentials:
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

    if credentials.expired:
        if not credentials.refresh_token:
            raise RuntimeError(
                "Google OAuth token has expired and no refresh token is available."
            )
        try:
            credentials.refresh(Request())
        except Exception as exc:
            raise RuntimeError(
                f"Could not refresh the Google OAuth access token: {exc}"
            ) from exc

    if not credentials.valid:
        raise RuntimeError("Google OAuth credentials are not valid.")
    return credentials


def _send_with_gmail_api(message: EmailMessage, config: Mapping[str, Any]) -> str:
    credentials = _gmail_credentials(config)
    service = build(
        "gmail",
        "v1",
        credentials=credentials,
        cache_discovery=False,
    )
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    result = (
        service.users()
        .messages()
        .send(userId="me", body={"raw": encoded_message})
        .execute()
    )
    return str(result.get("id", ""))


def run_pending_client_digest(
    repository: TaskRepository,
    config: Mapping[str, Any],
    *,
    report_date: date | None = None,
    dry_run: bool = False,
    enforce_schedule_day: bool = True,
) -> PendingClientDigestResult:
    office_date = report_date or current_work_date()
    result = PendingClientDigestResult(report_date=office_date)

    if enforce_schedule_day and office_date.day not in {1, 11, 21}:
        result.skipped = True
        result.warnings.append(
            "Digest skipped because the office date is not the 1st, 11th, or 21st."
        )
        return result

    recipients = _normalize_email_list(
        config.get("PENDING_CLIENT_DIGEST_RECIPIENTS", ())
    )
    if not recipients:
        raise RuntimeError("PENDING_CLIENT_DIGEST_RECIPIENTS is not configured.")

    grouped, warnings = collect_pending_client_tasks(repository)
    result.eligible_tasks = sum(len(tasks) for tasks in grouped.values())
    result.assignee_count = len(grouped)
    result.recipient_count = len(recipients)
    result.warnings.extend(warnings)

    if dry_run:
        return result

    message = build_pending_client_digest_message(
        recipients=recipients,
        grouped_tasks=grouped,
        report_date=office_date,
        config=config,
    )
    _send_with_gmail_api(message, config)
    result.sent_emails = 1
    return result
