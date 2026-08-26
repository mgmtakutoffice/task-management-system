from __future__ import annotations

import threading
import unittest

from config import TASK_ACTIVITY_HEADERS
from repository import GoogleSheetsRepository, RepositoryError


class _ExecuteResult:
    def __init__(self, result=None):
        self.result = result or {}

    def execute(self):
        return self.result


class _FakeValues:
    def __init__(self):
        self.append_calls = []
        self.get_calls = []
        self.update_calls = []
        self.get_result = {"values": []}

    def append(self, **kwargs):
        self.append_calls.append(kwargs)
        return _ExecuteResult({})

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return _ExecuteResult(self.get_result)

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return _ExecuteResult({})


class _FakeSpreadsheets:
    def __init__(self):
        self.values_api = _FakeValues()

    def values(self):
        return self.values_api


class _FakeService:
    def __init__(self):
        self.sheets_api = _FakeSpreadsheets()

    def spreadsheets(self):
        return self.sheets_api


class SeparateActivitySpreadsheetTests(unittest.TestCase):
    def _repository(self):
        repo = object.__new__(GoogleSheetsRepository)
        repo._google_lock = threading.RLock()
        repo.service = _FakeService()
        repo.spreadsheet_id = "main-sheet-id"
        repo.activity_spreadsheet_id = "activity-sheet-id"
        repo.activity_sheet_name = "Task Activity Log"
        return repo

    def test_activity_log_append_targets_separate_spreadsheet(self):
        repo = self._repository()
        repo.add_task_activities(
            [{"Activity ID": "A1", "Task ID": "T1", "Activity Type": "Task Updated"}]
        )

        calls = repo.service.sheets_api.values_api.append_calls
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["spreadsheetId"], "activity-sheet-id")
        self.assertIn("'Task Activity Log'!A:", calls[0]["range"])

    def test_activity_header_creation_targets_separate_spreadsheet(self):
        repo = self._repository()
        repo._ensure_header(
            "Task Activity Log",
            TASK_ACTIVITY_HEADERS,
            spreadsheet_id=repo.activity_spreadsheet_id,
        )
        values_api = repo.service.sheets_api.values_api
        self.assertEqual(values_api.get_calls[0]["spreadsheetId"], "activity-sheet-id")
        self.assertEqual(values_api.update_calls[0]["spreadsheetId"], "activity-sheet-id")

    def test_missing_activity_sheet_id_is_rejected_before_google_client_setup(self):
        with self.assertRaisesRegex(RepositoryError, "ACTIVITY_SHEET_ID is required"):
            GoogleSheetsRepository(
                spreadsheet_id="main-sheet-id",
                credentials_json="",
                credentials_file="",
                tasks_sheet_name="Tasks",
                users_sheet_name="Users",
                masters_sheet_name="Masters",
                clients_sheet_name="Clients",
                activity_spreadsheet_id="",
                activity_sheet_name="Task Activity Log",
            )

    def test_same_main_and_activity_sheet_ids_are_rejected(self):
        with self.assertRaisesRegex(RepositoryError, "must point to a separate"):
            GoogleSheetsRepository(
                spreadsheet_id="same-id",
                credentials_json="",
                credentials_file="",
                tasks_sheet_name="Tasks",
                users_sheet_name="Users",
                masters_sheet_name="Masters",
                clients_sheet_name="Clients",
                activity_spreadsheet_id="same-id",
                activity_sheet_name="Task Activity Log",
            )


if __name__ == "__main__":
    unittest.main()
