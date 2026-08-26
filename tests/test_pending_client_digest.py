from datetime import date
from pathlib import Path

from pending_client_digest_service import (
    build_pending_client_digest_message,
    collect_pending_client_tasks,
    run_pending_client_digest,
)
from repository import LocalJsonRepository


def make_repository(tmp_path: Path) -> LocalJsonRepository:
    repository = LocalJsonRepository(tmp_path / "data")
    repository.ensure_structure()
    return repository


def test_digest_uses_latest_true_status_transition_not_later_edit(tmp_path):
    repository = make_repository(tmp_path)
    repository.add_task(
        {
            "Task ID": "T-1",
            "Client Code": "CL001",
            "Client Name": "ABC Developers",
            "Matter / Project": "Project A",
            "Task Description": "Await documents",
            "Assigned To": "Associate One",
            "Assigned To Email": "associate@example.com",
            "Status": "Pending for Client Input",
        }
    )
    repository.add_task_activities(
        [
            {
                "Activity ID": "A1",
                "Task ID": "T-1",
                "Activity Type": "Status Changed",
                "Previous Status": "In Progress",
                "New Status": "Pending for Client Input",
                "Activity At": "2026-08-12T10:00:00+05:30",
            },
            {
                "Activity ID": "A2",
                "Task ID": "T-1",
                "Activity Type": "Task Updated",
                "Previous Status": "Pending for Client Input",
                "New Status": "Pending for Client Input",
                "Activity At": "2026-08-20T15:30:00+05:30",
            },
        ]
    )

    grouped, warnings = collect_pending_client_tasks(repository)
    task = grouped["Associate One"][0]
    assert task["Status Since"] == "12-Aug-2026"
    assert warnings == []


def test_digest_uses_latest_reentry_into_same_status(tmp_path):
    repository = make_repository(tmp_path)
    repository.add_task(
        {
            "Task ID": "T-2",
            "Client Code": "CL002",
            "Client Name": "XYZ Limited",
            "Matter / Project": "Matter B",
            "Task Description": "Await final confirmation",
            "Assigned To": "Associate Two",
            "Assigned To Email": "two@example.com",
            "Status": "Pending for Client Confirmation",
        }
    )
    repository.add_task_activities(
        [
            {
                "Activity ID": "B1",
                "Task ID": "T-2",
                "Previous Status": "In Progress",
                "New Status": "Pending for Client Confirmation",
                "Activity At": "2026-08-01T09:00:00+05:30",
            },
            {
                "Activity ID": "B2",
                "Task ID": "T-2",
                "Previous Status": "Pending for Client Confirmation",
                "New Status": "In Progress",
                "Activity At": "2026-08-05T09:00:00+05:30",
            },
            {
                "Activity ID": "B3",
                "Task ID": "T-2",
                "Previous Status": "In Progress",
                "New Status": "Pending for Client Confirmation",
                "Activity At": "2026-08-18T09:00:00+05:30",
            },
        ]
    )

    grouped, _ = collect_pending_client_tasks(repository)
    assert grouped["Associate Two"][0]["Status Since"] == "18-Aug-2026"


def test_digest_excludes_other_deleted_and_archived_tasks(tmp_path):
    repository = make_repository(tmp_path)
    tasks = [
        ("T1", "Pending for Client Input", "", ""),
        ("T2", "Pending for Client Confirmation", "", ""),
        ("T3", "In Progress", "", ""),
        ("T4", "Pending for Client Input", "Yes", ""),
        ("T5", "Pending for Client Confirmation", "", "Yes"),
    ]
    for task_id, status, deleted, archived in tasks:
        repository.add_task(
            {
                "Task ID": task_id,
                "Client Code": "CL",
                "Client Name": "Client",
                "Assigned To": "Person",
                "Assigned To Email": "person@example.com",
                "Status": status,
                "Deleted": deleted,
                "Archived": archived,
            }
        )

    grouped, _ = collect_pending_client_tasks(repository)
    assert {item["Task ID"] for item in grouped["Person"]} == {"T1", "T2"}


def test_email_has_only_requested_table_columns():
    message = build_pending_client_digest_message(
        recipients=["management@example.com"],
        grouped_tasks={
            "Associate One": [
                {
                    "Client Code": "CL001",
                    "Client Name": "ABC Developers",
                    "Matter / Project": "Project A",
                    "Task Description": "Await documents",
                    "Status": "Pending for Client Input",
                    "Status Since": "12-Aug-2026",
                }
            ]
        },
        report_date=date(2026, 8, 21),
        config={
            "GMAIL_FROM_EMAIL": "reminders@example.com",
            "GMAIL_FROM_NAME": "Anand Akut & Associates",
        },
    )
    html_body = message.get_body(preferencelist=("html",)).get_content()
    for heading in [
        "Sr.",
        "Client Code",
        "Client Name",
        "Matter / Project",
        "Task Description",
        "Status",
        "Status Since",
    ]:
        assert heading in html_body
    assert "Priority" not in html_body
    assert "Due Date" not in html_body


def test_non_schedule_day_is_skipped_without_recipient_config(tmp_path):
    repository = make_repository(tmp_path)
    result = run_pending_client_digest(
        repository,
        {},
        report_date=date(2026, 8, 25),
        dry_run=False,
        enforce_schedule_day=True,
    )
    assert result.skipped is True
    assert result.sent_emails == 0
