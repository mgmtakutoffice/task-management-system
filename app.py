from __future__ import annotations

import hmac
import json
import os
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from flask import (  # noqa: E402
    Flask,
    flash,
    g,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash  # noqa: E402

from config import (  # noqa: E402
    Config,
    DEFAULT_PRIORITIES,
    DEFAULT_STATUSES,
    TASK_HEADERS,
)
from repository import (  # noqa: E402
    RepositoryError,
    TaskRepository,
    build_repository,
)

DATE_FORMAT = "%Y-%m-%d"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
COMPLETED_STATUS = "Completed"
PENDING_APPROVAL_STATUS = "Pending Completion Approval"
REJECTED_STATUS = "Completion Rejected"
APPROVAL_PENDING = "Pending"
APPROVAL_APPROVED = "Approved"
APPROVAL_SELF_APPROVED = "Self Approved"
APPROVAL_REJECTED = "Rejected"
PENDING_CHECKING_STATUS = "Pending for Checking"
CHANGES_REQUIRED_STATUS = "Changes Required"
CLIENT_INPUT_STATUS = "Pending for Client Input"
CLIENT_CONFIRMATION_STATUS = "Pending for Client Confirmation"
LEGACY_PENDING_CLIENT_STATUS = "Pending for Client"
CHECKING_PENDING = "Pending"
CHECKING_CHECKED = "Checked"
CHECKING_CHANGES_REQUIRED = "Changes Required"
NOTIFICATION_NEW_TASK = "New Task"
NOTIFICATION_AUTHORISED_TODAY = "Authorised Today"
NOTIFICATION_CHECKING_ASSIGNMENT = "Checking Assignment"
NOTIFICATION_CHECKING_AUTHORISED_TODAY = "Checking Authorised Today"
NOTIFICATION_CHECKING_ACCEPTED = "Checking Accepted"
NOTIFICATION_CHECKING_CHANGES_REQUIRED = "Checking Changes Required"
AUTHORISED_TODAY_BLOCKED_ASSIGNED_STATUSES = {
    PENDING_CHECKING_STATUS.lower(),
    PENDING_APPROVAL_STATUS.lower(),
    COMPLETED_STATUS.lower(),
}
INACTIVE_VALUES = {"no", "false", "0", "inactive"}
try:
    ACTIVITY_TIMEZONE = ZoneInfo("Asia/Kolkata")
except ZoneInfoNotFoundError:
    # Windows may not include the IANA time-zone database. India does not
    # observe daylight-saving time, so UTC+05:30 is a safe fallback.
    ACTIVITY_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="IST")
ACTIVITY_COMPARE_FIELDS = [
    "Client Code",
    "Client Name",
    "Entity Name",
    "Matter / Project",
    "Task Description",
    "Assigned By",
    "Assigned Date",
    "Priority",
    "Additional Details",
    "Reference Link",
    "Deleted",
]


def create_app(config_override: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_override:
        app.config.update(config_override)

    try:
        repository = build_repository(app.config)
    except RepositoryError as exc:
        repository = None
        app.config["STARTUP_ERROR"] = str(exc)

    app.extensions["task_repository"] = repository

    def repo() -> TaskRepository:
        current = app.extensions.get("task_repository")
        if current is None:
            raise RepositoryError(
                app.config.get("STARTUP_ERROR", "Repository is unavailable.")
            )
        return current

    def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not session.get("user_email"):
                flash("Please sign in to continue.", "warning")
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)

        return wrapped

    def editor_required(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        @login_required
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not is_task_editor():
                flash("Only authorised users can access this page.", "warning")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped

    def is_active(value: str) -> bool:
        return str(value or "Yes").strip().lower() not in INACTIVE_VALUES

    def unique_ordered(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean = str(value).strip()
            key = clean.lower()
            if clean and key not in seen:
                result.append(clean)
                seen.add(key)
        return result

    def request_cached(key: str, loader: Callable[[], Any]) -> Any:
        """Cache read-only repository results for the current HTTP request.

        Several pages use the same task/user/client data in both the route and
        template context processor. Reusing the object for that one request
        prevents duplicate Google responses and duplicate Python lists from
        existing in memory at the same time. The cache is automatically
        discarded by Flask when the request ends, so it cannot become stale
        across different user actions.
        """
        if not has_request_context():
            return loader()
        cache = getattr(g, "_tms_request_cache", None)
        if cache is None:
            cache = {}
            g._tms_request_cache = cache
        if key not in cache:
            cache[key] = loader()
        return cache[key]

    def active_users() -> list[dict[str, str]]:
        def load() -> list[dict[str, str]]:
            return sorted(
                [
                    user
                    for user in repo().get_users()
                    if user.get("Email", "").strip()
                    and is_active(user.get("Active", "Yes"))
                ],
                key=lambda user: (
                    user.get("Name", "").strip().lower(),
                    user.get("Email", "").strip().lower(),
                ),
            )

        return request_cached("active_users", load)

    def active_clients() -> list[dict[str, str]]:
        def load() -> list[dict[str, str]]:
            return sorted(
                [
                    client
                    for client in repo().get_clients()
                    if client.get("Client Code", "").strip()
                    and client.get("Client Name", "").strip()
                    and is_active(client.get("Active", "Yes"))
                ],
                key=lambda client: (
                    client.get("Client Code", "").strip().lower(),
                    client.get("Client Name", "").strip().lower(),
                ),
            )

        return request_cached("active_clients", load)

    def masters() -> dict[str, list[str]]:
        def load() -> dict[str, list[str]]:
            data = repo().get_masters()
            user_names = [
                user.get("Name", "").strip()
                for user in active_users()
                if user.get("Name", "").strip()
            ]
            client_names = [
                client.get("Client Name", "").strip()
                for client in active_clients()
                if client.get("Client Name", "").strip()
            ]
            data["Associates"] = sorted(
                set(data.get("Associates", []) + user_names),
                key=str.lower,
            )
            data["Clients"] = sorted(
                set(data.get("Clients", []) + client_names),
                key=str.lower,
            )
            # Always include workflow statuses even when the Masters sheet was
            # created before newer statuses were added. The former single
            # "Pending for Client" value is deliberately hidden from normal UI
            # choices; historical tasks carrying it remain readable/editable.
            master_statuses = [
                item
                for item in data.get("Status", [])
                if str(item).strip().lower()
                != LEGACY_PENDING_CLIENT_STATUS.lower()
            ]
            data["Status"] = unique_ordered(master_statuses + DEFAULT_STATUSES)
            data["Priority"] = unique_ordered(
                list(data.get("Priority", [])) + DEFAULT_PRIORITIES
            )
            return data

        return request_cached("masters", load)

    def current_user() -> dict[str, str]:
        return {
            "name": session.get("user_name", ""),
            "email": session.get("user_email", ""),
        }

    def current_user_email() -> str:
        return str(session.get("user_email", "")).strip().lower()

    def current_work_date() -> date:
        """Return the current office date in India time."""
        return datetime.now(ACTIVITY_TIMEZONE).date()

    def focus_date_value() -> str:
        return current_work_date().strftime(DATE_FORMAT)

    def assigned_today_focus(task: dict[str, Any]) -> bool:
        return (
            str(task.get("Today Focus Date", "")).strip()
            == focus_date_value()
        )

    def checking_today_focus(task: dict[str, Any]) -> bool:
        return (
            str(task.get("Checking Today Focus Date", "")).strip()
            == focus_date_value()
        )

    def assigned_authorised_today(task: dict[str, Any]) -> bool:
        """Return True only when the assignee may have an active blue Today flag."""
        current_status = str(task.get("Status", "")).strip().lower()
        return (
            current_status not in AUTHORISED_TODAY_BLOCKED_ASSIGNED_STATUSES
            and str(task.get("Authorised Today Date", "")).strip()
            == focus_date_value()
        )

    def checking_authorised_today(task: dict[str, Any]) -> bool:
        """Return True only while the separate checking responsibility is pending."""
        return (
            is_pending_checking(task)
            and str(
                task.get(
                    "Checking Authorised Today Date",
                    "",
                )
            ).strip()
            == focus_date_value()
        )

    def clear_assigned_authorised_today(
        task: dict[str, str],
    ) -> None:
        """Clear the assignee's blue Today fields after the workflow moves on."""
        task["Authorised Today Date"] = ""
        task["Authorised Today By"] = ""
        task["Authorised Today At"] = ""

    def clear_checking_authorised_today(
        task: dict[str, str],
    ) -> None:
        """Clear the checker's blue Today fields when checking finishes."""
        task["Checking Authorised Today Date"] = ""
        task["Checking Authorised Today By"] = ""
        task["Checking Authorised Today At"] = ""

    def activity_timestamp() -> str:
        """Return a sortable timezone-aware audit timestamp in India time."""
        return datetime.now(ACTIVITY_TIMEZONE).isoformat(timespec="seconds")

    def task_activity(
        *,
        task_id: str,
        activity_type: str,
        previous: dict[str, str] | None = None,
        updated: dict[str, str] | None = None,
        checking_outcome: str = "",
        comment: str = "",
        additional_information: str = "",
    ) -> dict[str, str]:
        previous = previous or {}
        updated = updated or {}
        user = current_user()
        return {
            "Activity ID": uuid.uuid4().hex,
            "Task ID": task_id,
            "Activity Type": activity_type,
            "Previous Status": previous.get("Status", ""),
            "New Status": updated.get("Status", ""),
            "Updated By": user.get("name", "") or user.get("email", ""),
            "Updated By Email": user.get("email", ""),
            "Activity At": activity_timestamp(),
            "Previous Assignee": previous.get("Assigned To", ""),
            "New Assignee": updated.get("Assigned To", ""),
            "Previous Due Date": previous.get("Due Date", ""),
            "New Due Date": updated.get("Due Date", ""),
            "Checker Name": (
                updated.get("Checker Name", "")
                or previous.get("Checker Name", "")
            ),
            "Checking Outcome": checking_outcome,
            "Comment": comment,
            "Additional Information": additional_information,
        }

    def save_task_activities(activities: list[dict[str, str]]) -> None:
        """Append audit rows without reversing a task change already saved."""
        if not activities:
            return
        try:
            repo().add_task_activities(activities)
        except Exception:
            app.logger.exception("Unable to append Task Activity Log rows")
            flash(
                "The task was saved, but its activity log could not be "
                "recorded. Please inform the administrator.",
                "warning",
            )

    def browser_push_configured() -> bool:
        return bool(
            str(app.config.get("VAPID_PUBLIC_KEY", "")).strip()
            and str(app.config.get("VAPID_PRIVATE_KEY", "")).strip()
            and str(app.config.get("VAPID_SUBJECT", "")).strip()
        )

    def send_browser_push(
        user_email: str, notification: dict[str, str]
    ) -> None:
        """Send one Web Push message to every active browser for the user.

        Push delivery is best-effort. A task action and its in-app notification
        remain saved even when a browser endpoint is offline or expired.
        """
        recipient = str(user_email).strip().lower()
        if not recipient or not browser_push_configured():
            return

        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            app.logger.exception(
                "pywebpush is not installed; browser push is disabled"
            )
            return

        try:
            subscriptions = repo().get_push_subscriptions(recipient)
        except Exception:
            app.logger.exception(
                "Unable to load browser push subscriptions for %s", recipient
            )
            return

        if not subscriptions:
            return

        notification_id = notification.get("Notification ID", "").strip()
        open_url = (
            url_for("open_notification", notification_id=notification_id)
            if notification_id
            else url_for("dashboard")
        )
        payload = json.dumps(
            {
                "notification_id": notification_id,
                "type": notification.get("Type", ""),
                "task_id": notification.get("Task ID", ""),
                "title": notification.get("Title", "Task notification"),
                "message": notification.get("Message", ""),
                "created_at": notification.get("Created At", ""),
                "open_url": open_url,
            },
            ensure_ascii=False,
        )

        current_origin = request.host_url.rstrip("/")
        for subscription in subscriptions:
            subscription_origin = subscription.get("Origin", "").strip().rstrip("/")
            if subscription_origin and subscription_origin != current_origin:
                continue
            endpoint = subscription.get("Endpoint", "").strip()
            p256dh = subscription.get("P256DH", "").strip()
            auth = subscription.get("Auth", "").strip()
            if not endpoint or not p256dh or not auth:
                continue
            try:
                webpush(
                    subscription_info={
                        "endpoint": endpoint,
                        "keys": {"p256dh": p256dh, "auth": auth},
                    },
                    data=payload,
                    vapid_private_key=app.config["VAPID_PRIVATE_KEY"],
                    vapid_claims={"sub": app.config["VAPID_SUBJECT"]},
                    ttl=300,
                    timeout=5,
                )
            except WebPushException as exc:
                response = getattr(exc, "response", None)
                status_code = getattr(response, "status_code", None)
                if status_code in {404, 410}:
                    try:
                        repo().deactivate_push_subscription(
                            endpoint,
                            recipient,
                            datetime.now(ACTIVITY_TIMEZONE).strftime(
                                DATETIME_FORMAT
                            ),
                        )
                    except Exception:
                        app.logger.exception(
                            "Unable to deactivate expired push endpoint for %s",
                            recipient,
                        )
                app.logger.warning(
                    "Browser push failed for %s with status %s: %s",
                    recipient,
                    status_code,
                    exc,
                )
            except Exception:
                app.logger.exception(
                    "Unexpected browser push failure for %s", recipient
                )

    def notification_task_label(task: dict[str, str]) -> str:
        client = (
            task.get("Client Name", "").strip()
            or task.get("Client Code", "").strip()
            or "Task"
        )
        matter = task.get("Matter / Project", "").strip()
        description = task.get("Task Description", "").strip()
        parts = [client]
        if matter:
            parts.append(matter)
        label = " - ".join(parts)
        if description:
            label = f"{label}: {description}"
        return label[:240]

    def add_notification(
        *,
        user_email: str,
        notification_type: str,
        task: dict[str, str],
        title: str,
        message: str,
    ) -> None:
        """Save an in-app notification without undoing the task action."""
        recipient = str(user_email).strip().lower()
        if not recipient:
            return
        actor = current_user()
        notification = {
            "Notification ID": uuid.uuid4().hex,
            "User Email": recipient,
            "Type": notification_type,
            "Task ID": task.get("Task ID", ""),
            "Title": title,
            "Message": message,
            "Created By": actor.get("name", "") or actor.get("email", ""),
            "Created At": datetime.now(ACTIVITY_TIMEZONE).strftime(
                DATETIME_FORMAT
            ),
            "Read": "No",
            "Read At": "",
        }
        try:
            repo().add_notification(notification)
        except Exception:
            # Notifications are useful but must never reverse or duplicate a
            # task action that has already been saved successfully.
            app.logger.exception(
                "Unable to create notification %s for %s",
                notification_type,
                recipient,
            )
            return

        # The Notifications sheet remains the durable history. Browser push is
        # the immediate delivery channel and is intentionally best-effort.
        send_browser_push(recipient, notification)

    def checking_assignment_was_created(
        previous: dict[str, str],
        updated: dict[str, str],
    ) -> bool:
        return bool(
            is_pending_checking(updated)
            and updated.get("Checker Email", "").strip()
            and (
                previous.get("Checker Email", "").strip().lower()
                != updated.get("Checker Email", "").strip().lower()
                or previous.get("Submitted for Checking At", "").strip()
                != updated.get("Submitted for Checking At", "").strip()
                or not is_pending_checking(previous)
            )
        )

    def creation_activity_rows(task: dict[str, str]) -> list[dict[str, str]]:
        rows = [
            task_activity(
                task_id=task["Task ID"],
                activity_type="Task Created",
                updated=task,
                additional_information="Initial task record created.",
            ),
            task_activity(
                task_id=task["Task ID"],
                activity_type="Task Assigned",
                updated=task,
                additional_information=(
                    f"Assigned email: {task.get('Assigned To Email', '')}; "
                    f"Assigned date: {task.get('Assigned Date', '')}"
                ),
            ),
        ]
        if task.get("Due Date", "").strip():
            rows.append(
                task_activity(
                    task_id=task["Task ID"],
                    activity_type="Due Date Assigned",
                    updated=task,
                    additional_information=(
                        f"Due date assigned at task creation: "
                        f"{task.get('Due Date', '')}"
                    ),
                )
            )
        return rows

    def update_activity_rows(
        previous: dict[str, str],
        updated: dict[str, str],
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        task_id = updated.get("Task ID", "") or previous.get("Task ID", "")

        previous_assignee_email = previous.get(
            "Assigned To Email", ""
        ).strip().lower()
        new_assignee_email = updated.get(
            "Assigned To Email", ""
        ).strip().lower()
        if previous_assignee_email != new_assignee_email:
            rows.append(
                task_activity(
                    task_id=task_id,
                    activity_type=(
                        "Task Reassigned"
                        if previous_assignee_email
                        else "Task Assigned"
                    ),
                    previous=previous,
                    updated=updated,
                    additional_information=(
                        f"Previous email: {previous_assignee_email}; "
                        f"New email: {new_assignee_email}"
                    ),
                )
            )

        if previous.get("Assigned Date", "") != updated.get(
            "Assigned Date", ""
        ):
            rows.append(
                task_activity(
                    task_id=task_id,
                    activity_type="Assigned Date Changed",
                    previous=previous,
                    updated=updated,
                    additional_information=(
                        f"Assigned date changed from "
                        f"{previous.get('Assigned Date', '') or 'blank'} to "
                        f"{updated.get('Assigned Date', '') or 'blank'}."
                    ),
                )
            )

        previous_due = previous.get("Due Date", "").strip()
        new_due = updated.get("Due Date", "").strip()
        if previous_due != new_due:
            if not previous_due and new_due:
                due_activity = "Due Date Assigned"
            elif previous_due and not new_due:
                due_activity = "Due Date Removed"
            else:
                due_activity = "Due Date Changed"
            rows.append(
                task_activity(
                    task_id=task_id,
                    activity_type=due_activity,
                    previous=previous,
                    updated=updated,
                )
            )

        previous_status = previous.get("Status", "").strip()
        new_status = updated.get("Status", "").strip()
        if previous_status.lower() != new_status.lower():
            if new_status.lower() == PENDING_CHECKING_STATUS.lower():
                activity_type = "Submitted for Checking"
                additional_information = (
                    f"Checker email: {updated.get('Checker Email', '')}"
                )
            elif new_status.lower() == PENDING_APPROVAL_STATUS.lower():
                activity_type = "Completion Submitted"
                additional_information = (
                    "Task submitted for authorised completion approval."
                )
            elif (
                new_status.lower() == COMPLETED_STATUS.lower()
                and updated.get("Completion Approval Status", "").lower()
                == APPROVAL_SELF_APPROVED.lower()
            ):
                activity_type = "Completion Self Approved"
                additional_information = (
                    "Task completed directly by the authorised assignee."
                )
            else:
                activity_type = "Status Changed"
                additional_information = (
                    f"Status changed from {previous_status or 'blank'} "
                    f"to {new_status or 'blank'}."
                )
            rows.append(
                task_activity(
                    task_id=task_id,
                    activity_type=activity_type,
                    previous=previous,
                    updated=updated,
                    comment=updated.get("Additional Details", "").strip(),
                    additional_information=additional_information,
                )
            )

        if previous.get("Deleted", "") != updated.get("Deleted", ""):
            deleted_now = updated.get("Deleted", "").strip().lower() in {
                "yes", "true", "1", "deleted"
            }
            rows.append(
                task_activity(
                    task_id=task_id,
                    activity_type=(
                        "Task Deleted" if deleted_now else "Task Restored"
                    ),
                    previous=previous,
                    updated=updated,
                )
            )

        changed_fields = [
            field
            for field in ACTIVITY_COMPARE_FIELDS
            if str(previous.get(field, "")) != str(updated.get(field, ""))
        ]
        already_logged_fields = {"Assigned Date", "Deleted"}
        descriptive_changes = [
            field for field in changed_fields if field not in already_logged_fields
        ]
        if descriptive_changes:
            rows.append(
                task_activity(
                    task_id=task_id,
                    activity_type="Task Updated",
                    previous=previous,
                    updated=updated,
                    additional_information=(
                        "Changed fields: " + ", ".join(descriptive_changes)
                    ),
                )
            )
        return rows

    def is_task_editor() -> bool:
        editor_emails = {
            str(email).strip().lower()
            for email in app.config.get("TASK_EDITOR_EMAILS", ())
            if str(email).strip()
        }
        return bool(current_user_email() and current_user_email() in editor_emails)

    def is_billing_admin() -> bool:
        billing_admin_email = str(
            app.config.get("BILLING_ADMIN_EMAIL", "")
        ).strip().lower()
        return bool(
            billing_admin_email
            and current_user_email()
            and current_user_email() == billing_admin_email
        )

    def is_pending_for_billing(task: dict[str, Any]) -> bool:
        return str(task.get("Pending for Billing", "")).strip().lower() in {
            "yes", "true", "1"
        }

    def is_billing_completed(task: dict[str, Any]) -> bool:
        return str(task.get("Billing Completed", "")).strip().lower() in {
            "yes", "true", "1"
        }

    def can_mark_pending_billing(task: dict[str, Any]) -> bool:
        """Allow the assignee or any authorised user to raise billing."""
        if (
            is_deleted(task)
            or is_archived_task(task)
            or not is_completed(task)
            or is_billing_completed(task)
        ):
            return False
        return bool(
            is_task_editor()
            or is_billing_admin()
            or task_assigned_to_current_user(task)
        )

    def ongoing_return_endpoint() -> str:
        """Return the appropriate active-task page for the signed-in user."""
        return "ongoing_tasks" if is_task_editor() else "my_tasks"

    def is_deleted(task: dict[str, str]) -> bool:
        return task.get("Deleted", "").strip().lower() in {
            "yes",
            "true",
            "1",
            "deleted",
        }

    def task_assigned_to_current_user(task: dict[str, Any]) -> bool:
        assigned_email = str(task.get("Assigned To Email", "")).strip().lower()
        return bool(current_user_email() and assigned_email == current_user_email())

    def task_checker_is_current_user(task: dict[str, Any]) -> bool:
        checker_email = str(task.get("Checker Email", "")).strip().lower()
        return bool(current_user_email() and checker_email == current_user_email())

    def is_completed(task: dict[str, str]) -> bool:
        return task.get("Status", "").strip().lower() == COMPLETED_STATUS.lower()

    def is_pending_approval(task: dict[str, str]) -> bool:
        return (
            task.get("Status", "").strip().lower()
            == PENDING_APPROVAL_STATUS.lower()
        )

    def checking_status_value(task: dict[str, Any]) -> str:
        return str(task.get("Checking Status", "")).strip().lower()

    def has_checking_assignment(task: dict[str, Any]) -> bool:
        checker_recorded = bool(
            str(task.get("Checker Email", "")).strip()
            or str(task.get("Checker Name", "")).strip()
        )
        return bool(
            checker_recorded
            and checking_status_value(task)
            in {
                CHECKING_PENDING.lower(),
                CHECKING_CHECKED.lower(),
                CHECKING_CHANGES_REQUIRED.lower(),
            }
        )

    def is_pending_checking(task: dict[str, str]) -> bool:
        return (
            task.get("Status", "").strip().lower()
            == PENDING_CHECKING_STATUS.lower()
            and checking_status_value(task) == CHECKING_PENDING.lower()
        )

    def is_completed_checking(task: dict[str, Any]) -> bool:
        return bool(
            has_checking_assignment(task)
            and checking_status_value(task)
            in {CHECKING_CHECKED.lower(), CHECKING_CHANGES_REQUIRED.lower()}
        )

    def can_review_checking(task: dict[str, Any]) -> bool:
        return bool(is_pending_checking(task) and (is_task_editor() or task_checker_is_current_user(task)))

    def can_update_task(task: dict[str, Any]) -> bool:
        # Authorised users may edit every task, except that a checking decision
        # should be recorded through the dedicated checking-review page.
        if is_task_editor():
            return True
        if is_completed(task) or is_pending_approval(task) or is_pending_checking(task):
            return False
        return task_assigned_to_current_user(task)

    def parse_date(value: str) -> date | None:
        if not value:
            return None
        try:
            return datetime.strptime(value.strip(), DATE_FORMAT).date()
        except ValueError:
            return None

    def date_value_matches_month(value: Any, selected_month: str) -> bool:
        """Return True when a stored date/datetime falls in YYYY-MM.

        Google Sheets normally stores these values in ISO format, so the
        prefix check handles both YYYY-MM-DD and YYYY-MM-DD HH:MM:SS.
        The additional formats keep the filter useful for older sheet data.
        """
        month = str(selected_month or "").strip()
        if not month:
            return True
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            return True

        clean = str(value or "").strip()
        if not clean:
            return False
        if clean.startswith(month):
            return True

        for date_format in (
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%d-%b-%Y",
            "%d %b %Y",
        ):
            try:
                return datetime.strptime(clean, date_format).strftime("%Y-%m") == month
            except ValueError:
                continue
        return False

    def parse_stored_date(value: Any) -> date | None:
        """Parse a stored task date or datetime into a date value."""
        clean = str(value or "").strip()
        if not clean:
            return None

        # Current task dates/timestamps begin with YYYY-MM-DD.
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
        ):
            try:
                return datetime.strptime(clean, stored_format).date()
            except ValueError:
                continue
        return None

    def is_archived_task(task: dict[str, Any]) -> bool:
        return (
            str(task.get("Archived", "")).strip().lower()
            in {"yes", "true", "1"}
        )

    def archive_cutoff_date(today_value: date | None = None) -> date:
        """Return the first day of the month two full months ago.

        Example: any day in August 2026 returns 1 June 2026, so tasks
        completed on or before 31 May 2026 are eligible for archiving.
        """
        current = today_value or current_work_date()
        month_number = current.year * 12 + current.month - 1 - 2
        cutoff_year, cutoff_month_zero = divmod(month_number, 12)
        return date(cutoff_year, cutoff_month_zero + 1, 1)

    def task_completion_date(task: dict[str, Any]) -> date | None:
        value = (
            task.get("Completion Approved At", "")
            or task.get("Completion Date", "")
        )
        return parse_stored_date(value)

    def eligible_for_archive(task: dict[str, Any]) -> bool:
        if (
            is_deleted(task)
            or not is_completed(task)
            or is_archived_task(task)
            or is_pending_for_billing(task)
        ):
            return False
        completed_on = task_completion_date(task)
        return bool(
            completed_on
            and completed_on < archive_cutoff_date()
        )

    def is_overdue(task: dict[str, str]) -> bool:
        due = parse_date(task.get("Due Date", ""))
        return bool(due and due < date.today() and not is_completed(task))

    def pending_approval_tasks() -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]]:
            pending = [
                task
                for task in enriched_tasks()
                if is_pending_approval(task)
                and task.get("Completion Approval Status", "").strip().lower()
                in {"", APPROVAL_PENDING.lower()}
            ]
            pending.sort(
                key=lambda task: (
                    task.get("Completion Submitted At", ""),
                    task.get("Task ID", ""),
                )
            )
            return pending

        return request_cached("pending_approval_tasks", load)

    def enriched_tasks() -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for task in repo().get_tasks():
                if is_deleted(task):
                    continue
                copy: dict[str, Any] = dict(task)
                due = parse_date(task.get("Due Date", ""))
                completion = parse_date(task.get("Completion Date", ""))
                copy["_due_date"] = due
                copy["_completion_date"] = completion
                copy["_is_completed"] = is_completed(task)
                copy["_is_archived"] = is_archived_task(task)
                copy["_is_pending_approval"] = is_pending_approval(task)
                copy["_is_pending_checking"] = is_pending_checking(task)
                copy["_is_overdue"] = is_overdue(task)
                copy["_is_today_focus"] = assigned_today_focus(task)
                copy["_is_checking_today_focus"] = checking_today_focus(task)
                copy["_is_authorised_today"] = assigned_authorised_today(task)
                copy["_is_checking_authorised_today"] = (
                    checking_authorised_today(task)
                )
                if completion and due:
                    if completion < due:
                        copy["_completion_result"] = "Completed before due date"
                    elif completion == due:
                        copy["_completion_result"] = "Completed on time"
                    else:
                        copy["_completion_result"] = "Completed late"
                else:
                    copy["_completion_result"] = "Completed"
                rows.append(copy)
            return rows

        return request_cached("enriched_tasks", load)

    def ongoing_sort_key(task: dict[str, Any]) -> tuple[bool, date, str]:
        due = task.get("_due_date")
        return (
            due is None,
            due or date.max,
            str(task.get("Task Description", "")).lower(),
        )

    def completed_sort_key(task: dict[str, Any]) -> tuple[date, str]:
        return (
            task.get("_completion_date") or date.min,
            task.get("Task ID", ""),
        )

    def apply_filters(
        tasks: list[dict[str, Any]],
        *,
        include_checker: bool = False,
    ) -> list[dict[str, Any]]:
        query = request.args.get("q", "").strip().lower()
        assigned_to = request.args.get("assigned_to", "").strip().lower()
        status = request.args.get("status", "").strip().lower()
        priority = request.args.get("priority", "").strip().lower()
        client = request.args.get("client", "").strip().lower()
        assignment_month = request.args.get("assignment_month", "").strip()
        overdue_only = request.args.get("overdue") == "1"
        due_today_only = request.args.get("due_today") == "1"

        filtered: list[dict[str, Any]] = []
        for task in tasks:
            searchable = " ".join(
                [
                    str(task.get("Task ID", "")),
                    str(task.get("Client Code", "")),
                    str(task.get("Client Name", "")),
                    str(task.get("Entity Name", "")),
                    str(task.get("Matter / Project", "")),
                    str(task.get("Task Description", "")),
                    str(task.get("Assigned To", "")),
                    str(task.get("Checker Name", "")),
                    str(task.get("Status", "")),
                ]
            ).lower()
            client_searchable = " ".join(
                [
                    str(task.get("Client Code", "")),
                    str(task.get("Client Name", "")),
                    str(task.get("Entity Name", "")),
                ]
            ).lower()
            if query and query not in searchable:
                continue
            if assigned_to:
                assigned_matches = (
                    task.get("Assigned To", "").strip().lower() == assigned_to
                )
                checker_matches = (
                    include_checker
                    and task.get("Checker Name", "").strip().lower() == assigned_to
                    and has_checking_assignment(task)
                )
                if not (assigned_matches or checker_matches):
                    continue
            if status and task.get("Status", "").strip().lower() != status:
                continue
            if priority and task.get("Priority", "").strip().lower() != priority:
                continue
            if client and client not in client_searchable:
                continue
            if assignment_month and not date_value_matches_month(
                task.get("Assigned Date", ""), assignment_month
            ):
                continue
            if overdue_only and not task.get("_is_overdue"):
                continue
            if due_today_only and task.get("_due_date") != current_work_date():
                continue
            filtered.append(task)
        return filtered

    def normal_statuses(for_new_task: bool = False) -> list[str]:
        # Completion Rejected is assigned only by the system when an
        # authorised user rejects a completion request. It must never be
        # available as a manually selectable status on Add or Update Task.
        excluded = {
            PENDING_APPROVAL_STATUS.lower(),
            COMPLETED_STATUS.lower(),
            PENDING_CHECKING_STATUS.lower(),
            CHANGES_REQUIRED_STATUS.lower(),
            REJECTED_STATUS.lower(),
        }
        return [
            item
            for item in masters().get("Status", [])
            if item.strip().lower() not in excluded
        ]

    def status_options_for_form(
        task: dict[str, str] | None,
    ) -> list[dict[str, str]]:
        options = [
            {"value": item, "label": item}
            for item in normal_statuses(for_new_task=task is None)
        ]
        if task is None:
            return options

        current_status = task.get("Status", "").strip()
        if (
            current_status
            and current_status.lower() != REJECTED_STATUS.lower()
            and current_status.lower() not in {
                item["value"].lower() for item in options
            }
        ):
            label = {
                PENDING_APPROVAL_STATUS.lower(): "Awaiting Completion Approval",
                PENDING_CHECKING_STATUS.lower(): "Awaiting Checking",
                LEGACY_PENDING_CLIENT_STATUS.lower(): (
                    "Pending for Client (Legacy - choose Input or Confirmation)"
                ),
            }.get(current_status.lower(), current_status)
            options.append({"value": current_status, "label": label})

        # The original assignee (and authorised users) can choose either
        # workflow: submit for checking first, or send directly for final
        # completion approval when a separate checker is not required.
        if task_assigned_to_current_user(task) or is_task_editor():
            options.extend(
                [
                    {
                        "value": PENDING_CHECKING_STATUS,
                        "label": "Submit for Checking",
                    },
                    {
                        "value": PENDING_APPROVAL_STATUS,
                        "label": "Submit for Completion Approval",
                    },
                ]
            )

        # Preserve the earlier rule: authorised users may directly complete
        # only tasks assigned to their own login email.
        if is_task_editor() and task_assigned_to_current_user(task):
            options.append({"value": COMPLETED_STATUS, "label": COMPLETED_STATUS})

        output: list[dict[str, str]] = []
        seen: set[str] = set()
        for option in options:
            key = option["value"].lower()
            if key not in seen:
                output.append(option)
                seen.add(key)
        return output

    def validate_task_form(form: Any) -> list[str]:
        errors: list[str] = []
        required_fields = {
            "client_code": "Client",
            "task_description": "Task description",
            "assigned_to_email": "Assigned associate",
            "assigned_date": "Assigned date",
            "priority": "Priority",
            "status": "Status",
        }
        for field, label in required_fields.items():
            if not form.get(field, "").strip():
                errors.append(f"{label} is required.")

        selected_code = form.get("client_code", "").strip().lower()
        client_codes = {
            client.get("Client Code", "").strip().lower()
            for client in active_clients()
        }
        if selected_code and selected_code not in client_codes:
            errors.append("Please select a valid active client.")

        assigned = parse_date(form.get("assigned_date", ""))
        due = parse_date(form.get("due_date", ""))
        completion = parse_date(form.get("completion_date", ""))
        if form.get("assigned_date") and not assigned:
            errors.append("Assigned date must be a valid date.")
        if form.get("due_date") and not due:
            errors.append("Due date must be a valid date.")
        if form.get("completion_date") and not completion:
            errors.append("Completion date must be a valid date.")

        selected_status = form.get("status", "").strip().lower()
        if selected_status == PENDING_CHECKING_STATUS.lower():
            checker_email = form.get("checker_email", "").strip().lower()
            assigned_email = form.get("assigned_to_email", "").strip().lower()
            if not checker_email:
                errors.append("Please select a checker before submitting for checking.")
            elif checker_email == assigned_email:
                errors.append("The checker must be different from the assigned user.")
        return errors

    def validate_limited_update(form: Any) -> list[str]:
        errors: list[str] = []
        status = form.get("status", "").strip()
        if not status:
            errors.append("Status is required.")
        if status.lower() == PENDING_CHECKING_STATUS.lower():
            checker_email = form.get("checker_email", "").strip().lower()
            if not checker_email:
                errors.append("Please select a checker before submitting for checking.")
            if checker_email == current_user_email():
                errors.append("The checker must be a different user.")
        return errors

    def selected_checker() -> tuple[str, str]:
        checker_email = request.form.get("checker_email", "").strip().lower()
        users_by_email = {
            user.get("Email", "").strip().lower(): user
            for user in active_users()
        }
        checker = users_by_email.get(checker_email, {})
        return checker.get("Name", "").strip(), checker_email

    def task_from_form(existing: dict[str, str] | None = None) -> dict[str, str]:
        existing = existing or {}
        user = current_user()
        users_by_email = {
            item.get("Email", "").strip().lower(): item
            for item in active_users()
        }
        assigned_email = request.form.get("assigned_to_email", "").strip().lower()
        assigned_user = users_by_email.get(assigned_email, {})
        assigned_name = assigned_user.get("Name", "").strip()

        clients_by_code = {
            item.get("Client Code", "").strip().lower(): item
            for item in active_clients()
        }
        client_code = request.form.get("client_code", "").strip()
        selected_client = clients_by_code.get(client_code.lower(), {})
        now = datetime.now().strftime(DATETIME_FORMAT)

        task = {header: existing.get(header, "") for header in TASK_HEADERS}
        task.update(
            {
                "Client Code": client_code,
                "Client Name": selected_client.get("Client Name", "").strip(),
                "Entity Name": selected_client.get("Entity Name", "").strip(),
                "Matter / Project": request.form.get("matter_project", "").strip(),
                "Task Description": request.form.get("task_description", "").strip(),
                "Assigned To": assigned_name,
                "Assigned To Email": assigned_email,
                "Assigned By": request.form.get("assigned_by", "").strip()
                or user["name"],
                "Assigned Date": request.form.get("assigned_date", "").strip(),
                "Due Date": (
                    request.form.get("due_date", "").strip()
                    if not existing or is_task_editor()
                    else existing.get("Due Date", "")
                ),
                "Priority": request.form.get("priority", "Normal").strip(),
                "Status": request.form.get("status", "Yet to Start").strip(),
                # These columns remain in the repository/Google Sheet but are
                # currently hidden from the Add and Update Task forms. Preserve
                # existing values whenever the fields are absent from the POST.
                "Progress Remarks": request.form.get(
                    "progress_remarks", existing.get("Progress Remarks", "")
                ).strip(),
                "Pending Reason": request.form.get(
                    "pending_reason", existing.get("Pending Reason", "")
                ).strip(),
                "Additional Details": request.form.get(
                    "additional_details", existing.get("Additional Details", "")
                ).strip(),
                "Reference Link": request.form.get(
                    "reference_link", existing.get("Reference Link", "")
                ).strip(),
                "Last Updated By": user["name"] or user["email"],
                "Last Updated At": now,
                "Deleted": existing.get("Deleted", "No") or "No",
            }
        )
        if not existing:
            task.update(
                {
                    "Task ID": (
                        f"TSK-{date.today():%Y%m%d}-"
                        f"{uuid.uuid4().hex[:6].upper()}"
                    ),
                    "Created By": user["name"] or user["email"],
                    "Created At": now,
                    "Completion Date": "",
                    "Completion Submitted By": "",
                    "Completion Submitted At": "",
                    "Completion Approval Status": "",
                    "Completion Approved By": "",
                    "Completion Approved At": "",
                    "Completion Rejection Reason": "",
                    "Checker Name": "",
                    "Checker Email": "",
                    "Checking Status": "",
                    "Submitted for Checking By": "",
                    "Submitted for Checking At": "",
                    "Checking Completed By": "",
                    "Checking Completed At": "",
                    "Checking Comment": "",
                    "Changes Required Comment": "",
                }
            )
        return task

    def submit_for_checking(
        existing: dict[str, str],
        updated: dict[str, str],
    ) -> dict[str, str]:
        checker_name, checker_email = selected_checker()
        assigned_email = (
            request.form.get("assigned_to_email", "").strip().lower()
            or str(existing.get("Assigned To Email", "")).strip().lower()
        )
        if not checker_email:
            raise RepositoryError("Please select a checker before submitting for checking.")
        if checker_email == assigned_email:
            raise RepositoryError("The checker must be different from the assigned user.")
        if not checker_name:
            raise RepositoryError("Please select a valid active checker.")

        user = current_user()
        now = datetime.now().strftime(DATETIME_FORMAT)
        updated.update(
            {
                "Status": PENDING_CHECKING_STATUS,
                "Checker Name": checker_name,
                "Checker Email": checker_email,
                "Checking Status": CHECKING_PENDING,
                "Submitted for Checking By": user["name"] or user["email"],
                "Submitted for Checking At": now,
                "Checking Completed By": "",
                "Checking Completed At": "",
                "Checking Comment": "",
                "Changes Required Comment": "",
                "Completion Date": "",
                "Completion Submitted By": "",
                "Completion Submitted At": "",
                "Completion Approval Status": "",
                "Completion Approved By": "",
                "Completion Approved At": "",
                "Completion Rejection Reason": "",
            }
        )
        clear_assigned_authorised_today(updated)
        return updated

    def submit_for_completion_approval(
        existing: dict[str, str],
        updated: dict[str, str],
    ) -> dict[str, str]:
        """Send a task directly to authorised-user completion approval.

        This route is used when a separate checking assignment is not
        required. Any earlier completed checking details are preserved so
        they can continue to appear in the checker history.
        """
        user = current_user()
        now = datetime.now().strftime(DATETIME_FORMAT)
        updated.update(
            {
                "Status": PENDING_APPROVAL_STATUS,
                "Completion Date": "",
                "Completion Submitted By": user["name"] or user["email"],
                "Completion Submitted At": now,
                "Completion Approval Status": APPROVAL_PENDING,
                "Completion Approved By": "",
                "Completion Approved At": "",
                "Completion Rejection Reason": "",
            }
        )
        clear_assigned_authorised_today(updated)
        return updated

    def apply_editor_workflow(
        existing: dict[str, str],
        updated: dict[str, str],
    ) -> dict[str, str]:
        status = updated.get("Status", "").strip()
        now = datetime.now().strftime(DATETIME_FORMAT)
        user = current_user()

        if status.lower() == PENDING_CHECKING_STATUS.lower():
            return submit_for_checking(existing, updated)

        if status.lower() == COMPLETED_STATUS.lower():
            if not task_assigned_to_current_user(existing):
                raise RepositoryError(
                    "An authorised user may directly complete only a task "
                    "assigned to their own email. Other tasks must pass "
                    "through checking."
                )
            completion_date = request.form.get("completion_date", "").strip()
            updated["Completion Date"] = (
                completion_date or date.today().strftime(DATE_FORMAT)
            )
            updated["Completion Approval Status"] = APPROVAL_SELF_APPROVED
            updated["Completion Approved By"] = user["name"] or user["email"]
            updated["Completion Approved At"] = now
            updated["Completion Rejection Reason"] = ""
            clear_assigned_authorised_today(updated)
            clear_checking_authorised_today(updated)
            return updated

        if status.lower() == PENDING_APPROVAL_STATUS.lower():
            # Do not resubmit an already locked approval request merely because
            # an authorised user opened and saved its edit form.
            if is_pending_approval(existing):
                updated["Status"] = PENDING_APPROVAL_STATUS
                updated["Completion Date"] = ""
                updated["Completion Approval Status"] = (
                    existing.get("Completion Approval Status", "")
                    or APPROVAL_PENDING
                )
                return updated
            return submit_for_completion_approval(existing, updated)

        updated["Completion Date"] = ""
        if is_completed(existing):
            updated["Completion Approval Status"] = ""
            updated["Completion Approved By"] = ""
            updated["Completion Approved At"] = ""
        return updated

    def limited_update_from_form(existing: dict[str, str]) -> dict[str, str]:
        user = current_user()
        now = datetime.now().strftime(DATETIME_FORMAT)
        updated = {header: existing.get(header, "") for header in TASK_HEADERS}
        requested_status = request.form.get(
            "status", existing.get("Status", "Yet to Start")
        ).strip()

        # Ordinary users may either submit the task for checking or send it
        # directly for authorised completion approval. A forged Completed value
        # is safely converted to completion approval rather than direct completion.
        if requested_status.lower() == PENDING_CHECKING_STATUS.lower():
            updated = submit_for_checking(existing, updated)
        elif requested_status.lower() in {
            PENDING_APPROVAL_STATUS.lower(),
            COMPLETED_STATUS.lower(),
        }:
            updated = submit_for_completion_approval(existing, updated)
        else:
            updated["Status"] = requested_status
            updated["Completion Date"] = ""

        submitted_description = request.form.get("task_description")
        if submitted_description is not None and submitted_description.strip():
            updated["Task Description"] = submitted_description.strip()

        updated.update(
            {
                # Progress Remarks and Pending Reason remain hidden. Preserve
                # their existing values when the fields are absent from POST.
                "Progress Remarks": request.form.get(
                    "progress_remarks", existing.get("Progress Remarks", "")
                ).strip(),
                "Pending Reason": request.form.get(
                    "pending_reason", existing.get("Pending Reason", "")
                ).strip(),
                # Additional Details is intentionally available on Update Task
                # for all users as the task-level update comment/note.
                "Additional Details": request.form.get(
                    "additional_details", existing.get("Additional Details", "")
                ).strip(),
                "Last Updated By": user["name"] or user["email"],
                "Last Updated At": now,
            }
        )
        return updated

    def get_task(task_id: str) -> dict[str, str] | None:
        return next(
            (
                row
                for row in repo().get_tasks()
                if row.get("Task ID") == task_id and not is_deleted(row)
            ),
            None,
        )

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        approval_count = 0
        if session.get("user_email") and is_task_editor():
            approval_count = len(pending_approval_tasks())
        return {
            "current_user": current_user(),
            "is_task_editor": is_task_editor(),
            "can_update_task": can_update_task,
            "can_review_checking": can_review_checking,
            "pending_approval_count": approval_count,
            "today_iso": focus_date_value(),
            "push_notifications_configured": browser_push_configured(),
            "vapid_public_key": str(
                app.config.get("VAPID_PUBLIC_KEY", "")
            ).strip(),
            "status_class": lambda status: {
                "yet to start": "status-not-started",
                "in progress": "status-progress",
                "checking accepted": "status-progress",
                "pending for checking": "status-checking",
                "changes required": "status-rejected",
                "changes required after checking": "status-rejected",
                "pending for client input": "status-client-input",
                "pending for client confirmation": "status-client-confirmation",
                # Retained only so historical tasks using the former status
                # still render clearly until they are updated.
                "pending for client": "status-client",
                "pending for estimate approval": "status-estimate-approval",
                "on hold": "status-hold",
                "completion rejected": "status-rejected",
                "pending completion approval": "status-approval",
                "completed": "status-completed",
            }.get(str(status).lower(), "status-neutral"),
        }

    @app.after_request
    def prevent_authenticated_page_caching(response: Any) -> Any:
        """Prevent one signed-in user's pages appearing for the next user.

        This is important on shared office computers and when the browser Back
        button restores a previously authorised page from its cache.
        """
        if session.get("user_email") and request.endpoint != "static":
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0, private"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if session.get("user_email"):
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            try:
                user = next(
                    (
                        item
                        for item in active_users()
                        if item.get("Email", "").strip().lower() == email
                    ),
                    None,
                )
            except RepositoryError as exc:
                flash(str(exc), "danger")
                return render_template("login.html")

            valid = False
            if user:
                stored = user.get("Password Hash", "")
                if stored.startswith(("scrypt:", "pbkdf2:")):
                    valid = check_password_hash(stored, password)
                else:
                    valid = hmac.compare_digest(stored, password)
            if valid and user:
                session.clear()
                session.permanent = True
                session["user_email"] = user.get("Email", "").strip().lower()
                session["user_name"] = user.get("Name", "").strip()
                next_url = request.args.get("next", "")
                return redirect(
                    next_url
                    if next_url.startswith("/") and not next_url.startswith("//")
                    else url_for("dashboard")
                )
            flash("Invalid email or password.", "danger")
        return render_template("login.html")

    @app.route("/logout")
    def logout() -> Any:
        session.clear()
        flash("You have been signed out.", "info")
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def dashboard() -> Any:
        all_tasks = enriched_tasks()
        editor_view = is_task_editor()
        current_month = current_work_date().strftime("%Y-%m")
        today_date = current_work_date()

        if editor_view:
            visible_tasks = all_tasks
            ongoing = [task for task in visible_tasks if not task["_is_completed"]]
            completed = [task for task in visible_tasks if task["_is_completed"]]
            approvals = pending_approval_tasks()
            # Authorised dashboard shows the firm's complete pending-checking
            # queue, not only checking assigned to the logged-in editor.
            pending_checking_count = sum(
                is_pending_checking(task)
                for task in ongoing
            )
            completed_month_count = sum(
                date_value_matches_month(
                    task.get("Completion Date", "")
                    or task.get("Completion Approved At", ""),
                    current_month,
                )
                for task in completed
            )
            metrics = {
                "ongoing": len(ongoing),
                "pending_checking": pending_checking_count,
                "completed_month": completed_month_count,
                "pending_approvals": len(approvals),
            }
        else:
            assigned_ongoing = [
                task
                for task in all_tasks
                if task_assigned_to_current_user(task)
                and not task["_is_completed"]
            ]
            checking_queue = [
                task
                for task in all_tasks
                if task_checker_is_current_user(task)
                and is_pending_checking(task)
            ]
            visible_tasks = [
                task
                for task in all_tasks
                if (
                    task_assigned_to_current_user(task)
                    or task_checker_is_current_user(task)
                )
            ]
            completed = [
                task
                for task in all_tasks
                if task_assigned_to_current_user(task) and task["_is_completed"]
            ]
            approvals = []

            due_today_count = sum(
                task.get("_due_date") == today_date
                for task in assigned_ongoing
            )
            overdue_count = sum(
                bool(task.get("_is_overdue")) for task in assigned_ongoing
            )
            pending_approval_count = sum(
                is_pending_approval(task) for task in assigned_ongoing
            )
            today_focus_count = sum(
                bool(
                    task.get("_is_authorised_today")
                    or task.get("_is_today_focus")
                )
                for task in assigned_ongoing
            ) + sum(
                bool(
                    task.get("_is_checking_authorised_today")
                    or task.get("_is_checking_today_focus")
                )
                for task in checking_queue
            )
            completed_month_count = sum(
                date_value_matches_month(
                    task.get("Completion Date", "")
                    or task.get("Completion Approved At", ""),
                    current_month,
                )
                for task in completed
            )
            metrics = {
                "ongoing": len(assigned_ongoing),
                "due_today": due_today_count,
                "overdue": overdue_count,
                "pending_checking": len(checking_queue),
                "pending_approvals": pending_approval_count,
                "completed_month": completed_month_count,
                "today_focus": today_focus_count,
            }

        associate_workload: list[dict[str, Any]] = []
        if editor_view:
            associate_summary: dict[str, Counter[str]] = defaultdict(Counter)
            for user in active_users():
                name = user.get("Name", "").strip()
                if name:
                    associate_summary[name]

            for task in all_tasks:
                name = str(task.get("Assigned To", "")).strip() or "Unassigned"
                if task["_is_completed"]:
                    if date_value_matches_month(
                        task.get("Completion Date", "")
                        or task.get("Completion Approved At", ""),
                        current_month,
                    ):
                        associate_summary[name]["completed_month"] += 1
                    continue

                associate_summary[name]["ongoing"] += 1
                if task.get("_is_overdue"):
                    # Count overdue assigned responsibility under the assignee.
                    # Pending-checking work is also counted below under the
                    # checker because it is an active checking responsibility.
                    associate_summary[name]["overdue"] += 1

                if is_pending_checking(task):
                    checker_name = str(task.get("Checker Name", "")).strip()
                    if checker_name:
                        associate_summary[checker_name]["checking"] += 1
                        if task.get("_is_overdue"):
                            associate_summary[checker_name]["overdue"] += 1

            maximum_active_workload = max(
                (
                    counts["ongoing"] + counts["checking"]
                    for counts in associate_summary.values()
                ),
                default=0,
            )
            for name, counts in sorted(
                associate_summary.items(), key=lambda item: item[0].lower()
            ):
                active_total = counts["ongoing"] + counts["checking"]
                associate_workload.append(
                    {
                        "name": name,
                        "ongoing": counts["ongoing"],
                        "checking": counts["checking"],
                        "overdue": counts["overdue"],
                        "completed_month": counts["completed_month"],
                        "active_total": active_total,
                        "load_percent": (
                            round(active_total * 100 / maximum_active_workload)
                            if maximum_active_workload
                            else 0
                        ),
                    }
                )

        personal_priority_tasks: list[dict[str, Any]] = []
        personal_pipeline: list[dict[str, Any]] = []
        personal_checking_queue: list[dict[str, Any]] = []
        if not editor_view:
            assigned_ongoing = [
                task
                for task in all_tasks
                if task_assigned_to_current_user(task)
                and not task["_is_completed"]
            ]
            personal_priority_tasks = [
                task
                for task in assigned_ongoing
                if (
                    task.get("_is_authorised_today")
                    or task.get("_is_today_focus")
                    or task.get("_is_overdue")
                    or task.get("_due_date") == today_date
                    or str(task.get("Priority", "")).strip().lower() == "urgent"
                )
            ]
            personal_priority_tasks.sort(
                key=lambda task: (
                    0 if task.get("_is_authorised_today") else 1,
                    0 if task.get("_is_today_focus") else 1,
                    0 if task.get("_is_overdue") else 1,
                    0 if task.get("_due_date") == today_date else 1,
                    0
                    if str(task.get("Priority", "")).strip().lower() == "urgent"
                    else 1,
                    *ongoing_sort_key(task),
                )
            )
            personal_priority_tasks = personal_priority_tasks[:8]

            pipeline_order = [
                "Yet to Start",
                "In Progress",
                "Changes Required",
                CLIENT_INPUT_STATUS,
                CLIENT_CONFIRMATION_STATUS,
                "Pending for Estimate Approval",
                "Pending for Checking",
                "On Hold",
                PENDING_APPROVAL_STATUS,
            ]
            status_counts = Counter(
                str(task.get("Status", "")).strip() for task in assigned_ongoing
            )
            personal_pipeline = [
                {"status": status, "count": status_counts.get(status, 0)}
                for status in pipeline_order
            ]

            personal_checking_queue = [
                task
                for task in all_tasks
                if task_checker_is_current_user(task)
                and is_pending_checking(task)
            ]
            personal_checking_queue.sort(
                key=lambda task: (
                    0
                    if task.get("_is_checking_authorised_today")
                    else 1,
                    0 if task.get("_is_checking_today_focus") else 1,
                    str(task.get("Submitted for Checking At", "")),
                    *ongoing_sort_key(task),
                )
            )
            personal_checking_queue = personal_checking_queue[:8]

        recent = sorted(
            visible_tasks,
            key=lambda item: item.get("Last Updated At", ""),
            reverse=True,
        )[:8]
        return render_template(
            "dashboard.html",
            metrics=metrics,
            associate_workload=associate_workload,
            current_month=current_month,
            recent_tasks=recent,
            pending_approvals=approvals[:6],
            personal_priority_tasks=personal_priority_tasks,
            personal_pipeline=personal_pipeline,
            personal_checking_queue=personal_checking_queue,
        )

    @app.route("/clients/add", methods=["POST"])
    @login_required
    def add_client() -> Any:
        client_code = request.form.get("new_client_code", "").strip().upper()
        client_name = request.form.get("new_client_name", "").strip()
        entity_name = request.form.get("new_entity_name", "").strip()
        if not client_code:
            flash("Client Code is required.", "danger")
            return redirect(url_for("add_task"))
        if not client_name:
            flash("Client Name is required.", "danger")
            return redirect(url_for("add_task"))

        user = current_user()
        client = {
            "Client Code": client_code,
            "Client Name": client_name,
            "Entity Name": entity_name,
            "Active": "Yes",
            "Created By": user["name"] or user["email"],
            "Created At": datetime.now().strftime(DATETIME_FORMAT),
        }
        try:
            repo().add_client(client)
            flash(f"Client {client_code} - {client_name} has been added.", "success")
        except RepositoryError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("add_task"))
        return redirect(url_for("add_task", client_code=client_code))

    @app.route("/tasks/add", methods=["GET", "POST"])
    @login_required
    def add_task() -> Any:
        master_data = masters()
        users = active_users()
        clients = active_clients()
        if request.method == "POST":
            errors = validate_task_form(request.form)
            selected_status = request.form.get("status", "").strip()
            allowed_statuses = {item.lower() for item in normal_statuses(True)}
            if selected_status.lower() not in allowed_statuses:
                errors.append("Please select a valid starting status.")
            if not errors:
                try:
                    task = task_from_form()
                    task["Pending Reason"] = ""
                    repo().add_task(task)
                    save_task_activities(creation_activity_rows(task))
                    add_notification(
                        user_email=task.get("Assigned To Email", ""),
                        notification_type=NOTIFICATION_NEW_TASK,
                        task=task,
                        title="New task assigned",
                        message=(
                            f"A new task has been assigned to you: "
                            f"{notification_task_label(task)}"
                            + (
                                f". Due {task.get('Due Date', '')}."
                                if task.get("Due Date", "").strip()
                                else "."
                            )
                        ),
                    )
                    flash("Task has been added.", "success")
                    return redirect(url_for(ongoing_return_endpoint()))
                except RepositoryError as exc:
                    errors.append(str(exc))
            for error in errors:
                flash(error, "danger")

        return render_template(
            "task_form.html",
            page_title="Add Task",
            submit_label="Add Task",
            task=None,
            users=users,
            masters=master_data,
            clients=clients,
            status_options=status_options_for_form(None),
        )

    @app.route("/update-task")
    @login_required
    def update_task_page() -> Any:
        tasks = enriched_tasks()
        if not is_task_editor():
            tasks = [task for task in tasks if task_assigned_to_current_user(task)]

        # Completed tasks are read-only and belong on the Completed Tasks page,
        # not in the Update Task work queue.
        tasks = [task for task in tasks if not task["_is_completed"]]
        tasks = apply_filters(tasks)
        tasks.sort(key=ongoing_sort_key)
        return render_template(
            "task_list.html",
            page_title="Update Task",
            page_subtitle=(
                "Authorised users may update ongoing tasks. Other users may "
                "update only their assigned tasks that are not awaiting approval."
            ),
            tasks=tasks,
            masters=masters(),
            show_completion=False,
            responsibility_mode=False,
            empty_message="No matching tasks were found.",
        )

    @app.route("/service-worker.js")
    def service_worker() -> Any:
        response = send_from_directory(
            app.static_folder,
            "js/service-worker.js",
            mimetype="application/javascript",
        )
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.route("/api/push/subscribe", methods=["POST"])
    @login_required
    def subscribe_browser_push() -> Any:
        if not browser_push_configured():
            return jsonify(
                {"ok": False, "error": "Browser push is not configured."}
            ), 503

        payload = request.get_json(silent=True) or {}
        subscription = payload.get("subscription", payload)
        if not isinstance(subscription, dict):
            return jsonify({"ok": False, "error": "Invalid subscription."}), 400

        keys = subscription.get("keys", {})
        if not isinstance(keys, dict):
            keys = {}
        endpoint = str(subscription.get("endpoint", "")).strip()
        p256dh = str(keys.get("p256dh", "")).strip()
        auth = str(keys.get("auth", "")).strip()
        if not endpoint or not p256dh or not auth:
            return jsonify(
                {"ok": False, "error": "Incomplete push subscription."}
            ), 400

        now = datetime.now(ACTIVITY_TIMEZONE).strftime(DATETIME_FORMAT)
        record = {
            "Subscription ID": uuid.uuid4().hex,
            "User Email": current_user_email(),
            "Endpoint": endpoint,
            "P256DH": p256dh,
            "Auth": auth,
            "User Agent": request.headers.get("User-Agent", "")[:500],
            "Origin": request.host_url.rstrip("/"),
            "Created At": now,
            "Last Seen At": now,
            "Active": "Yes",
        }
        try:
            # Endpoint is the stable identity of the browser subscription. The
            # repository upsert also reassigns an endpoint to the current login,
            # preventing a shared browser from remaining linked to an old user.
            repo().save_push_subscription(record)
        except Exception:
            app.logger.exception("Unable to save browser push subscription")
            return jsonify({"ok": False}), 503
        return jsonify({"ok": True})

    @app.route("/api/push/unsubscribe", methods=["POST"])
    @login_required
    def unsubscribe_browser_push() -> Any:
        payload = request.get_json(silent=True) or {}
        endpoint = str(payload.get("endpoint", "")).strip()
        if not endpoint:
            return jsonify({"ok": False, "error": "Endpoint is required."}), 400
        try:
            repo().deactivate_push_subscription(
                endpoint,
                current_user_email(),
                datetime.now(ACTIVITY_TIMEZONE).strftime(DATETIME_FORMAT),
            )
        except Exception:
            app.logger.exception("Unable to deactivate browser push subscription")
            return jsonify({"ok": False}), 503
        return jsonify({"ok": True})

    @app.route("/api/notifications/unread")
    @login_required
    def unread_notifications() -> Any:
        try:
            notifications = repo().get_notifications(current_user_email())
        except Exception:
            app.logger.exception("Unable to load notifications")
            return jsonify({"count": 0, "notifications": []}), 503

        unread = [
            row
            for row in notifications
            if str(row.get("Read", "")).strip().lower()
            not in {"yes", "true", "1"}
        ]
        unread.sort(
            key=lambda row: row.get("Created At", ""),
            reverse=True,
        )
        payload = [
            {
                "id": row.get("Notification ID", ""),
                "type": row.get("Type", ""),
                "task_id": row.get("Task ID", ""),
                "title": row.get("Title", "Notification"),
                "message": row.get("Message", ""),
                "created_by": row.get("Created By", ""),
                "created_at": row.get("Created At", ""),
                "open_url": url_for(
                    "open_notification",
                    notification_id=row.get("Notification ID", ""),
                ),
                "read_url": url_for(
                    "mark_notification_read",
                    notification_id=row.get("Notification ID", ""),
                ),
            }
            for row in unread[:12]
            if row.get("Notification ID", "").strip()
        ]
        return jsonify({"count": len(unread), "notifications": payload})

    @app.route(
        "/api/notifications/<notification_id>/read",
        methods=["POST"],
    )
    @login_required
    def mark_notification_read(notification_id: str) -> Any:
        try:
            changed = repo().mark_notification_read(
                notification_id,
                current_user_email(),
                datetime.now(ACTIVITY_TIMEZONE).strftime(DATETIME_FORMAT),
            )
        except Exception:
            app.logger.exception("Unable to mark notification read")
            return jsonify({"ok": False}), 503
        if not changed:
            return jsonify({"ok": False}), 404
        return jsonify({"ok": True})

    @app.route("/notifications/<notification_id>/open")
    @login_required
    def open_notification(notification_id: str) -> Any:
        try:
            notification = next(
                (
                    row
                    for row in repo().get_notifications(current_user_email())
                    if row.get("Notification ID") == notification_id
                ),
                None,
            )
            if not notification:
                flash("Notification was not found.", "warning")
                return redirect(url_for("dashboard"))
            repo().mark_notification_read(
                notification_id,
                current_user_email(),
                datetime.now(ACTIVITY_TIMEZONE).strftime(DATETIME_FORMAT),
            )
        except Exception:
            app.logger.exception("Unable to open notification")
            flash("The notification could not be opened.", "warning")
            return redirect(url_for("dashboard"))

        task_id = notification.get("Task ID", "").strip()
        task = get_task(task_id) if task_id else None
        if not task:
            flash("The related task is no longer available.", "warning")
            return redirect(url_for(ongoing_return_endpoint()))

        notification_type = notification.get("Type", "").strip()
        if notification_type in {
            NOTIFICATION_CHECKING_ASSIGNMENT,
            NOTIFICATION_CHECKING_AUTHORISED_TODAY,
        } and can_review_checking(task):
            return redirect(url_for("review_checking", task_id=task_id))

        if task_assigned_to_current_user(task):
            if is_pending_checking(task):
                return redirect(url_for("view_task", task_id=task_id))
            if can_update_task(task):
                return redirect(url_for("edit_task", task_id=task_id))
            if is_completed(task):
                return redirect(url_for("completed_tasks"))
            return redirect(url_for("my_tasks"))

        if task_checker_is_current_user(task) and can_review_checking(task):
            return redirect(url_for("review_checking", task_id=task_id))

        if is_task_editor():
            return redirect(url_for("view_task", task_id=task_id))

        return redirect(url_for("my_tasks"))

    @app.route("/tasks/<task_id>/view")
    @login_required
    def view_task(task_id: str) -> Any:
        """Read-only task view for an assignee while checking is pending."""
        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for(ongoing_return_endpoint()))

        # This read-only page is primarily for the original assignee while the
        # task is locked for checking. Authorised users may also open the view.
        if not (is_task_editor() or task_assigned_to_current_user(task)):
            flash("You do not have access to view this task.", "warning")
            return redirect(url_for(ongoing_return_endpoint()))

        if not is_task_editor() and not is_pending_checking(task):
            # Once checking is finished, the normal user should use the ordinary
            # Edit page again so that the returned task can be worked on.
            return redirect(url_for("edit_task", task_id=task_id))

        return render_template(
            "task_view.html",
            page_title="Task Details",
            task=task,
        )

    @app.route("/tasks/<task_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_task(task_id: str) -> Any:
        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for("update_task_page"))

        # Completed tasks cannot be reopened through the ordinary Update Task
        # route. Billing actions remain available through their dedicated routes.
        if is_completed(task):
            flash("Completed tasks cannot be updated.", "warning")
            return redirect(url_for("completed_tasks"))

        if not can_update_task(task):
            if is_pending_checking(task) and task_checker_is_current_user(task):
                return redirect(url_for("review_checking", task_id=task_id))
            message = (
                "This task is awaiting completion approval and cannot be changed."
                if is_pending_approval(task)
                else "This task is awaiting checking and is locked for the assigned user."
                if is_pending_checking(task)
                else "You can update only tasks assigned to you."
            )
            flash(message, "warning")
            return redirect(url_for(ongoing_return_endpoint()))

        if request.method == "POST":
            errors = (
                validate_task_form(request.form)
                if is_task_editor()
                else validate_limited_update(request.form)
            )

            requested_status = request.form.get("status", "").strip()
            current_status = task.get("Status", "").strip()
            if (
                requested_status.lower() == REJECTED_STATUS.lower()
                and current_status.lower() != REJECTED_STATUS.lower()
            ):
                errors.append(
                    "Completion Rejected is a system-generated status and "
                    "cannot be selected manually."
                )

            if not errors:
                try:
                    if is_task_editor():
                        updated = task_from_form(task)
                        if is_pending_approval(task):
                            updated["Status"] = PENDING_APPROVAL_STATUS
                        updated = apply_editor_workflow(task, updated)
                    else:
                        updated = limited_update_from_form(task)
                    notify_checker = checking_assignment_was_created(
                        task, updated
                    )
                    repo().update_task(task_id, updated)
                    save_task_activities(update_activity_rows(task, updated))
                    if notify_checker:
                        add_notification(
                            user_email=updated.get("Checker Email", ""),
                            notification_type=NOTIFICATION_CHECKING_ASSIGNMENT,
                            task=updated,
                            title="Task assigned for checking",
                            message=(
                                f"A task has been assigned to you for checking: "
                                f"{notification_task_label(updated)}."
                            ),
                        )
                    if is_pending_checking(updated):
                        flash("Task has been submitted for checking.", "success")
                    elif is_pending_approval(updated):
                        flash("Task is awaiting final completion approval.", "success")
                    else:
                        flash("Task has been updated.", "success")
                    if is_completed(updated):
                        destination = "completed_tasks"
                    else:
                        destination = ongoing_return_endpoint()
                    return redirect(url_for(destination))
                except RepositoryError as exc:
                    errors.append(str(exc))
            for error in errors:
                flash(error, "danger")

        return render_template(
            "task_form.html",
            page_title="Update Task",
            submit_label="Save Changes",
            task=task,
            users=active_users(),
            masters=masters(),
            clients=active_clients(),
            status_options=status_options_for_form(task),
            can_self_complete=(
                is_task_editor() and task_assigned_to_current_user(task)
            ),
        )

    @app.route("/tasks/<task_id>/checking")
    @login_required
    def review_checking(task_id: str) -> Any:
        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for(ongoing_return_endpoint()))
        if not can_review_checking(task):
            flash("This checking assignment is not available to you.", "warning")
            return redirect(url_for(ongoing_return_endpoint()))
        return render_template("checking_review.html", task=task)

    @app.route("/tasks/<task_id>/checking/complete", methods=["POST"])
    @login_required
    def complete_checking(task_id: str) -> Any:
        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for(ongoing_return_endpoint()))
        if not can_review_checking(task):
            flash("This checking assignment is not available to you.", "warning")
            return redirect(url_for(ongoing_return_endpoint()))
        comment = request.form.get("checking_comment", "").strip()
        if not comment:
            flash("Please enter a checking comment.", "danger")
            return redirect(url_for("review_checking", task_id=task_id))

        user = current_user()
        now = datetime.now().strftime(DATETIME_FORMAT)
        updated = {header: task.get(header, "") for header in TASK_HEADERS}
        updated.update(
            {
                "Checking Status": CHECKING_CHECKED,
                "Checking Completed By": user["name"] or user["email"],
                "Checking Completed At": now,
                "Checking Comment": comment,
                "Changes Required Comment": "",
                "Last Updated By": user["name"] or user["email"],
                "Last Updated At": now,
            }
        )

        # Checking acceptance completes only the checker's responsibility.
        # The main task returns to the original assignee as In Progress so the
        # assignee can incorporate the review and later submit it for completion.
        updated.update(
            {
                "Status": "In Progress",
                "Completion Date": "",
                "Completion Approval Status": "",
                "Completion Approved By": "",
                "Completion Approved At": "",
                "Completion Rejection Reason": "",
                "Completion Submitted By": "",
                "Completion Submitted At": "",
            }
        )
        clear_checking_authorised_today(updated)
        message = (
            "Checking completed and accepted. The task has been returned to the "
            "original assignee with status In Progress."
        )

        repo().update_task(task_id, updated)
        save_task_activities(
            [
                task_activity(
                    task_id=task_id,
                    activity_type="Checking Accepted",
                    previous=task,
                    updated=updated,
                    checking_outcome="Accepted",
                    comment=comment,
                    additional_information=(
                        "Checking completed and accepted. The task was returned "
                        "to the original assignee with status In Progress."
                    ),
                )
            ]
        )
        add_notification(
            user_email=updated.get("Assigned To Email", ""),
            notification_type=NOTIFICATION_CHECKING_ACCEPTED,
            task=updated,
            title="Checking accepted",
            message=(
                f"Your task checking has been accepted by "
                f"{user.get('name', '') or user.get('email', '')}: "
                f"{notification_task_label(updated)}."
                + (f" Comment: {comment}" if comment else "")
            ),
        )
        flash(message, "success")
        return redirect(url_for(ongoing_return_endpoint()))

    @app.route("/tasks/<task_id>/checking/return", methods=["POST"])
    @login_required
    def return_checking(task_id: str) -> Any:
        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for(ongoing_return_endpoint()))
        if not can_review_checking(task):
            flash("This checking assignment is not available to you.", "warning")
            return redirect(url_for(ongoing_return_endpoint()))
        reason = request.form.get("changes_required_comment", "").strip()
        if not reason:
            flash("Please enter the changes required.", "danger")
            return redirect(url_for("review_checking", task_id=task_id))

        user = current_user()
        now = datetime.now().strftime(DATETIME_FORMAT)
        updated = {header: task.get(header, "") for header in TASK_HEADERS}
        updated.update(
            {
                "Status": CHANGES_REQUIRED_STATUS,
                "Checking Status": CHECKING_CHANGES_REQUIRED,
                "Checking Completed By": user["name"] or user["email"],
                "Checking Completed At": now,
                "Checking Comment": "",
                "Changes Required Comment": reason,
                "Completion Date": "",
                "Completion Submitted By": "",
                "Completion Submitted At": "",
                "Completion Approval Status": "",
                "Completion Approved By": "",
                "Completion Approved At": "",
                "Completion Rejection Reason": "",
                "Last Updated By": user["name"] or user["email"],
                "Last Updated At": now,
            }
        )
        clear_checking_authorised_today(updated)
        repo().update_task(task_id, updated)
        save_task_activities(
            [
                task_activity(
                    task_id=task_id,
                    activity_type="Checking Changes Required",
                    previous=task,
                    updated=updated,
                    checking_outcome="Changes Required",
                    comment=reason,
                )
            ]
        )
        add_notification(
            user_email=updated.get("Assigned To Email", ""),
            notification_type=NOTIFICATION_CHECKING_CHANGES_REQUIRED,
            task=updated,
            title="Changes required after checking",
            message=(
                f"Your task has been returned for changes by "
                f"{user.get('name', '') or user.get('email', '')}: "
                f"{notification_task_label(updated)}. "
                f"Changes required: {reason}"
            ),
        )
        flash("Task has been returned to the assigned user for changes.", "success")
        return redirect(url_for(ongoing_return_endpoint()))

    @app.route("/completion-approvals")
    @editor_required
    def completion_approvals() -> Any:
        return render_template(
            "completion_approvals.html",
            tasks=pending_approval_tasks(),
        )

    @app.route("/tasks/<task_id>/completion/approve", methods=["POST"])
    @editor_required
    def approve_completion(task_id: str) -> Any:
        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for("completion_approvals"))
        if not is_pending_approval(task):
            flash("This completion request has already been handled.", "info")
            return redirect(url_for("completion_approvals"))

        user = current_user()
        now = datetime.now().strftime(DATETIME_FORMAT)
        updated = {header: task.get(header, "") for header in TASK_HEADERS}
        updated.update(
            {
                "Status": COMPLETED_STATUS,
                "Completion Date": date.today().strftime(DATE_FORMAT),
                "Completion Approval Status": APPROVAL_APPROVED,
                "Completion Approved By": user["name"] or user["email"],
                "Completion Approved At": now,
                "Completion Rejection Reason": "",
                "Last Updated By": user["name"] or user["email"],
                "Last Updated At": now,
            }
        )
        clear_assigned_authorised_today(updated)
        clear_checking_authorised_today(updated)
        repo().update_task(task_id, updated)
        save_task_activities(
            [
                task_activity(
                    task_id=task_id,
                    activity_type="Completion Approved",
                    previous=task,
                    updated=updated,
                    additional_information=(
                        f"Approved by {updated.get('Completion Approved By', '')}."
                    ),
                )
            ]
        )
        flash("Completion has been approved and the task is now completed.", "success")
        return redirect(url_for("completion_approvals"))

    @app.route("/tasks/<task_id>/completion/reject", methods=["POST"])
    @editor_required
    def reject_completion(task_id: str) -> Any:
        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for("completion_approvals"))
        if not is_pending_approval(task):
            flash("This completion request has already been handled.", "info")
            return redirect(url_for("completion_approvals"))

        reason = request.form.get("rejection_reason", "").strip()
        if not reason:
            flash("A rejection reason is required.", "danger")
            return redirect(url_for("completion_approvals"))

        user = current_user()
        now = datetime.now().strftime(DATETIME_FORMAT)
        updated = {header: task.get(header, "") for header in TASK_HEADERS}
        updated.update(
            {
                "Status": REJECTED_STATUS,
                "Completion Date": "",
                "Completion Approval Status": APPROVAL_REJECTED,
                "Completion Approved By": "",
                "Completion Approved At": "",
                "Completion Rejection Reason": reason,
                "Last Updated By": user["name"] or user["email"],
                "Last Updated At": now,
            }
        )
        repo().update_task(task_id, updated)
        save_task_activities(
            [
                task_activity(
                    task_id=task_id,
                    activity_type="Completion Rejected",
                    previous=task,
                    updated=updated,
                    comment=reason,
                )
            ]
        )
        flash("Completion request has been rejected and returned for further work.", "success")
        return redirect(url_for("completion_approvals"))

    @app.route(
        "/tasks/<task_id>/authorised-today",
        methods=["POST"],
    )
    @editor_required
    def toggle_authorised_today(task_id: str) -> Any:
        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for("ongoing_tasks"))

        responsibility = (
            request.form.get("responsibility", "assigned")
            .strip()
            .lower()
        )
        enabled = (
            request.form.get("enabled", "").strip().lower()
            == "yes"
        )

        if responsibility not in {"assigned", "checking"}:
            flash("Please select a valid task responsibility.", "warning")
            return redirect(url_for("ongoing_tasks"))

        if responsibility == "checking":
            # The checking card is allowed even though the underlying task
            # status is Pending for Checking. It is a separate responsibility.
            if not is_pending_checking(task):
                flash(
                    "This checking responsibility is no longer pending.",
                    "warning",
                )
                return redirect(url_for("ongoing_tasks"))
        else:
            current_status = str(task.get("Status", "")).strip().lower()
            if current_status in AUTHORISED_TODAY_BLOCKED_ASSIGNED_STATUSES:
                flash(
                    "Assign for Today is not available for an assigned task "
                    "that is Pending for Checking, Pending Completion Approval, "
                    "or Completed.",
                    "warning",
                )
                return redirect(url_for("ongoing_tasks"))

        updated = {
            header: task.get(header, "")
            for header in TASK_HEADERS
        }
        user = current_user()
        user_name = user.get("name", "") or user.get("email", "")
        today_value = focus_date_value() if enabled else ""
        assigned_at = (
            datetime.now(ACTIVITY_TIMEZONE).strftime(DATETIME_FORMAT)
            if enabled
            else ""
        )

        if responsibility == "checking":
            updated["Checking Authorised Today Date"] = today_value
            updated["Checking Authorised Today By"] = (
                user_name if enabled else ""
            )
            updated["Checking Authorised Today At"] = assigned_at
            activity_type = (
                "Checking Authorised Today Added"
                if enabled
                else "Checking Authorised Today Removed"
            )
        else:
            updated["Authorised Today Date"] = today_value
            updated["Authorised Today By"] = (
                user_name if enabled else ""
            )
            updated["Authorised Today At"] = assigned_at
            activity_type = (
                "Authorised Today Added"
                if enabled
                else "Authorised Today Removed"
            )

        updated["Last Updated By"] = user_name
        updated["Last Updated At"] = (
            datetime.now(ACTIVITY_TIMEZONE).strftime(DATETIME_FORMAT)
        )

        repo().update_task(task_id, updated)
        save_task_activities(
            [
                task_activity(
                    task_id=task_id,
                    activity_type=activity_type,
                    previous=task,
                    updated=updated,
                    additional_information=(
                        f"Responsibility: {responsibility}; "
                        f"authorised Today date: "
                        f"{today_value or 'cleared'}."
                    ),
                )
            ]
        )

        if enabled:
            if responsibility == "checking":
                add_notification(
                    user_email=updated.get("Checker Email", ""),
                    notification_type=NOTIFICATION_CHECKING_AUTHORISED_TODAY,
                    task=updated,
                    title="Checking marked Authorised Today",
                    message=(
                        f"Your checking responsibility has been marked for "
                        f"Today by {user_name}: "
                        f"{notification_task_label(updated)}."
                    ),
                )
            else:
                add_notification(
                    user_email=updated.get("Assigned To Email", ""),
                    notification_type=NOTIFICATION_AUTHORISED_TODAY,
                    task=updated,
                    title="Task marked Authorised Today",
                    message=(
                        f"Your task has been marked for Today by {user_name}: "
                        f"{notification_task_label(updated)}."
                    ),
                )

        return redirect(url_for("ongoing_tasks"))

    @app.route("/tasks/<task_id>/today-focus", methods=["POST"])
    @login_required
    def toggle_today_focus(task_id: str) -> Any:
        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for("my_tasks"))

        responsibility = (
            request.form.get("responsibility", "assigned").strip().lower()
        )
        checked = request.form.get("today", "").strip().lower() == "yes"
        updated = {header: task.get(header, "") for header in TASK_HEADERS}
        user = current_user()
        user_name = user.get("name", "") or user.get("email", "")
        today_value = focus_date_value() if checked else ""

        if responsibility == "checking":
            if not (
                task_checker_is_current_user(task)
                and is_pending_checking(task)
            ):
                flash(
                    "You cannot change another user's checking focus.",
                    "warning",
                )
                return redirect(url_for("my_tasks"))
            updated["Checking Today Focus Date"] = today_value
            updated["Checking Today Focus By"] = user_name if checked else ""
            activity_type = (
                "Checking Today Focus Added"
                if checked
                else "Checking Today Focus Removed"
            )
        else:
            if not (
                task_assigned_to_current_user(task)
                and not is_completed(task)
            ):
                flash(
                    "You cannot change another user's task focus.",
                    "warning",
                )
                return redirect(url_for("my_tasks"))
            updated["Today Focus Date"] = today_value
            updated["Today Focus By"] = user_name if checked else ""
            activity_type = (
                "Today Focus Added"
                if checked
                else "Today Focus Removed"
            )

        updated["Last Updated By"] = user_name
        updated["Last Updated At"] = datetime.now(
            ACTIVITY_TIMEZONE
        ).strftime(DATETIME_FORMAT)

        repo().update_task(task_id, updated)
        save_task_activities(
            [
                task_activity(
                    task_id=task_id,
                    activity_type=activity_type,
                    previous=task,
                    updated=updated,
                    additional_information=(
                        f"Responsibility: {responsibility}; "
                        f"focus date: {today_value or 'cleared'}."
                    ),
                )
            ]
        )
        return redirect(url_for("my_tasks"))

    @app.route("/my-tasks")
    @login_required
    def my_tasks() -> Any:
        # My Tasks contains active primary assignments and checking work that is
        # still pending for the logged-in checker. Completed checking decisions
        # are shown only on the Completed Tasks page.
        source_tasks = apply_filters(enriched_tasks(), include_checker=True)
        items: list[dict[str, Any]] = []

        for task in source_tasks:
            if task_assigned_to_current_user(task) and not task["_is_completed"]:
                item = dict(task)
                item["_display_type"] = "assigned"
                item["_display_label"] = "ASSIGNED"

                # After a checking decision, normal users see a clear
                # responsibility-level display status while the real task status
                # remains workflow-safe (In Progress / Changes Required).
                checking_status = checking_status_value(task)
                if (
                    checking_status == CHECKING_CHECKED.lower()
                    and task.get("Status", "").strip().lower() == "in progress"
                ):
                    item["_responsibility_status"] = "Checking Accepted"
                elif (
                    checking_status == CHECKING_CHANGES_REQUIRED.lower()
                    and task.get("Status", "").strip().lower()
                    == CHANGES_REQUIRED_STATUS.lower()
                ):
                    item["_responsibility_status"] = (
                        "Changes Required After Checking"
                    )
                else:
                    item["_responsibility_status"] = task.get("Status", "")

                item["_can_open"] = can_update_task(task)
                item["_is_today_focus_for_responsibility"] = bool(
                    task.get("_is_today_focus")
                )
                item["_is_authorised_today_for_responsibility"] = bool(
                    task.get("_is_authorised_today")
                )
                item["_authorised_today_by"] = task.get(
                    "Authorised Today By",
                    "",
                )
                items.append(item)

            if task_checker_is_current_user(task) and is_pending_checking(task):
                item = dict(task)
                item["_display_type"] = "checking"
                item["_display_label"] = "FOR CHECKING"
                item["_responsibility_status"] = "Pending for Checking"
                item["_checking_outcome"] = "Pending"
                item["_checking_is_complete"] = False
                item["_can_open"] = can_review_checking(task)
                item["_is_today_focus_for_responsibility"] = bool(
                    task.get("_is_checking_today_focus")
                )
                item["_is_authorised_today_for_responsibility"] = bool(
                    task.get("_is_checking_authorised_today")
                )
                item["_authorised_today_by"] = task.get(
                    "Checking Authorised Today By",
                    "",
                )
                items.append(item)

        responsibility_filter = (
            request.args.get("responsibility", "").strip().lower()
        )
        if responsibility_filter in {"assigned", "checking"}:
            items = [
                item
                for item in items
                if item.get("_display_type") == responsibility_filter
            ]

        if request.args.get("today") == "1":
            items = [
                item
                for item in items
                if (
                    item.get("_is_authorised_today_for_responsibility")
                    or item.get("_is_today_focus_for_responsibility")
                )
            ]

        def my_task_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
            authorised_today_rank = (
                0
                if item.get("_is_authorised_today_for_responsibility")
                else 1
            )
            personal_today_rank = (
                0
                if item.get("_is_today_focus_for_responsibility")
                else 1
            )
            responsibility_rank = (
                0 if item.get("_display_type") == "checking" else 1
            )
            return (
                authorised_today_rank,
                personal_today_rank,
                responsibility_rank,
                *ongoing_sort_key(item),
            )

        items.sort(key=my_task_sort_key)
        return render_template(
            "task_list.html",
            page_title="My Tasks",
            page_subtitle=(
                "Tasks assigned to you and tasks currently pending for your "
                "checking. Your orange Today flag and an authorised blue "
                "Today assignment both place a responsibility at the top for "
                "the current office day."
            ),
            tasks=items,
            masters=masters(),
            show_completion=False,
            responsibility_mode=True,
            empty_message="No assigned tasks or pending checking work were found.",
        )

    @app.route("/ongoing-tasks")
    @editor_required
    def ongoing_tasks() -> Any:
        master_data = masters()

        # The page contains active main tasks under their original assignee and
        # checking assignments that are still pending under the checker.
        # Accepted and returned checking decisions belong only on Completed Tasks.
        tasks = apply_filters(enriched_tasks(), include_checker=True)

        active = active_users()
        name_by_email = {
            user.get("Email", "").strip().lower(): user.get("Name", "").strip()
            for user in active
        }
        advocates: list[str] = []
        for user in active:
            name = user.get("Name", "").strip()
            if name and name not in advocates:
                advocates.append(name)
        for name in master_data.get("Associates", []):
            clean_name = str(name).strip()
            if clean_name and clean_name not in advocates:
                advocates.append(clean_name)
        for task in tasks:
            names: list[str] = []
            if not task["_is_completed"]:
                names.append(str(task.get("Assigned To", "")))
            if is_pending_checking(task):
                names.append(str(task.get("Checker Name", "")))
            for name in names:
                clean_name = name.strip()
                if clean_name and clean_name not in advocates:
                    advocates.append(clean_name)

        selected_advocate = request.args.get("assigned_to", "").strip()
        if selected_advocate:
            advocates = [selected_advocate]
        else:
            advocates.sort(key=str.lower)

        # Count main ongoing tasks only. A completed checking record must not
        # inflate the count when the page is filtered by the checker.
        ongoing_task_count = sum(
            1
            for task in tasks
            if not task["_is_completed"]
            and (
                not selected_advocate
                or str(task.get("Assigned To", "")).strip()
                == selected_advocate
            )
        )

        has_unassigned = any(
            not task["_is_completed"]
            and not str(task.get("Assigned To", "")).strip()
            for task in tasks
        )
        if has_unassigned and not selected_advocate:
            advocates.append("Unassigned")

        items_by_advocate: dict[str, list[dict[str, Any]]] = {
            advocate: [] for advocate in advocates
        }
        assigned_counts = Counter()
        checking_counts = Counter()
        pending_checking_counts = Counter()

        for task in tasks:
            # Completed main tasks belong on the Completed Tasks page.
            if not task["_is_completed"]:
                assigned_name = (
                    str(task.get("Assigned To", "")).strip() or "Unassigned"
                )
                if assigned_name in items_by_advocate:
                    item = dict(task)
                    item["_display_type"] = "assigned"
                    item["_display_label"] = "ASSIGNED"
                    item["_can_open"] = can_update_task(task)
                    item["_is_today_focus_for_responsibility"] = bool(
                        task.get("_is_today_focus")
                    )
                    item["_is_authorised_today_for_responsibility"] = bool(
                        task.get("_is_authorised_today")
                    )
                    item["_authorised_today_by"] = task.get(
                        "Authorised Today By",
                        "",
                    )
                    items_by_advocate[assigned_name].append(item)
                    assigned_counts[assigned_name] += 1

            # Only pending checking work appears in the checker's ongoing column.
            if is_pending_checking(task):
                checker_name = str(task.get("Checker Name", "")).strip()
                if not checker_name:
                    checker_email = str(
                        task.get("Checker Email", "")
                    ).strip().lower()
                    checker_name = name_by_email.get(checker_email, "") or checker_email
                if checker_name in items_by_advocate:
                    item = dict(task)
                    item["_display_type"] = "checking"
                    item["_display_label"] = "FOR CHECKING"
                    item["_checking_outcome"] = "Pending"
                    item["_checking_is_complete"] = False
                    item["_can_open"] = can_review_checking(task)
                    item["_is_today_focus_for_responsibility"] = bool(
                        task.get("_is_checking_today_focus")
                    )
                    item["_is_authorised_today_for_responsibility"] = bool(
                        task.get("_is_checking_authorised_today")
                    )
                    item["_authorised_today_by"] = task.get(
                        "Checking Authorised Today By",
                        "",
                    )
                    items_by_advocate[checker_name].append(item)
                    checking_counts[checker_name] += 1
                    pending_checking_counts[checker_name] += 1

        def item_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
            # Authorised blue Today assignments come first, followed by the
            # user's own orange Today focus selections.
            authorised_today_rank = (
                0
                if item.get("_is_authorised_today_for_responsibility")
                else 1
            )
            personal_today_rank = (
                0
                if item.get("_is_today_focus_for_responsibility")
                else 1
            )
            # Pending checking work is shown before ordinary assigned tasks.
            responsibility_rank = (
                0 if item.get("_display_type") == "checking" else 1
            )
            changes_rank = (
                0
                if item.get("Status", "").strip().lower()
                == CHANGES_REQUIRED_STATUS.lower()
                else 1
            )
            urgent_rank = (
                0 if item.get("Priority", "").strip().lower() == "urgent" else 1
            )
            return (
                authorised_today_rank,
                personal_today_rank,
                responsibility_rank,
                changes_rank,
                urgent_rank,
                *ongoing_sort_key(item),
            )

        for advocate in advocates:
            items_by_advocate[advocate].sort(key=item_sort_key)

        advocate_counts = {
            advocate: len(items_by_advocate.get(advocate, []))
            for advocate in advocates
        }
        max_item_count = max(advocate_counts.values(), default=0)
        task_rows: list[list[dict[str, Any] | None]] = []
        for row_number in range(max_item_count):
            row: list[dict[str, Any] | None] = []
            for advocate in advocates:
                advocate_items = items_by_advocate.get(advocate, [])
                row.append(
                    advocate_items[row_number]
                    if row_number < len(advocate_items)
                    else None
                )
            task_rows.append(row)

        return render_template(
            "ongoing_tasks.html",
            advocates=advocates,
            advocate_counts=advocate_counts,
            assigned_counts=assigned_counts,
            checking_counts=checking_counts,
            pending_checking_counts=pending_checking_counts,
            task_rows=task_rows,
            total_tasks=ongoing_task_count,
            total_checking=sum(checking_counts.values()),
            total_pending_checking=sum(pending_checking_counts.values()),
            masters=master_data,
        )

    def render_completed_register(*, archive_view: bool) -> Any:
        master_data = masters()
        completed_month = request.args.get("completed_month", "").strip()
        billing_filter = request.args.get("billing_status", "").strip().lower()
        editor_view = is_task_editor()
        signed_in_email = current_user_email()

        all_task_rows = enriched_tasks()
        register_tasks = [
            task
            for task in all_task_rows
            if bool(task.get("_is_archived")) == archive_view
        ]

        # Security boundary: authorised users may see all matching completed
        # responsibilities. Ordinary users may see only responsibilities that
        # belong to their login email.
        if editor_view:
            source_tasks = register_tasks
        else:
            source_tasks = []
            for task in register_tasks:
                assigned_email = str(
                    task.get("Assigned To Email", "")
                ).strip().lower()
                checker_email = str(
                    task.get("Checker Email", "")
                ).strip().lower()

                if signed_in_email and (
                    assigned_email == signed_in_email
                    or checker_email == signed_in_email
                ):
                    source_tasks.append(task)

        # Same filters are used on Completed Tasks and Archived Tasks.
        source_tasks = apply_filters(source_tasks, include_checker=True)

        active = active_users()
        name_by_email = {
            user.get("Email", "").strip().lower(): user.get("Name", "").strip()
            for user in active
        }

        completed_items: list[dict[str, Any]] = []

        for task in source_tasks:
            assigned_email = str(
                task.get("Assigned To Email", "")
            ).strip().lower()
            checker_email = str(
                task.get("Checker Email", "")
            ).strip().lower()

            main_completion_value = (
                task.get("Completion Approved At", "")
                or task.get("Completion Date", "")
            )

            if (
                task.get("_is_completed")
                and date_value_matches_month(
                    main_completion_value,
                    completed_month,
                )
                and (
                    editor_view
                    or (
                        signed_in_email
                        and assigned_email == signed_in_email
                    )
                )
            ):
                item = dict(task)
                item["_display_type"] = "assigned_completed"
                item["_display_label"] = "TASK COMPLETED"
                item["_responsible_user"] = (
                    task.get("Assigned To", "")
                    or task.get("Assigned To Email", "")
                    or "Unassigned"
                )
                item["_responsibility_email"] = assigned_email
                item["_completed_at"] = main_completion_value
                item["_responsibility_completed_at"] = main_completion_value
                item["_completed_by"] = (
                    task.get("Completion Approved By", "")
                    or "Not recorded"
                )
                item["_outcome"] = "Completed"
                item["_billing_pending"] = is_pending_for_billing(task)
                item["_billing_completed"] = is_billing_completed(task)
                item["_can_mark_billing"] = bool(
                    not archive_view
                    and not item["_billing_pending"]
                    and can_mark_pending_billing(task)
                )
                item["_can_close_billing"] = bool(
                    not archive_view
                    and item["_billing_pending"]
                    and is_billing_admin()
                )
                # Archived records are deliberately read-only. Restore them
                # first before changing task details.
                item["_can_open"] = (
                    False if archive_view else can_update_task(task)
                )
                completed_items.append(item)

            if (
                is_completed_checking(task)
                and date_value_matches_month(
                    task.get("Checking Completed At", ""),
                    completed_month,
                )
                and (
                    editor_view
                    or (
                        signed_in_email
                        and checker_email == signed_in_email
                    )
                )
            ):
                checker_name = str(task.get("Checker Name", "")).strip()
                if not checker_name:
                    checker_name = (
                        name_by_email.get(checker_email, "")
                        or checker_email
                        or "Unassigned"
                    )

                checking_status = checking_status_value(task)
                checking_outcome = (
                    "Accepted"
                    if checking_status == CHECKING_CHECKED.lower()
                    else "Changes Required"
                )
                item = dict(task)
                item["_display_type"] = "checking_completed"
                item["_display_label"] = "CHECKING COMPLETED"
                item["_responsible_user"] = checker_name
                item["_responsibility_email"] = checker_email
                item["_completed_at"] = task.get("Checking Completed At", "")
                item["_responsibility_completed_at"] = task.get(
                    "Checking Completed At", ""
                )
                item["_completed_by"] = (
                    task.get("Checking Completed By", "")
                    or checker_name
                )
                item["_checking_outcome"] = checking_outcome
                item["_outcome"] = checking_outcome
                # Billing belongs to the completed main task, not the separate
                # completed checking responsibility.
                item["_billing_pending"] = False
                item["_billing_completed"] = False
                item["_can_mark_billing"] = False
                item["_can_close_billing"] = False
                item["_can_open"] = False
                completed_items.append(item)

        # Defence in depth for ordinary users.
        if not editor_view:
            completed_items = [
                item
                for item in completed_items
                if (
                    signed_in_email
                    and str(
                        item.get("_responsibility_email", "")
                    ).strip().lower() == signed_in_email
                )
            ]

        if billing_filter:
            # Billing is a single task-level workflow, so when a billing filter
            # is selected the separate checking-completed responsibility is
            # intentionally omitted to avoid duplicate billing records.
            billing_items = [
                item
                for item in completed_items
                if item.get("_display_type") == "assigned_completed"
            ]
            if billing_filter == "pending":
                completed_items = [
                    item for item in billing_items
                    if item.get("_billing_pending")
                ]
            elif billing_filter == "completed":
                completed_items = [
                    item for item in billing_items
                    if item.get("_billing_completed")
                ]
            elif billing_filter == "not_marked":
                completed_items = [
                    item for item in billing_items
                    if not item.get("_billing_pending")
                    and not item.get("_billing_completed")
                ]

        completed_items.sort(
            key=lambda item: (
                str(item.get("_completed_at", "")),
                str(item.get("Task ID", "")),
            ),
            reverse=True,
        )

        advocates: list[str] = []
        items_by_advocate: dict[str, list[dict[str, Any]]] = {}
        assigned_completed_counts = Counter()
        checking_completed_counts = Counter()
        task_rows: list[list[dict[str, Any] | None]] = []
        completed_filter_advocates: list[str] = []

        if editor_view:
            for user in active:
                name = str(user.get("Name", "")).strip()
                if name and name not in advocates:
                    advocates.append(name)

            for name in master_data.get("Associates", []):
                clean_name = str(name).strip()
                if clean_name and clean_name not in advocates:
                    advocates.append(clean_name)

            for item in completed_items:
                responsible_name = (
                    str(item.get("_responsible_user", "")).strip()
                    or "Unassigned"
                )
                if responsible_name not in advocates:
                    advocates.append(responsible_name)

            advocates.sort(key=str.lower)
            completed_filter_advocates = list(advocates)

            selected_advocate = request.args.get("assigned_to", "").strip()
            if selected_advocate:
                advocates = [selected_advocate]

            items_by_advocate = {
                advocate: [] for advocate in advocates
            }

            for item in completed_items:
                responsible_name = (
                    str(item.get("_responsible_user", "")).strip()
                    or "Unassigned"
                )
                if responsible_name not in items_by_advocate:
                    continue

                items_by_advocate[responsible_name].append(item)
                if item.get("_display_type") == "checking_completed":
                    checking_completed_counts[responsible_name] += 1
                else:
                    assigned_completed_counts[responsible_name] += 1

            for advocate in advocates:
                items_by_advocate[advocate].sort(
                    key=lambda item: (
                        str(item.get("_completed_at", "")),
                        str(item.get("Task ID", "")),
                    ),
                    reverse=True,
                )

            max_count = max(
                (
                    len(items_by_advocate.get(advocate, []))
                    for advocate in advocates
                ),
                default=0,
            )
            for row_number in range(max_count):
                task_rows.append(
                    [
                        items_by_advocate[advocate][row_number]
                        if row_number < len(items_by_advocate[advocate])
                        else None
                        for advocate in advocates
                    ]
                )

        archive_eligible_count = 0
        if editor_view and not archive_view:
            archive_eligible_count = sum(
                1 for task in all_task_rows if eligible_for_archive(task)
            )

        page_endpoint = "archived_tasks" if archive_view else "completed_tasks"
        return render_template(
            "completed_tasks.html",
            completed_items=completed_items,
            total_tasks=len(completed_items),
            masters=master_data,
            advocates=advocates,
            assigned_completed_counts=assigned_completed_counts,
            checking_completed_counts=checking_completed_counts,
            task_rows=task_rows,
            completed_filter_advocates=completed_filter_advocates,
            completed_editor_view=editor_view,
            archive_view=archive_view,
            page_endpoint=page_endpoint,
            archive_cutoff=archive_cutoff_date().strftime("%d-%b-%Y"),
            archive_eligible_count=archive_eligible_count,
            billing_admin_view=is_billing_admin(),
        )

    @app.route("/completed-tasks")
    @login_required
    def completed_tasks() -> Any:
        return render_completed_register(archive_view=False)

    @app.route("/archived-tasks")
    @login_required
    def archived_tasks() -> Any:
        return render_completed_register(archive_view=True)

    @app.route("/tasks/<task_id>/pending-billing", methods=["POST"])
    @login_required
    def mark_pending_billing(task_id: str) -> Any:
        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for("completed_tasks"))

        if is_archived_task(task):
            flash("Archived tasks cannot be marked Pending for Billing.", "warning")
            return redirect(url_for("completed_tasks"))
        if not is_completed(task):
            flash("Only completed tasks can be marked Pending for Billing.", "warning")
            return redirect(url_for("completed_tasks"))
        if is_billing_completed(task):
            flash("Billing has already been completed for this task.", "info")
            return redirect(url_for("completed_tasks"))
        if is_pending_for_billing(task):
            flash("This task is already Pending for Billing.", "info")
            return redirect(url_for("completed_tasks"))
        if not can_mark_pending_billing(task):
            flash(
                "You can mark Pending for Billing only on your own completed "
                "task unless you are an authorised user.",
                "warning",
            )
            return redirect(url_for("completed_tasks"))

        user = current_user()
        user_name = user.get("name", "") or user.get("email", "")
        now = datetime.now(ACTIVITY_TIMEZONE).strftime(DATETIME_FORMAT)
        updated = {header: task.get(header, "") for header in TASK_HEADERS}
        updated["Pending for Billing"] = "Yes"
        updated["Billing Flagged By"] = user_name
        updated["Billing Flagged At"] = now
        updated["Billing Completed"] = ""
        updated["Billing Completed By"] = ""
        updated["Billing Completed At"] = ""
        updated["Last Updated By"] = user_name
        updated["Last Updated At"] = now
        repo().update_task(task_id, updated)
        save_task_activities(
            [
                task_activity(
                    task_id=task_id,
                    activity_type="Marked Pending for Billing",
                    previous=task,
                    updated=updated,
                    additional_information=(
                        f"Billing flag raised by {user_name or user.get('email', '')}."
                    ),
                )
            ]
        )
        flash("Task marked Pending for Billing.", "success")
        return redirect(url_for("completed_tasks"))

    @app.route("/tasks/<task_id>/billing-complete", methods=["POST"])
    @login_required
    def mark_billing_complete(task_id: str) -> Any:
        if not is_billing_admin():
            flash("Only the designated Billing Admin can close billing.", "warning")
            return redirect(url_for("completed_tasks"))

        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for("completed_tasks"))
        if is_archived_task(task):
            flash("Archived tasks cannot be changed through the billing queue.", "warning")
            return redirect(url_for("completed_tasks"))
        if not is_completed(task):
            flash("Only completed tasks can have billing completed.", "warning")
            return redirect(url_for("completed_tasks"))
        if not is_pending_for_billing(task):
            flash("This task is not currently Pending for Billing.", "info")
            return redirect(url_for("completed_tasks"))

        user = current_user()
        user_name = user.get("name", "") or user.get("email", "")
        now = datetime.now(ACTIVITY_TIMEZONE).strftime(DATETIME_FORMAT)
        updated = {header: task.get(header, "") for header in TASK_HEADERS}
        updated["Pending for Billing"] = "No"
        updated["Billing Completed"] = "Yes"
        updated["Billing Completed By"] = user_name
        updated["Billing Completed At"] = now
        updated["Last Updated By"] = user_name
        updated["Last Updated At"] = now
        repo().update_task(task_id, updated)
        save_task_activities(
            [
                task_activity(
                    task_id=task_id,
                    activity_type="Billing Completed",
                    previous=task,
                    updated=updated,
                    additional_information=(
                        f"Billing closed by designated Billing Admin: "
                        f"{user_name or user.get('email', '')}."
                    ),
                )
            ]
        )
        flash("Billing marked completed for this task.", "success")
        return redirect(url_for("completed_tasks"))

    @app.route("/archive-completed-tasks", methods=["POST"])
    @editor_required
    def archive_completed_tasks() -> Any:
        cutoff = archive_cutoff_date()
        candidates = [
            task
            for task in repo().get_tasks()
            if eligible_for_archive(task)
        ]

        if not candidates:
            flash(
                "There are no completed tasks older than two full months "
                "that are eligible for archiving.",
                "info",
            )
            return redirect(url_for("completed_tasks"))

        user = current_user()
        user_name = user.get("name", "") or user.get("email", "")
        now = datetime.now(ACTIVITY_TIMEZONE).strftime(DATETIME_FORMAT)
        activities: list[dict[str, str]] = []
        archived_count = 0

        for task in candidates:
            task_id = str(task.get("Task ID", "")).strip()
            if not task_id:
                continue
            updated = {
                header: task.get(header, "")
                for header in TASK_HEADERS
            }
            updated["Archived"] = "Yes"
            updated["Archived At"] = now
            updated["Archived By"] = user_name
            updated["Last Updated By"] = user_name
            updated["Last Updated At"] = now

            repo().update_task(task_id, updated)
            archived_count += 1
            completed_on = task_completion_date(task)
            activities.append(
                task_activity(
                    task_id=task_id,
                    activity_type="Task Archived",
                    previous=task,
                    updated=updated,
                    additional_information=(
                        f"Completed on: "
                        f"{completed_on.isoformat() if completed_on else 'unknown'}; "
                        f"archive cutoff: {cutoff.isoformat()}."
                    ),
                )
            )

        save_task_activities(activities)
        flash(
            f"{archived_count} completed task(s) were archived successfully.",
            "success",
        )
        return redirect(url_for("completed_tasks"))

    @app.route("/tasks/<task_id>/restore-archive", methods=["POST"])
    @editor_required
    def restore_archived_task(task_id: str) -> Any:
        task = get_task(task_id)
        if not task:
            flash("Task was not found.", "danger")
            return redirect(url_for("archived_tasks"))

        if not is_archived_task(task):
            flash("This task is not archived.", "info")
            return redirect(url_for("archived_tasks"))

        user = current_user()
        user_name = user.get("name", "") or user.get("email", "")
        now = datetime.now(ACTIVITY_TIMEZONE).strftime(DATETIME_FORMAT)
        updated = {
            header: task.get(header, "")
            for header in TASK_HEADERS
        }
        updated["Archived"] = ""
        updated["Archived At"] = ""
        updated["Archived By"] = ""
        updated["Last Updated By"] = user_name
        updated["Last Updated At"] = now

        repo().update_task(task_id, updated)
        save_task_activities(
            [
                task_activity(
                    task_id=task_id,
                    activity_type="Task Restored From Archive",
                    previous=task,
                    updated=updated,
                    additional_information=(
                        "Archived flag cleared and the task restored to the "
                        "Completed Tasks register."
                    ),
                )
            ]
        )
        flash("The task was restored to Completed Tasks.", "success")
        return redirect(url_for("archived_tasks"))

    @app.errorhandler(RepositoryError)
    def handle_repository_error(error: RepositoryError) -> tuple[str, int]:
        return render_template("error.html", message=str(error)), 500

    @app.errorhandler(404)
    def not_found(_: Any) -> tuple[str, int]:
        return (
            render_template(
                "error.html", message="The requested page was not found."
            ),
            404,
        )

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )
