from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import due_reminder_service as reminder
from config import TASK_HEADERS
from repository import LocalJsonRepository


def make_repo(tmp_path: Path) -> LocalJsonRepository:
    data = tmp_path / "data"
    data.mkdir()
    (data / "users.json").write_text(
        json.dumps(
            [
                {
                    "Name": "Associate One",
                    "Email": "associate@example.com",
                    "Password Hash": "",
                    "Active": "Yes",
                },
                {
                    "Name": "Checker One",
                    "Email": "checker@example.com",
                    "Password Hash": "",
                    "Active": "Yes",
                },
                {
                    "Name": "Inactive User",
                    "Email": "inactive@example.com",
                    "Password Hash": "",
                    "Active": "No",
                },
            ]
        ),
        encoding="utf-8",
    )
    (data / "tasks.json").write_text("[]", encoding="utf-8")
    (data / "clients.json").write_text("[]", encoding="utf-8")
    (data / "task_activity_log.json").write_text("[]", encoding="utf-8")
    (data / "masters.json").write_text("{}", encoding="utf-8")
    repo = LocalJsonRepository(data)
    repo.ensure_structure()
    return repo


def task(task_id: str, **overrides: str) -> dict[str, str]:
    row = {header: "" for header in TASK_HEADERS}
    row.update(
        {
            "Task ID": task_id,
            "Client Name": "ABC Client",
            "Matter / Project": "Agreement Review",
            "Task Description": "Review draft agreement",
            "Assigned To": "Associate One",
            "Assigned To Email": "associate@example.com",
            "Due Date": "2026-08-09",
            "Priority": "Normal",
            "Status": "In Progress",
        }
    )
    row.update(overrides)
    return row


def test_collect_due_tomorrow_routes_checking_to_checker_and_excludes_workflow_states(tmp_path):
    repo = make_repo(tmp_path)
    repo.add_task(task("A1"))
    repo.add_task(
        task(
            "C1",
            Status="Pending for Checking",
            **{
                "Checker Name": "Checker One",
                "Checker Email": "checker@example.com",
            },
        )
    )
    repo.add_task(task("P1", Status="Pending Completion Approval"))
    repo.add_task(task("D1", Status="Completed"))
    repo.add_task(task("R1", Archived="Yes"))
    repo.add_task(task("X1", Deleted="Yes"))
    repo.add_task(
        task(
            "I1",
            **{"Assigned To": "Inactive User", "Assigned To Email": "inactive@example.com"},
        )
    )

    grouped, warnings, due_date = reminder.collect_due_tomorrow(
        repo,
        today=date(2026, 8, 8),
    )

    assert due_date == date(2026, 8, 9)
    assert set(grouped) == {"associate@example.com", "checker@example.com"}
    assert [row["Task ID"] for row in grouped["associate@example.com"]] == ["A1"]
    assert grouped["associate@example.com"][0]["_responsibility"] == "Assigned Task"
    assert [row["Task ID"] for row in grouped["checker@example.com"]] == ["C1"]
    assert grouped["checker@example.com"][0]["_responsibility"] == "For Checking"
    assert any("I1" in warning and "not an active" in warning for warning in warnings)


def test_duplicate_protection_is_per_current_due_date(tmp_path):
    repo = make_repo(tmp_path)
    repo.add_task(
        task(
            "A1",
            **{"Due Reminder Sent For": "2026-08-09"},
        )
    )
    repo.add_task(
        task(
            "A2",
            **{"Due Reminder Sent For": "2026-08-01"},
        )
    )

    grouped, _, _ = reminder.collect_due_tomorrow(
        repo,
        today=date(2026, 8, 8),
    )
    assert [row["Task ID"] for row in grouped["associate@example.com"]] == ["A2"]


def test_run_sends_one_digest_per_user_marks_tasks_and_logs_activity(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    repo.add_task(task("A1"))
    repo.add_task(task("A2", **{"Priority": "Urgent"}))
    repo.add_task(
        task(
            "C1",
            Status="Pending for Checking",
            **{
                "Checker Name": "Checker One",
                "Checker Email": "checker@example.com",
            },
        )
    )

    sent_messages = []

    def fake_gmail_send(message, config):
        sent_messages.append(message)
        return f"gmail-message-{len(sent_messages)}"

    monkeypatch.setattr(reminder, "_send_with_gmail_api", fake_gmail_send)
    config = {
        "GMAIL_FROM_EMAIL": "reminders@example.com",
        "GMAIL_FROM_NAME": "Anand Akut & Associates",
        "TASK_MANAGER_URL": "https://tasks.example.com",
    }

    result = reminder.run_due_reminders(
        repo,
        config,
        today=date(2026, 8, 8),
    )

    assert result.success
    assert result.eligible_tasks == 3
    assert result.recipient_count == 2
    assert result.sent_emails == 2
    assert result.updated_tasks == 3
    assert len(sent_messages) == 2

    recipients = {message["To"] for message in sent_messages}
    assert recipients == {"associate@example.com", "checker@example.com"}

    saved = {row["Task ID"]: row for row in repo.get_tasks()}
    assert saved["A1"]["Due Reminder Sent For"] == "2026-08-09"
    assert saved["A2"]["Due Reminder Sent For"] == "2026-08-09"
    assert saved["C1"]["Due Reminder Sent For"] == "2026-08-09"
    assert saved["A1"]["Due Reminder Sent At"]

    activity_rows = json.loads(repo.activity_file.read_text(encoding="utf-8"))
    assert len(activity_rows) == 3
    assert {row["Activity Type"] for row in activity_rows} == {
        "Due Date Reminder Email Sent"
    }
    assert all("One day prior" in row["Additional Information"] for row in activity_rows)


def test_dry_run_does_not_send_or_update(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    repo.add_task(task("A1"))

    def should_not_send(*_args, **_kwargs):
        raise AssertionError("Gmail API should not be called during dry run")

    monkeypatch.setattr(reminder, "_send_with_gmail_api", should_not_send)
    result = reminder.run_due_reminders(
        repo,
        {"GMAIL_FROM_EMAIL": "reminders@example.com"},
        today=date(2026, 8, 8),
        dry_run=True,
    )

    assert result.eligible_tasks == 1
    assert result.sent_emails == 0
    assert repo.get_tasks()[0]["Due Reminder Sent For"] == ""
