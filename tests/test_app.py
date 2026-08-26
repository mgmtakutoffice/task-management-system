import json
from pathlib import Path

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path: Path):
    source_data = Path(__file__).resolve().parents[1] / "data"
    test_data = tmp_path / "data"
    test_data.mkdir()
    for filename in ["users.json", "tasks.json", "masters.json", "clients.json"]:
        (test_data / filename).write_text(
            (source_data / filename).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATA_BACKEND": "local",
            "LOCAL_DATA_DIR": test_data,
            "TASK_EDITOR_EMAILS": (
                "demo@taskapp.local",
                "second-editor@taskapp.local",
            ),
            "BILLING_ADMIN_EMAIL": "demo@taskapp.local",
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()


def set_login(client, email: str, name: str):
    with client.session_transaction() as session:
        session.clear()
        session["user_email"] = email
        session["user_name"] = name


def login_editor(client):
    set_login(client, "demo@taskapp.local", "Demo User")


def login_associate(client):
    set_login(client, "associate@taskapp.local", "Associate One")


def login_checker(client):
    set_login(client, "checker@taskapp.local", "Checker One")


def tasks(app):
    return app.extensions["task_repository"].get_tasks()


def task_by_id(app, task_id):
    return next(row for row in tasks(app) if row["Task ID"] == task_id)


def submit_for_checking(client, task_id="TSK-20260708-D4E5F6"):
    return client.post(
        f"/tasks/{task_id}/edit",
        data={
            "status": "Pending for Checking",
            "checker_email": "checker@taskapp.local",
            "progress_remarks": "Draft completed and ready for checking.",
            "pending_reason": "",
            "additional_details": "Please verify the final clauses.",
        },
        follow_redirects=True,
    )


def submit_for_completion_approval(client, task_id="TSK-20260708-D4E5F6"):
    return client.post(
        f"/tasks/{task_id}/edit",
        data={
            "status": "Pending Completion Approval",
            "checker_email": "",
        },
        follow_redirects=True,
    )


def editor_full_task_data(status, assigned_email="associate@taskapp.local"):
    return {
        "client_code": "CL002",
        "matter_project": "Conveyance Follow-up",
        "task_description": "Editor revised task",
        "assigned_to_email": assigned_email,
        "assigned_by": "Demo User",
        "assigned_date": "2026-07-08",
        "due_date": "2026-08-01",
        "priority": "Important",
        "status": status,
        "checker_email": "checker@taskapp.local",
        "progress_remarks": "Editor update",
        "pending_reason": "",
        "additional_details": "Editor details",
        "reference_link": "",
        "completion_date": "",
    }


def test_authenticated_pages_are_not_browser_cached(client):
    login_editor(client)
    response = client.get("/completed-tasks")
    assert response.status_code == 200
    assert "no-store" in response.headers.get("Cache-Control", "")
    assert response.headers.get("Pragma") == "no-cache"
    assert response.headers.get("Expires") == "0"


def test_main_pages_load(client):
    login_editor(client)
    for route in [
        "/",
        "/tasks/add",
        "/update-task",
        "/my-tasks",
        "/ongoing-tasks",
        "/completed-tasks",
        "/completion-approvals",
    ]:
        assert client.get(route).status_code == 200


def test_dashboard_hides_urgent_and_overdue_summary(client):
    login_editor(client)
    response = client.get("/")
    assert b">Urgent<" not in response.data
    assert b">Overdue<" not in response.data
    assert b"Urgent and overdue" not in response.data


def test_add_form_hides_deferred_details_but_update_reopens_additional_details(client):
    login_associate(client)
    response = client.get("/tasks/add")
    assert b"Any user may set the due date while creating a task." in response.data
    assert b'name="due_date"' in response.data
    for label in [b"Progress Remarks", b"Pending Reason", b"Additional Details / Comment"]:
        assert label not in response.data
    for field_name in [
        b'name="progress_remarks"',
        b'name="pending_reason"',
        b'name="additional_details"',
    ]:
        assert field_name not in response.data
    assert b"Completion Rejected" not in response.data

    response = client.get("/tasks/TSK-20260708-D4E5F6/edit")
    assert response.status_code == 200
    assert b"Progress Remarks" not in response.data
    assert b"Pending Reason" not in response.data
    assert b"Additional Details / Comment" in response.data
    assert b'name="additional_details"' in response.data
    assert b'name="task_description"' in response.data
    # Description is editable for an ordinary assigned user (not readonly).
    description_start = response.data.find(b'name="task_description"')
    assert description_start >= 0
    assert b"readonly" not in response.data[description_start:description_start + 300]
    assert b"Completion Rejected" not in response.data

    login_editor(client)
    response = client.get("/tasks/add")
    assert b'name="due_date"' in response.data
    assert b'name="additional_details"' not in response.data
    assert b"Completion Rejected" not in response.data



def test_associate_can_set_due_date_while_creating_task(app, client):
    login_associate(client)
    response = client.post(
        "/tasks/add",
        data={
            "client_code": "CL001",
            "matter_project": "Creation due-date test",
            "task_description": "Task created by ordinary user with due date",
            "assigned_to_email": "associate@taskapp.local",
            "assigned_by": "Associate One",
            "assigned_date": "2026-07-24",
            "due_date": "2026-08-15",
            "priority": "Important",
            "status": "Yet to Start",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    created = next(
        row
        for row in tasks(app)
        if row["Task Description"] == "Task created by ordinary user with due date"
    )
    assert created["Due Date"] == "2026-08-15"


def test_associate_cannot_change_due_date_after_creation(app, client):
    original_due_date = task_by_id(app, "TSK-20260708-D4E5F6")["Due Date"]
    login_associate(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={
            "status": "In Progress",
            "checker_email": "",
            "due_date": "2027-01-01",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert task_by_id(app, "TSK-20260708-D4E5F6")["Due Date"] == original_due_date


def test_authorised_user_can_change_due_date_after_creation(app, client):
    login_editor(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data=editor_full_task_data("In Progress"),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert task_by_id(app, "TSK-20260708-D4E5F6")["Due Date"] == "2026-08-01"


def test_priority_is_shown_at_top_of_task_cards(client):
    login_editor(client)
    ongoing = client.get("/ongoing-tasks")
    assert ongoing.status_code == 200
    assert b"task-card-top" in ongoing.data
    assert b"task-priority-badge-urgent" in ongoing.data
    assert b">Urgent<" in ongoing.data

    completed = client.get("/completed-tasks")
    assert completed.status_code == 200
    assert b"task-priority-badge-normal" in completed.data
    assert b">Normal<" in completed.data
    assert b"Completion approved by" in completed.data

def test_system_rejected_status_is_displayed_but_not_selectable(app, client):
    repo = app.extensions["task_repository"]
    rejected = task_by_id(app, "TSK-20260708-D4E5F6")
    rejected["Status"] = "Completion Rejected"
    rejected["Completion Rejection Reason"] = "Correct the final annexure."
    repo.update_task(rejected["Task ID"], rejected)

    login_associate(client)
    response = client.get("/tasks/TSK-20260708-D4E5F6/edit")
    assert response.status_code == 200
    assert b'value="Completion Rejected" selected hidden' in response.data
    assert response.data.count(b'value="Completion Rejected"') == 1
    assert b"Completion request rejected" in response.data


def test_completion_rejected_cannot_be_posted_manually(app, client):
    login_associate(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={"status": "Completion Rejected"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"system-generated status" in response.data
    assert task_by_id(app, "TSK-20260708-D4E5F6")["Status"] != "Completion Rejected"


def test_hidden_detail_fields_preserve_existing_sheet_values(app, client):
    original = task_by_id(app, "TSK-20260708-D4E5F6")
    expected = {
        "Progress Remarks": original["Progress Remarks"],
        "Pending Reason": original["Pending Reason"],
        "Additional Details": original["Additional Details"],
    }

    login_associate(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={"status": "In Progress", "checker_email": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    for column, value in expected.items():
        assert updated[column] == value


def test_associate_submits_task_for_checking(app, client):
    login_associate(client)
    response = submit_for_checking(client)
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Status"] == "Pending for Checking"
    assert updated["Checking Status"] == "Pending"
    assert updated["Checker Name"] == "Checker One"
    assert updated["Checker Email"] == "checker@taskapp.local"
    assert updated["Submitted for Checking By"] == "Associate One"
    assert updated["Submitted for Checking At"]
    assert updated["Completion Approval Status"] == ""


def test_forged_completed_by_associate_is_converted_to_completion_approval(app, client):
    login_associate(client)
    client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={
            "status": "Completed",
            "checker_email": "",
        },
        follow_redirects=True,
    )
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Status"] == "Pending Completion Approval"
    assert updated["Completion Approval Status"] == "Pending"
    assert updated["Completion Date"] == ""


def test_update_form_offers_both_submission_flows(client):
    login_associate(client)
    response = client.get("/tasks/TSK-20260708-D4E5F6/edit")
    assert response.status_code == 200
    assert b"Submit for Checking" in response.data
    assert b"Submit for Completion Approval" in response.data


def test_associate_can_submit_directly_for_completion_approval_without_checker(
    app, client
):
    login_associate(client)
    response = submit_for_completion_approval(client)
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Status"] == "Pending Completion Approval"
    assert updated["Completion Approval Status"] == "Pending"
    assert updated["Completion Submitted By"] == "Associate One"
    assert updated["Completion Submitted At"]
    assert updated["Checker Email"] == ""
    assert updated["Checking Status"] == ""

    login_editor(client)
    approvals = client.get("/completion-approvals")
    assert b"Obtain pending property card" in approvals.data


def test_checker_must_be_different_user(app, client):
    login_associate(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={
            "status": "Pending for Checking",
            "checker_email": "associate@taskapp.local",
            "progress_remarks": "Ready",
            "pending_reason": "",
            "additional_details": "",
        },
        follow_redirects=True,
    )
    assert b"checker must be a different user" in response.data.lower()
    assert task_by_id(app, "TSK-20260708-D4E5F6")["Status"] != "Pending for Checking"


def test_ongoing_page_displays_assigned_and_for_checking_for_editor(app, client):
    login_associate(client)
    submit_for_checking(client)
    login_editor(client)
    response = client.get("/ongoing-tasks")
    assert response.status_code == 200
    assert b"ASSIGNED" in response.data
    assert b"FOR CHECKING" in response.data
    assert b"Associate One" in response.data
    assert b"Checker One" in response.data
    assert response.data.count(b"Obtain pending property card") >= 2


def test_ongoing_tab_and_route_are_hidden_from_normal_users(client):
    login_associate(client)
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert b"All Ongoing Tasks" not in dashboard.data

    response = client.get("/ongoing-tasks")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_assignee_is_locked_while_task_is_pending_checking(app, client):
    login_associate(client)
    submit_for_checking(client)
    response = client.get(
        "/tasks/TSK-20260708-D4E5F6/edit",
        follow_redirects=True,
    )
    assert b"awaiting checking" in response.data.lower()


def test_checker_can_open_review_page(app, client):
    login_associate(client)
    submit_for_checking(client)
    login_checker(client)
    response = client.get("/tasks/TSK-20260708-D4E5F6/checking")
    assert response.status_code == 200
    assert b"Review Task for Checking" in response.data
    assert b"Mark Checking Completed" in response.data
    assert b"Return for Changes" in response.data


def test_pending_checking_appears_in_checker_my_tasks(app, client):
    login_associate(client)
    submit_for_checking(client)
    login_checker(client)
    response = client.get("/my-tasks")
    assert response.status_code == 200
    assert b"FOR CHECKING" in response.data
    assert b"Obtain pending property card" in response.data
    assert b">Check<" in response.data


def test_ordinary_checker_acceptance_waits_for_authorised_approval(app, client):
    login_associate(client)
    submit_for_checking(client)
    login_checker(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/checking/complete",
        data={"checking_comment": "All clauses and references verified."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Checking Status"] == "Checked"
    assert updated["Checking Completed By"] == "Checker One"
    assert updated["Status"] == "Pending Completion Approval"
    assert updated["Completion Approval Status"] == "Pending"
    assert updated["Completion Approved By"] == ""
    assert updated["Completion Date"] == ""
    assert updated["Completion Submitted By"] == "Checker One"

    ongoing = client.get("/ongoing-tasks")
    assert ongoing.status_code == 302

    my_tasks = client.get("/my-tasks")
    assert b"CHECKING COMPLETED" not in my_tasks.data
    assert b"All clauses and references verified." not in my_tasks.data
    assert b"Obtain pending property card" not in my_tasks.data

    completed = client.get("/completed-tasks")
    assert b"CHECKING COMPLETED" in completed.data
    assert b"Accepted" in completed.data
    assert b"TASK COMPLETED" not in completed.data

    login_editor(client)
    approvals = client.get("/completion-approvals")
    assert b"Obtain pending property card" in approvals.data
    assert b"All clauses and references verified." in approvals.data

    client.post(
        "/tasks/TSK-20260708-D4E5F6/completion/approve",
        follow_redirects=True,
    )
    approved = task_by_id(app, "TSK-20260708-D4E5F6")
    assert approved["Status"] == "Completed"
    assert approved["Completion Approval Status"] == "Approved"
    assert approved["Completion Approved By"] == "Demo User"
    assert approved["Completion Date"]

    completed_after_approval = client.get("/completed-tasks")
    assert b"TASK COMPLETED" in completed_after_approval.data
    assert b"CHECKING COMPLETED" in completed_after_approval.data
    assert completed_after_approval.data.count(b"Obtain pending property card") >= 2

def test_checker_can_return_for_changes(app, client):
    login_associate(client)
    submit_for_checking(client)
    login_checker(client)
    client.post(
        "/tasks/TSK-20260708-D4E5F6/checking/return",
        data={"changes_required_comment": "Correct the consideration amount."},
        follow_redirects=True,
    )
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Status"] == "Changes Required"
    assert updated["Checking Status"] == "Changes Required"
    assert updated["Changes Required Comment"] == "Correct the consideration amount."

    ongoing = client.get("/ongoing-tasks")
    assert ongoing.status_code == 302

    my_tasks = client.get("/my-tasks")
    assert b"CHECKING COMPLETED" not in my_tasks.data
    assert b"Obtain pending property card" not in my_tasks.data

    completed = client.get("/completed-tasks")
    assert b"CHECKING COMPLETED" in completed.data
    assert b"Changes Required" in completed.data
    assert b"Correct the consideration amount." in completed.data
    assert b"Associate One" not in completed.data


def test_authorised_checker_acceptance_still_waits_for_completion_approval(app, client):
    login_associate(client)
    client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={
            "status": "Pending for Checking",
            "checker_email": "demo@taskapp.local",
            "progress_remarks": "Ready for management checking.",
            "pending_reason": "",
            "additional_details": "",
        },
        follow_redirects=True,
    )
    login_editor(client)
    client.post(
        "/tasks/TSK-20260708-D4E5F6/checking/complete",
        data={"checking_comment": "Checked and accepted for final approval."},
        follow_redirects=True,
    )
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Status"] == "Pending Completion Approval"
    assert updated["Checking Status"] == "Checked"
    assert updated["Completion Approval Status"] == "Pending"
    assert updated["Completion Approved By"] == ""
    assert updated["Completion Date"] == ""

    ongoing = client.get("/ongoing-tasks")
    assert b"CHECKING COMPLETED" not in ongoing.data
    assert b"Outcome: Accepted" not in ongoing.data
    assert b"Checked and accepted for final approval." not in ongoing.data
    assert ongoing.data.count(b"Obtain pending property card") == 1

    completed = client.get("/completed-tasks")
    assert b"CHECKING COMPLETED" in completed.data
    assert b"Outcome: Accepted" in completed.data
    assert b"Checked and accepted for final approval." in completed.data

    approvals = client.get("/completion-approvals")
    assert b"Obtain pending property card" in approvals.data

def test_checker_acceptance_creates_pending_completion_approval(app, client):
    login_associate(client)
    submit_for_checking(client)
    login_checker(client)
    client.post(
        "/tasks/TSK-20260708-D4E5F6/checking/complete",
        data={"checking_comment": "Checked."},
        follow_redirects=True,
    )
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Status"] == "Pending Completion Approval"
    assert updated["Completion Approval Status"] == "Pending"

    login_editor(client)
    dashboard = client.get("/")
    assert b"Obtain pending property card" in dashboard.data
    approvals = client.get("/completion-approvals")
    assert b"Obtain pending property card" in approvals.data

def test_authorised_user_can_directly_complete_own_task(app, client):
    login_editor(client)
    response = client.post(
        "/tasks/TSK-20260710-A1B2C3/edit",
        data={
            "client_code": "CL001",
            "matter_project": "Sale Agreement Review",
            "task_description": "Review completed by authorised user.",
            "assigned_to_email": "demo@taskapp.local",
            "assigned_by": "Management",
            "assigned_date": "2026-07-10",
            "due_date": "2026-07-20",
            "priority": "Urgent",
            "status": "Completed",
            "checker_email": "",
            "progress_remarks": "Completed",
            "pending_reason": "",
            "additional_details": "",
            "reference_link": "",
            "completion_date": "2026-07-19",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260710-A1B2C3")
    assert updated["Status"] == "Completed"
    assert updated["Completion Approval Status"] == "Self Approved"


def test_completed_page_groups_items_under_user_columns_for_editor(client):
    login_editor(client)
    response = client.get("/completed-tasks")
    assert response.status_code == 200
    assert b"advocate-wise-table" in response.data
    assert b"All Completed Tasks" in response.data
    assert b'<span class="advocate-name">Demo User</span>' in response.data
    assert b'<span class="advocate-name">Associate One</span>' in response.data
    assert b'<span class="advocate-name">Checker One</span>' in response.data
    assert b"TASK COMPLETED" in response.data

    # User headings are rendered in the table header before completed cards.
    assert response.data.index(b'<span class="advocate-name">Demo User</span>') < response.data.index(b"TASK COMPLETED")


def test_normal_user_sees_only_own_completed_tasks(app, client):
    repo = app.extensions["task_repository"]
    own_task = task_by_id(app, "TSK-20260708-D4E5F6")
    own_task["Status"] = "Completed"
    own_task["Completion Date"] = "2026-07-28"
    own_task["Completion Approved By"] = "Demo User"
    own_task["Completion Approved At"] = "2026-07-28 12:30:00"
    repo.update_task(own_task["Task ID"], own_task)

    login_associate(client)
    response = client.get("/completed-tasks")
    assert response.status_code == 200
    assert b"My Completed Tasks" in response.data
    assert b"Obtain pending property card" in response.data
    assert b"Complete the monthly document checklist" not in response.data
    assert b'<span class="advocate-name">Demo User</span>' not in response.data
    assert b'<span class="advocate-name">Checker One</span>' not in response.data
    assert b"advocate-wise-table" not in response.data
    assert b'<select name="assigned_to">' not in response.data


def test_ongoing_tasks_filter_by_assignment_month(client):
    login_editor(client)

    response = client.get("/ongoing-tasks?assignment_month=2026-07")
    assert response.status_code == 200
    assert b'name="assignment_month" value="2026-07"' in response.data
    assert b"Review the revised agreement" in response.data
    assert b"Obtain pending property card" in response.data

    response = client.get("/ongoing-tasks?assignment_month=2026-06")
    assert response.status_code == 200
    assert b"Review the revised agreement" not in response.data
    assert b"Obtain pending property card" not in response.data


def test_completed_tasks_filter_by_completed_month(client):
    login_editor(client)

    response = client.get("/completed-tasks?completed_month=2026-07")
    assert response.status_code == 200
    assert b'name="completed_month" value="2026-07"' in response.data
    assert b"Complete the monthly document checklist" in response.data

    response = client.get("/completed-tasks?completed_month=2026-06")
    assert response.status_code == 200
    assert b"Complete the monthly document checklist" not in response.data


def test_dashboard_uses_revised_associate_workload_cards(client):
    login_editor(client)
    response = client.get("/")
    assert response.status_code == 200
    assert b"workload-grid" in response.data
    assert b"workload-card" in response.data
    assert b"Assigned ongoing" in response.data
    assert b"For checking" in response.data
    assert b"Overdue tasks" in response.data
    assert b"overdue=1" in response.data
    assert b"Completed this month" in response.data
    assert b"not a capacity or performance score" in response.data


def activity_rows(app):
    repository = app.extensions["task_repository"]
    return json.loads(repository.activity_file.read_text(encoding="utf-8"))


def test_split_client_pending_statuses_are_available(client):
    login_associate(client)

    add_page = client.get("/tasks/add")
    assert add_page.status_code == 200
    assert b"Pending for Client Input" in add_page.data
    assert b"Pending for Client Confirmation" in add_page.data
    assert b">Pending for Client<" not in add_page.data


def test_split_client_pending_status_can_be_saved(app, client):
    login_associate(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={
            "status": "Pending for Client Input",
            "checker_email": "",
            "additional_details": "Awaiting documents from client.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Status"] == "Pending for Client Input"

    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={
            "status": "Pending for Client Confirmation",
            "checker_email": "",
            "additional_details": "Draft sent; awaiting confirmation.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Status"] == "Pending for Client Confirmation"


def test_pending_estimate_approval_status_is_available_on_add_and_update(client):
    login_associate(client)

    add_page = client.get("/tasks/add")
    assert add_page.status_code == 200
    assert b"Pending for Estimate Approval" in add_page.data

    update_page = client.get("/tasks/TSK-20260708-D4E5F6/edit")
    assert update_page.status_code == 200
    assert b"Pending for Estimate Approval" in update_page.data


def test_pending_estimate_approval_can_be_saved_as_normal_status(app, client):
    login_associate(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={
            "status": "Pending for Estimate Approval",
            "checker_email": "",
            "additional_details": "Estimate sent for approval.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Status"] == "Pending for Estimate Approval"


def test_task_creation_writes_backend_activity_log(app, client):
    login_associate(client)
    response = client.post(
        "/tasks/add",
        data={
            "client_code": "CL001",
            "matter_project": "Audit trail creation",
            "task_description": "Create task with backend audit log",
            "assigned_to_email": "associate@taskapp.local",
            "assigned_by": "Associate One",
            "assigned_date": "2026-07-27",
            "due_date": "2026-08-10",
            "priority": "Important",
            "status": "Yet to Start",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    created = next(
        row
        for row in tasks(app)
        if row["Task Description"] == "Create task with backend audit log"
    )
    logged = [
        row for row in activity_rows(app) if row["Task ID"] == created["Task ID"]
    ]
    assert [row["Activity Type"] for row in logged] == [
        "Task Created",
        "Task Assigned",
        "Due Date Assigned",
    ]
    assert all(row["Activity At"].endswith("+05:30") for row in logged)
    assert all(row["Updated By"] == "Associate One" for row in logged)


def test_status_change_writes_backend_activity_log(app, client):
    login_associate(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={"status": "In Progress", "checker_email": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    logged = [
        row
        for row in activity_rows(app)
        if row["Task ID"] == "TSK-20260708-D4E5F6"
    ]
    status_rows = [row for row in logged if row["Activity Type"] == "Status Changed"]
    assert status_rows
    latest = status_rows[-1]
    assert latest["Previous Status"] == "Pending for Client"
    assert latest["New Status"] == "In Progress"
    assert latest["Updated By Email"] == "associate@taskapp.local"


def test_checking_decision_writes_backend_activity_log(app, client):
    login_associate(client)
    submit_for_checking(client)
    login_checker(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/checking/complete",
        data={"checking_comment": "Checked and accepted."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    logged = [
        row
        for row in activity_rows(app)
        if row["Task ID"] == "TSK-20260708-D4E5F6"
    ]
    accepted = [row for row in logged if row["Activity Type"] == "Checking Accepted"]
    assert accepted
    assert accepted[-1]["Checking Outcome"] == "Accepted"
    assert accepted[-1]["Comment"] == "Checked and accepted."
    assert accepted[-1]["New Status"] == "Pending Completion Approval"


def test_normal_user_dashboard_is_personal(client):
    login_associate(client)
    response = client.get("/")
    assert response.status_code == 200
    assert b"Welcome, Associate One" in response.data
    assert b"Today Focus" in response.data
    assert b"My Priority Tasks" in response.data
    assert b"My Work Pipeline" in response.data
    assert b"My Checking Queue" in response.data
    assert b"Associate workload" not in response.data
    assert b"Pending completion approvals" not in response.data


def test_associate_can_toggle_today_focus_and_reselect_after_old_date(app, client):
    repo = app.extensions["task_repository"]
    task = task_by_id(app, "TSK-20260708-D4E5F6")
    task["Today Focus Date"] = "2000-01-01"
    task["Today Focus By"] = "Associate One"
    repo.update_task(task["Task ID"], task)

    login_associate(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/today-focus",
        data={"responsibility": "assigned", "today": "yes"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Today Focus Date"]
    assert updated["Today Focus Date"] != "2000-01-01"
    assert updated["Today Focus By"] == "Associate One"
    assert b"today-focus-badge" in response.data
    assert b"Today focus only" in response.data

    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/today-focus",
        data={"responsibility": "assigned"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Today Focus Date"] == ""
    assert updated["Today Focus By"] == ""


def test_normal_user_cannot_set_today_focus_for_another_users_task(app, client):
    login_associate(client)
    client.post(
        "/tasks/TSK-20260710-A1B2C3/today-focus",
        data={"responsibility": "assigned", "today": "yes"},
        follow_redirects=True,
    )
    task = task_by_id(app, "TSK-20260710-A1B2C3")
    assert task["Today Focus Date"] == ""


def test_checker_has_independent_today_focus(app, client):
    login_associate(client)
    submit_for_checking(client)

    login_checker(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/today-focus",
        data={"responsibility": "checking", "today": "yes"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Checking Today Focus Date"]
    assert updated["Checking Today Focus By"] == "Checker One"
    assert updated["Today Focus Date"] == ""


def test_today_focus_is_visible_on_all_ongoing_tasks(app, client):
    login_associate(client)
    client.post(
        "/tasks/TSK-20260708-D4E5F6/today-focus",
        data={"responsibility": "assigned", "today": "yes"},
        follow_redirects=True,
    )

    login_editor(client)
    response = client.get("/ongoing-tasks")
    assert response.status_code == 200
    assert b"task-today-focus" in response.data
    assert b"Today" in response.data


def test_editor_can_assign_blue_today_and_reassign_after_old_date(app, client):
    repo = app.extensions["task_repository"]
    task = task_by_id(app, "TSK-20260708-D4E5F6")
    task["Authorised Today Date"] = "2000-01-01"
    task["Authorised Today By"] = "Old Editor"
    task["Authorised Today At"] = "2000-01-01 09:00:00"
    repo.update_task(task["Task ID"], task)

    login_editor(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/authorised-today",
        data={"responsibility": "assigned", "enabled": "yes"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Authorised Today Date"]
    assert updated["Authorised Today Date"] != "2000-01-01"
    assert updated["Authorised Today By"] == "Demo User"
    assert updated["Authorised Today At"]
    assert b"authorised-today-badge" in response.data
    assert b"Assign for Today" in response.data

    logged = [
        row
        for row in activity_rows(app)
        if row["Task ID"] == "TSK-20260708-D4E5F6"
    ]
    assert any(
        row["Activity Type"] == "Authorised Today Added"
        for row in logged
    )


def test_normal_user_sees_but_cannot_change_blue_today(app, client):
    login_editor(client)
    client.post(
        "/tasks/TSK-20260708-D4E5F6/authorised-today",
        data={"responsibility": "assigned", "enabled": "yes"},
    )
    assigned_date = task_by_id(
        app, "TSK-20260708-D4E5F6"
    )["Authorised Today Date"]

    login_associate(client)
    response = client.get("/my-tasks")
    assert response.status_code == 200
    assert b"authorised-today-badge" in response.data
    assert b"Assigned by Demo User" in response.data
    assert b"Assign for Today" not in response.data
    assert b"My Today" in response.data

    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/authorised-today",
        data={"responsibility": "assigned"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Only authorised users can access this page." in response.data
    unchanged = task_by_id(app, "TSK-20260708-D4E5F6")
    assert unchanged["Authorised Today Date"] == assigned_date
    assert unchanged["Authorised Today By"] == "Demo User"


def test_blue_today_responsibility_sorts_first_on_my_tasks(app, client):
    repo = app.extensions["task_repository"]
    second = dict(task_by_id(app, "TSK-20260708-D4E5F6"))
    second.update(
        {
            "Task ID": "TSK-BLUE-SORT-002",
            "Task Description": "Earlier due ordinary task",
            "Due Date": "2000-01-02",
            "Authorised Today Date": "",
            "Authorised Today By": "",
            "Authorised Today At": "",
        }
    )
    repo.add_task(second)

    login_editor(client)
    client.post(
        "/tasks/TSK-20260708-D4E5F6/authorised-today",
        data={"responsibility": "assigned", "enabled": "yes"},
    )

    login_associate(client)
    response = client.get("/my-tasks")
    assert response.status_code == 200
    blue_position = response.data.find(
        b"Obtain pending property card"
    )
    ordinary_position = response.data.find(
        b"Earlier due ordinary task"
    )
    assert blue_position != -1
    assert ordinary_position != -1
    assert blue_position < ordinary_position


def test_editor_can_assign_blue_today_to_checking_responsibility(app, client):
    login_associate(client)
    submit_for_checking(client)

    login_editor(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/authorised-today",
        data={"responsibility": "checking", "enabled": "yes"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Checking Authorised Today Date"]
    assert updated["Checking Authorised Today By"] == "Demo User"
    assert updated["Authorised Today Date"] == ""

    login_checker(client)
    response = client.get("/my-tasks")
    assert response.status_code == 200
    assert b"FOR CHECKING" in response.data
    assert b"authorised-today-badge" in response.data
    assert b"Assigned by Demo User" in response.data
    assert b"Assign for Today" not in response.data


def test_assigned_blue_today_is_blocked_after_submit_for_checking(app, client):
    login_associate(client)
    submit_for_checking(client)

    login_editor(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/authorised-today",
        data={"responsibility": "assigned", "enabled": "yes"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Assign for Today is not available for an assigned task" in response.data
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Authorised Today Date"] == ""


def test_assigned_blue_today_is_blocked_pending_completion_approval(app, client):
    login_associate(client)
    submit_for_completion_approval(client)

    login_editor(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/authorised-today",
        data={"responsibility": "assigned", "enabled": "yes"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Assign for Today is not available for an assigned task" in response.data
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Authorised Today Date"] == ""


def test_ongoing_template_keeps_checking_checkbox_but_hides_blocked_assigned_checkbox():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "ongoing_tasks.html"
    )
    source = template_path.read_text(encoding="utf-8")
    assert "is_checking_card" in source
    assert "status not in blocked_assigned_today_statuses" in source
    assert "'pending for checking'" in source
    assert "'pending completion approval'" in source


def test_ongoing_colour_mapping_and_checking_flag(client):
    login_editor(client)
    response = client.get("/ongoing-tasks")
    assert response.status_code == 200
    assert b"Task Backgrounds:" in response.data
    assert b"colour-for-checking" in response.data
    assert "⚑ For Checking".encode("utf-8") in response.data

    css = client.get("/static/css/style.css")
    assert css.status_code == 200
    for rule in [
        b".task-status-not-started { background: #ffffff; }",
        b".task-status-progress { background: #dbeeff; }",
        b".task-status-client { background: #d1d5db; }",
        b".task-status-checking { background: #fff4b8; }",
        b".task-status-approval { background: #dcf5e3; }",
        b"background: #ebe4ff;",
        b"content: \"\xe2\x9a\x91 \";",
    ]:
        assert rule in css.data


def make_old_completed_task(app, task_id="TSK-20260701-G7H8I9"):
    repository = app.extensions["task_repository"]
    task = task_by_id(app, task_id)
    task["Status"] = "Completed"
    task["Completion Date"] = "2000-01-15"
    task["Completion Approved At"] = "2000-01-15 15:10:00"
    task["Archived"] = ""
    task["Archived At"] = ""
    task["Archived By"] = ""
    repository.update_task(task_id, task)
    return task


def test_completed_page_excludes_archived_and_archive_page_includes_it(app, client):
    make_old_completed_task(app)
    login_editor(client)

    response = client.post("/archive-completed-tasks", follow_redirects=True)
    assert response.status_code == 200
    assert b"completed task(s) were archived successfully" in response.data

    archived = task_by_id(app, "TSK-20260701-G7H8I9")
    assert archived["Archived"] == "Yes"
    assert archived["Archived At"]
    assert archived["Archived By"] == "Demo User"

    completed_response = client.get("/completed-tasks?q=Monthly+Compliance")
    assert b"Complete the monthly document checklist" not in completed_response.data

    archive_response = client.get("/archived-tasks?q=Monthly+Compliance")
    assert archive_response.status_code == 200
    assert b"Complete the monthly document checklist" in archive_response.data
    assert b"ARCHIVED" in archive_response.data


def test_archived_page_has_same_filters_as_completed_page(app, client):
    make_old_completed_task(app)
    login_editor(client)
    client.post("/archive-completed-tasks")

    response = client.get(
        "/archived-tasks?"
        "q=Monthly&assigned_to=Demo+User&client=INT001&"
        "priority=Normal&completed_month=2000-01"
    )
    assert response.status_code == 200
    assert b'name="q"' in response.data
    assert b'name="assigned_to"' in response.data
    assert b'name="client"' in response.data
    assert b'name="priority"' in response.data
    assert b'name="completed_month"' in response.data
    assert b"Complete the monthly document checklist" in response.data


def test_only_authorised_user_can_archive(app, client):
    make_old_completed_task(app)
    login_associate(client)
    response = client.post("/archive-completed-tasks", follow_redirects=True)
    assert response.status_code == 200
    assert task_by_id(app, "TSK-20260701-G7H8I9")["Archived"] == ""


def test_authorised_user_can_restore_archived_task(app, client):
    make_old_completed_task(app)
    login_editor(client)
    client.post("/archive-completed-tasks")

    response = client.post(
        "/tasks/TSK-20260701-G7H8I9/restore-archive",
        follow_redirects=True,
    )
    assert response.status_code == 200
    restored = task_by_id(app, "TSK-20260701-G7H8I9")
    assert restored["Archived"] == ""
    assert restored["Archived At"] == ""
    assert restored["Archived By"] == ""


def test_authorised_dashboard_pending_checking_is_personal_checker_queue(app, client):
    repository = app.extensions["task_repository"]
    task = task_by_id(app, "TSK-20260708-D4E5F6")
    task["Status"] = "Pending for Checking"
    task["Checking Status"] = "Pending"
    task["Checker Name"] = "Checker One"
    task["Checker Email"] = "checker@taskapp.local"
    repository.update_task(task["Task ID"], task)

    # Demo User is authorised but is not the checker for this task.
    login_editor(client)
    response = client.get("/")
    assert response.status_code == 200
    assert b"<span>For Checking</span><strong>0</strong>" in response.data


def test_associate_can_edit_task_description_and_additional_details(app, client):
    login_associate(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={
            "status": "In Progress",
            "checker_email": "",
            "task_description": "Updated task description by associate",
            "additional_details": "Called client and updated the working note.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Task Description"] == "Updated task description by associate"
    assert updated["Additional Details"] == "Called client and updated the working note."


def test_additional_details_preserved_as_comment_when_submitting_for_checking(app, client):
    login_associate(client)
    response = client.post(
        "/tasks/TSK-20260708-D4E5F6/edit",
        data={
            "status": "Pending for Checking",
            "checker_email": "checker@taskapp.local",
            "task_description": "Obtain pending property card",
            "additional_details": "Draft completed; please verify schedule and annexures.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, "TSK-20260708-D4E5F6")
    assert updated["Status"] == "Pending for Checking"
    assert updated["Additional Details"] == "Draft completed; please verify schedule and annexures."




def test_completed_page_shows_billing_controls_and_filter(client):
    login_editor(client)
    response = client.get("/completed-tasks")
    assert response.status_code == 200
    assert b"Pending for Billing" in response.data
    assert b'name="billing_status"' in response.data
    assert b"Billing Completed" in response.data


def test_authorised_user_can_mark_completed_task_pending_for_billing(app, client):
    task_id = "TSK-20260701-G7H8I9"
    login_editor(client)
    response = client.post(
        f"/tasks/{task_id}/pending-billing",
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, task_id)
    assert updated["Pending for Billing"] == "Yes"
    assert updated["Billing Flagged By"] == "Demo User"
    assert updated["Billing Flagged At"]
    assert updated["Billing Completed"] == ""


def test_ordinary_assignee_can_mark_own_completed_task_pending_for_billing(app, client):
    task_id = "TSK-20260701-G7H8I9"
    original = task_by_id(app, task_id)
    changed = dict(original)
    changed["Assigned To"] = "Associate One"
    changed["Assigned To Email"] = "associate@taskapp.local"
    app.extensions["task_repository"].update_task(task_id, changed)

    login_associate(client)
    response = client.post(
        f"/tasks/{task_id}/pending-billing",
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = task_by_id(app, task_id)
    assert updated["Pending for Billing"] == "Yes"
    assert updated["Billing Flagged By"] == "Associate One"


def test_only_billing_admin_can_close_pending_billing(app, client):
    task_id = "TSK-20260701-G7H8I9"
    login_editor(client)
    client.post(f"/tasks/{task_id}/pending-billing")
    assert task_by_id(app, task_id)["Pending for Billing"] == "Yes"

    set_login(client, "second-editor@taskapp.local", "Second Editor")
    response = client.post(
        f"/tasks/{task_id}/billing-complete",
        follow_redirects=True,
    )
    assert response.status_code == 200
    denied = task_by_id(app, task_id)
    assert denied["Pending for Billing"] == "Yes"
    assert denied["Billing Completed"] in {"", "No"}
    assert b"Only the designated Billing Admin can close billing" in response.data

    login_editor(client)
    response = client.post(
        f"/tasks/{task_id}/billing-complete",
        follow_redirects=True,
    )
    assert response.status_code == 200
    completed = task_by_id(app, task_id)
    assert completed["Pending for Billing"] == "No"
    assert completed["Billing Completed"] == "Yes"
    assert completed["Billing Completed By"] == "Demo User"
    assert completed["Billing Completed At"]


def test_pending_billing_filter_only_shows_billing_task_records(app, client):
    task_id = "TSK-20260701-G7H8I9"
    login_editor(client)
    client.post(f"/tasks/{task_id}/pending-billing")
    response = client.get("/completed-tasks?billing_status=pending")
    assert response.status_code == 200
    assert b"PENDING FOR BILLING" in response.data
    assert b"CHECKING COMPLETED" not in response.data
