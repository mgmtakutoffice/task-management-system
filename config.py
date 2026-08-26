from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Existing columns stay in their original order. New workflow columns are
# appended so an existing Google Sheet can be migrated safely.
TASK_HEADERS = [
    "Task ID",
    "Client Name",
    "Matter / Project",
    "Task Description",
    "Assigned To",
    "Assigned To Email",
    "Assigned By",
    "Assigned Date",
    "Due Date",
    "Priority",
    "Status",
    "Progress Remarks",
    "Pending Reason",
    "Additional Details",
    "Reference Link",
    "Completion Date",
    "Created By",
    "Created At",
    "Last Updated By",
    "Last Updated At",
    "Deleted",
    "Client Code",
    "Entity Name",
    "Completion Submitted By",
    "Completion Submitted At",
    "Completion Approval Status",
    "Completion Approved By",
    "Completion Approved At",
    "Completion Rejection Reason",
    "Checker Name",
    "Checker Email",
    "Checking Status",
    "Submitted for Checking By",
    "Submitted for Checking At",
    "Checking Completed By",
    "Checking Completed At",
    "Checking Comment",
    "Changes Required Comment",
    "Today Focus Date",
    "Today Focus By",
    "Checking Today Focus Date",
    "Checking Today Focus By",
    "Authorised Today Date",
    "Authorised Today By",
    "Authorised Today At",
    "Checking Authorised Today Date",
    "Checking Authorised Today By",
    "Checking Authorised Today At",
    "Archived",
    "Archived At",
    "Archived By",
    "Due Reminder Sent For",
    "Due Reminder Sent At",
    "Overdue Reminder Sent On",
    "Overdue Reminder Sent At",
    "Pending for Billing",
    "Billing Flagged By",
    "Billing Flagged At",
    "Billing Completed",
    "Billing Completed By",
    "Billing Completed At",
]

# Append-only backend audit log. This is intentionally kept separate from
# the Tasks sheet so repeated status changes are never overwritten.
TASK_ACTIVITY_HEADERS = [
    "Activity ID",
    "Task ID",
    "Activity Type",
    "Previous Status",
    "New Status",
    "Updated By",
    "Updated By Email",
    "Activity At",
    "Previous Assignee",
    "New Assignee",
    "Previous Due Date",
    "New Due Date",
    "Checker Name",
    "Checking Outcome",
    "Comment",
    "Additional Information",
]

NOTIFICATION_HEADERS = [
    "Notification ID",
    "User Email",
    "Type",
    "Task ID",
    "Title",
    "Message",
    "Created By",
    "Created At",
    "Read",
    "Read At",
]

PUSH_SUBSCRIPTION_HEADERS = [
    "Subscription ID",
    "User Email",
    "Endpoint",
    "P256DH",
    "Auth",
    "User Agent",
    "Origin",
    "Created At",
    "Last Seen At",
    "Active",
]

USER_HEADERS = ["Name", "Email", "Password Hash", "Active"]
CLIENT_HEADERS = [
    "Client Code",
    "Client Name",
    "Entity Name",
    "Active",
    "Created By",
    "Created At",
]
MASTER_HEADERS = ["Associates", "Clients", "Status", "Priority", "Task Categories"]

DEFAULT_STATUSES = [
    "Yet to Start",
    "In Progress",
    "Pending for Checking",
    "Changes Required",
    "Pending for Client Input",
    "Pending for Client Confirmation",
    "Pending for Estimate Approval",
    "On Hold",
    "Completion Rejected",
    "Pending Completion Approval",
    "Completed",
]
DEFAULT_PRIORITIES = ["Normal", "Important", "Urgent"]
DEFAULT_CATEGORIES = ["Client Work", "Internal", "Compliance", "Follow-up", "Other"]


def parse_email_list(value: str) -> tuple[str, ...]:
    """Return normalized comma-separated email addresses."""
    return tuple(
        email.strip().lower()
        for email in value.split(",")
        if email.strip()
    )


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key-before-deployment")
    DATA_BACKEND = os.getenv("DATA_BACKEND", "local").strip().lower()
    SHEET_ID = os.getenv("SHEET_ID", "").strip()
    # Separate Google Spreadsheet used only for the append-only activity log.
    ACTIVITY_SHEET_ID = os.getenv("ACTIVITY_SHEET_ID", "").strip()
    GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
    TASKS_SHEET_NAME = os.getenv("TASKS_SHEET_NAME", "Tasks").strip()
    USERS_SHEET_NAME = os.getenv("USERS_SHEET_NAME", "Users").strip()
    MASTERS_SHEET_NAME = os.getenv("MASTERS_SHEET_NAME", "Masters").strip()
    CLIENTS_SHEET_NAME = os.getenv("CLIENTS_SHEET_NAME", "Clients").strip()
    NOTIFICATIONS_SHEET_NAME = os.getenv(
        "NOTIFICATIONS_SHEET_NAME", "Notifications"
    ).strip()
    PUSH_SUBSCRIPTIONS_SHEET_NAME = os.getenv(
        "PUSH_SUBSCRIPTIONS_SHEET_NAME", "Push Subscriptions"
    ).strip()
    ACTIVITY_SHEET_NAME = os.getenv(
        "ACTIVITY_SHEET_NAME", "Task Activity Log"
    ).strip()
    TASK_EDITOR_EMAILS = parse_email_list(os.getenv("TASK_EDITOR_EMAILS", ""))
    # Exactly one login may close a Pending for Billing flag after invoicing.
    BILLING_ADMIN_EMAIL = os.getenv("BILLING_ADMIN_EMAIL", "").strip().lower()

    # Browser Web Push (VAPID). The public key is safe to expose to browsers;
    # the private key must remain a server secret. Leave all three blank to
    # disable browser push while retaining the in-app notification bell.
    VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip()
    VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "").strip()

    # Gmail API + OAuth settings used by the scheduled due-date reminder.
    # Local development can point to gmail_token.json. Render should store
    # the full authorized-user token JSON in GMAIL_OAUTH_TOKEN_JSON.
    GMAIL_OAUTH_TOKEN_JSON = os.getenv("GMAIL_OAUTH_TOKEN_JSON", "").strip()
    GMAIL_OAUTH_TOKEN_FILE = os.getenv("GMAIL_OAUTH_TOKEN_FILE", "").strip()
    GMAIL_FROM_EMAIL = os.getenv("GMAIL_FROM_EMAIL", "").strip()
    GMAIL_FROM_NAME = os.getenv(
        "GMAIL_FROM_NAME", "Anand Akut & Associates"
    ).strip()
    # Management recipients for the 1st/11th/21st pending-client digest.
    PENDING_CLIENT_DIGEST_RECIPIENTS = parse_email_list(
        os.getenv("PENDING_CLIENT_DIGEST_RECIPIENTS", "")
    )
    TASK_MANAGER_URL = os.getenv("TASK_MANAGER_URL", "").strip().rstrip("/")
    LOCAL_DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", str(BASE_DIR / "data")))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
