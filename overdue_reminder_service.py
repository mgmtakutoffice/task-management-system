from __future__ import annotations

import html
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Mapping

from config import TASK_ACTIVITY_HEADERS, TASK_HEADERS
from due_reminder_service import (
    DATETIME_FORMAT,
    OFFICE_TIMEZONE,
    _is_active,
    _is_archived,
    _is_deleted,
    _recipient_for_task,
    _send_with_gmail_api,
    _task_status,
    current_work_date,
    parse_stored_date,
)
from repository import TaskRepository

COMPLETED_STATUS = "completed"
PENDING_APPROVAL_STATUS = "pending completion approval"
SYSTEM_ACTOR_NAME = "Overdue Reminder Service"


@dataclass
class OverdueRunResult:
    today: date
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


def collect_overdue_tasks(
    repository: TaskRepository,
    *,
    today: date | None = None,
) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    """Collect overdue task responsibilities, grouped into one digest per active user.

    A task becomes overdue on the day after its Due Date. Completed, archived,
    deleted and Pending Completion Approval tasks are excluded. Pending for
    Checking is routed to the checker; other ongoing statuses are routed to the
    assignee, matching the existing due-tomorrow reminder ownership rules.

    The same task may be included again on the next calendar day while it remains
    overdue, but Overdue Reminder Sent On prevents duplicate mail if the job is
    run more than once on the same day.
    """
    office_today = today or current_work_date()
    today_key = office_today.isoformat()

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
        if due_date is None:
            # Tasks with no valid due date cannot be classified as overdue.
            continue

        if due_date >= office_today:
            continue

        if str(task.get("Overdue Reminder Sent On", "")).strip() == today_key:
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
        item["_days_overdue"] = str((office_today - due_date).days)
        item["_parsed_due_date"] = due_date.isoformat()
        grouped[email].append(item)

    for items in grouped.values():
        items.sort(
            key=lambda item: (
                str(item.get("Priority", "")).strip().lower() != "urgent",
                str(item.get("_parsed_due_date", "")),
                str(item.get("Client Name", "")).strip().lower(),
                str(item.get("Matter / Project", "")).strip().lower(),
                str(item.get("Task ID", "")).strip().lower(),
            )
        )

    return dict(grouped), warnings


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


def build_overdue_message(
    *,
    recipient_email: str,
    recipient_name: str,
    tasks: list[dict[str, str]],
    today: date,
    config: Mapping[str, Any],
) -> EmailMessage:
    count = len(tasks)
    subject = f"Overdue Task Reminder – {count} Overdue Task{'s' if count != 1 else ''}"
    task_manager_url = str(config.get("TASK_MANAGER_URL", "")).strip().rstrip("/")
    my_tasks_url = f"{task_manager_url}/my-tasks" if task_manager_url else ""

    text_lines = [
        f"Dear {recipient_name},",
        "",
        (
            "The following task assigned to your current responsibility is overdue:"
            if count == 1
            else "The following tasks assigned to your current responsibility are overdue:"
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
        due_date = parse_stored_date(task.get("Due Date", ""))
        due_display = due_date.strftime("%d-%b-%Y") if due_date else str(task.get("Due Date", ""))
        days_overdue = str(task.get("_days_overdue", "")).strip() or "1"

        text_lines.extend(
            [
                f"{index}. {summary}",
                f"   Task ID: {task_id}",
                f"   Responsibility: {responsibility}",
                f"   Status: {status}",
                f"   Priority: {priority}",
                f"   Due Date: {due_display}",
                f"   Days Overdue: {days_overdue}",
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
            f'<td style="font-weight:700;color:#b91c1c;">{html.escape(days_overdue)}</td>'
            "</tr>"
        )

    text_lines.append(
        "Please review the overdue tasks and update their status in the Task Manager."
    )
    if my_tasks_url:
        text_lines.extend(["", f"My Tasks: {my_tasks_url}"])
    text_lines.extend(["", "Anand Akut & Associates"])

    link_html = ""
    if my_tasks_url:
        link_html = (
            '<p><a href="'
            + html.escape(my_tasks_url, quote=True)
            + '" style="display:inline-block;padding:9px 14px;'
            'background:#b91c1c;color:#fff;text-decoration:none;'
            'border-radius:6px;font-weight:700;">Open My Tasks</a></p>'
        )

    html_body = f"""\
<html>
  <body style="font-family:Arial,sans-serif;color:#17243d;line-height:1.45;">
    <p>Dear {html.escape(recipient_name)},</p>
    <p style="color:#b91c1c;font-weight:700;">{'The following task is overdue:' if count == 1 else 'The following tasks are overdue:'}</p>
    <table style="border-collapse:collapse;width:100%;max-width:1050px;">
      <thead>
        <tr style="background:#991b1b;color:#fff;">
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Task ID</th>
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Task</th>
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Responsibility</th>
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Status</th>
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Priority</th>
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Due Date</th>
          <th style="padding:8px;border:1px solid #cbd5e1;text-align:left;">Days Overdue</th>
        </tr>
      </thead>
      <tbody>{''.join(html_rows)}</tbody>
    </table>
    <p>Please review the overdue tasks and update their status in the Task Manager.</p>
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


def _activity_row(
    *,
    task: Mapping[str, Any],
    recipient_email: str,
    responsibility: str,
    today: date,
    actor_email: str,
    activity_at: str,
) -> dict[str, str]:
    due_date = parse_stored_date(task.get("Due Date", ""))
    days_overdue = (today - due_date).days if due_date else 0

    row = {header: "" for header in TASK_ACTIVITY_HEADERS}
    row.update(
        {
            "Activity ID": uuid.uuid4().hex,
            "Task ID": str(task.get("Task ID", "")).strip(),
            "Activity Type": "Overdue Reminder Email Sent",
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
                f"Due Date: {due_date.isoformat() if due_date else str(task.get('Due Date', ''))}; "
                f"Days Overdue: {days_overdue}; "
                f"Responsibility: {responsibility}; "
                "Reminder Type: Daily overdue digest; "
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
    today: date,
    actor_email: str,
) -> tuple[int, list[str]]:
    sent_at = datetime.now(OFFICE_TIMEZONE).strftime(DATETIME_FORMAT)
    activity_at = datetime.now(OFFICE_TIMEZONE).isoformat(timespec="seconds")
    today_key = today.isoformat()
    updated_count = 0
    activities: list[dict[str, str]] = []
    warnings: list[str] = []

    for task in tasks:
        task_id = str(task.get("Task ID", "")).strip()
        updated = {
            header: str(task.get(header, ""))
            for header in TASK_HEADERS
        }
        updated["Overdue Reminder Sent On"] = today_key
        updated["Overdue Reminder Sent At"] = sent_at

        try:
            repository.update_task(task_id, updated)
        except Exception as exc:
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
                today=today,
                actor_email=actor_email,
                activity_at=activity_at,
            )
        )

    if activities:
        try:
            repository.add_task_activities(activities)
        except Exception as exc:
            warnings.append(
                "Overdue reminder fields were saved, but Task Activity Log could not be updated: "
                + str(exc)
            )

    return updated_count, warnings


def run_overdue_reminders(
    repository: TaskRepository,
    config: Mapping[str, Any],
    *,
    today: date | None = None,
    dry_run: bool = False,
) -> OverdueRunResult:
    office_today = today or current_work_date()
    grouped, warnings = collect_overdue_tasks(
        repository,
        today=office_today,
    )
    result = OverdueRunResult(
        today=office_today,
        eligible_tasks=sum(len(items) for items in grouped.values()),
        recipient_count=len(grouped),
        skipped_tasks=len(warnings),
        warnings=list(warnings),
    )

    # Dry-run performs all eligibility checks but does not call Gmail and does
    # not update Overdue Reminder Sent On / Sent At.
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
        message = build_overdue_message(
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            tasks=tasks,
            today=office_today,
            config=config,
        )

        try:
            _send_with_gmail_api(message, config)
        except Exception as exc:
            result.failed_recipients.append(recipient_email)
            result.warnings.append(
                f"Could not send overdue reminder to {recipient_email}: {exc}"
            )
            continue

        # Only mark tasks after Gmail confirms that the send API call succeeded.
        result.sent_emails += 1
        updated_count, update_warnings = _mark_tasks_sent(
            repository,
            tasks=tasks,
            recipient_email=recipient_email,
            today=office_today,
            actor_email=actor_email,
        )
        result.updated_tasks += updated_count
        result.warnings.extend(update_warnings)

    return result
