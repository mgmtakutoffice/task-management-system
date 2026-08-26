from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from config import (
    BASE_DIR,
    CLIENT_HEADERS,
    DEFAULT_CATEGORIES,
    DEFAULT_PRIORITIES,
    DEFAULT_STATUSES,
    MASTER_HEADERS,
    NOTIFICATION_HEADERS,
    PUSH_SUBSCRIPTION_HEADERS,
    TASK_ACTIVITY_HEADERS,
    TASK_HEADERS,
    USER_HEADERS,
)


class RepositoryError(RuntimeError):
    """Raised for configuration, storage, or validation failures."""


class TaskRepository(ABC):
    @abstractmethod
    def ensure_structure(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_tasks(self) -> list[dict[str, str]]:
        raise NotImplementedError

    @abstractmethod
    def add_task(self, task: dict[str, str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def update_task(self, task_id: str, task: dict[str, str]) -> None:
        raise NotImplementedError

    def get_task_activities(self) -> list[dict[str, str]]:
        """Return the append-only task activity history.

        Kept as a non-abstract extension so older test doubles/subclasses do not
        become un-instantiable merely because the digest feature is added.
        """
        raise NotImplementedError

    @abstractmethod
    def add_task_activities(
        self, activities: list[dict[str, str]]
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def add_notification(self, notification: dict[str, str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_notifications(self, user_email: str) -> list[dict[str, str]]:
        raise NotImplementedError

    @abstractmethod
    def mark_notification_read(
        self, notification_id: str, user_email: str, read_at: str
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def save_push_subscription(self, subscription: dict[str, str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_push_subscriptions(
        self, user_email: str
    ) -> list[dict[str, str]]:
        raise NotImplementedError

    @abstractmethod
    def deactivate_push_subscription(
        self, endpoint: str, user_email: str, last_seen_at: str
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_users(self) -> list[dict[str, str]]:
        raise NotImplementedError

    @abstractmethod
    def get_clients(self) -> list[dict[str, str]]:
        raise NotImplementedError

    @abstractmethod
    def add_client(self, client: dict[str, str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_masters(self) -> dict[str, list[str]]:
        raise NotImplementedError


def _row_to_dict(headers: list[str], row: list[Any]) -> dict[str, str]:
    padded = list(row) + [""] * max(0, len(headers) - len(row))
    return {
        header: str(padded[index]).strip()
        for index, header in enumerate(headers)
    }


def _dict_to_row(headers: list[str], record: dict[str, Any]) -> list[str]:
    return [str(record.get(header, "")) for header in headers]


def _active_value(value: str) -> bool:
    return str(value or "Yes").strip().lower() not in {
        "no",
        "false",
        "0",
        "inactive",
    }


class LocalJsonRepository(TaskRepository):
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.tasks_file = self.data_dir / "tasks.json"
        self.users_file = self.data_dir / "users.json"
        self.clients_file = self.data_dir / "clients.json"
        self.masters_file = self.data_dir / "masters.json"
        self.activity_file = self.data_dir / "task_activity_log.json"
        self.notifications_file = self.data_dir / "notifications.json"
        self.push_subscriptions_file = self.data_dir / "push_subscriptions.json"
        self._lock = threading.RLock()

    def ensure_structure(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not self.tasks_file.exists():
                self._write_json(self.tasks_file, [])
            if not self.users_file.exists():
                self._write_json(self.users_file, [])
            if not self.clients_file.exists():
                self._write_json(self.clients_file, [])
            if not self.activity_file.exists():
                self._write_json(self.activity_file, [])
            if not self.notifications_file.exists():
                self._write_json(self.notifications_file, [])
            if not self.push_subscriptions_file.exists():
                self._write_json(self.push_subscriptions_file, [])
            if not self.masters_file.exists():
                self._write_json(
                    self.masters_file,
                    {
                        "Associates": [],
                        "Clients": [],
                        "Status": DEFAULT_STATUSES,
                        "Priority": DEFAULT_PRIORITIES,
                        "Task Categories": DEFAULT_CATEGORIES,
                    },
                )

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temp_path, path)

    def get_tasks(self) -> list[dict[str, str]]:
        with self._lock:
            tasks = self._read_json(self.tasks_file, [])
        return [
            {header: str(row.get(header, "")) for header in TASK_HEADERS}
            for row in tasks
        ]

    def add_task(self, task: dict[str, str]) -> None:
        with self._lock:
            tasks = self._read_json(self.tasks_file, [])
            tasks.append(
                {header: str(task.get(header, "")) for header in TASK_HEADERS}
            )
            self._write_json(self.tasks_file, tasks)

    def update_task(self, task_id: str, task: dict[str, str]) -> None:
        with self._lock:
            tasks = self._read_json(self.tasks_file, [])
            for index, existing in enumerate(tasks):
                if str(existing.get("Task ID", "")).strip() == task_id:
                    tasks[index] = {
                        header: str(task.get(header, ""))
                        for header in TASK_HEADERS
                    }
                    self._write_json(self.tasks_file, tasks)
                    return
        raise RepositoryError(f"Task {task_id} was not found.")

    def get_task_activities(self) -> list[dict[str, str]]:
        with self._lock:
            rows = self._read_json(self.activity_file, [])
        return [
            {
                header: str(row.get(header, ""))
                for header in TASK_ACTIVITY_HEADERS
            }
            for row in rows
            if isinstance(row, dict)
        ]

    def add_task_activities(
        self, activities: list[dict[str, str]]
    ) -> None:
        if not activities:
            return
        with self._lock:
            rows = self._read_json(self.activity_file, [])
            rows.extend(
                {
                    header: str(activity.get(header, ""))
                    for header in TASK_ACTIVITY_HEADERS
                }
                for activity in activities
            )
            self._write_json(self.activity_file, rows)

    def add_notification(self, notification: dict[str, str]) -> None:
        with self._lock:
            rows = self._read_json(self.notifications_file, [])
            rows.append(
                {
                    header: str(notification.get(header, ""))
                    for header in NOTIFICATION_HEADERS
                }
            )
            self._write_json(self.notifications_file, rows)

    def get_notifications(self, user_email: str) -> list[dict[str, str]]:
        normalized_email = str(user_email).strip().lower()
        with self._lock:
            rows = self._read_json(self.notifications_file, [])
        return [
            {
                header: str(row.get(header, ""))
                for header in NOTIFICATION_HEADERS
            }
            for row in rows
            if str(row.get("User Email", "")).strip().lower()
            == normalized_email
        ]

    def mark_notification_read(
        self, notification_id: str, user_email: str, read_at: str
    ) -> bool:
        normalized_email = str(user_email).strip().lower()
        with self._lock:
            rows = self._read_json(self.notifications_file, [])
            for row in rows:
                if (
                    str(row.get("Notification ID", "")).strip()
                    == notification_id
                    and str(row.get("User Email", "")).strip().lower()
                    == normalized_email
                ):
                    row["Read"] = "Yes"
                    row["Read At"] = read_at
                    self._write_json(self.notifications_file, rows)
                    return True
        return False

    def save_push_subscription(self, subscription: dict[str, str]) -> None:
        endpoint = str(subscription.get("Endpoint", "")).strip()
        if not endpoint:
            raise RepositoryError("Push subscription endpoint is required.")
        with self._lock:
            rows = self._read_json(self.push_subscriptions_file, [])
            for index, row in enumerate(rows):
                if str(row.get("Endpoint", "")).strip() == endpoint:
                    created_at = str(row.get("Created At", "")).strip()
                    updated = {
                        header: str(subscription.get(header, ""))
                        for header in PUSH_SUBSCRIPTION_HEADERS
                    }
                    if created_at:
                        updated["Created At"] = created_at
                    rows[index] = updated
                    self._write_json(self.push_subscriptions_file, rows)
                    return
            rows.append(
                {
                    header: str(subscription.get(header, ""))
                    for header in PUSH_SUBSCRIPTION_HEADERS
                }
            )
            self._write_json(self.push_subscriptions_file, rows)

    def get_push_subscriptions(
        self, user_email: str
    ) -> list[dict[str, str]]:
        normalized_email = str(user_email).strip().lower()
        with self._lock:
            rows = self._read_json(self.push_subscriptions_file, [])
        return [
            {
                header: str(row.get(header, ""))
                for header in PUSH_SUBSCRIPTION_HEADERS
            }
            for row in rows
            if str(row.get("User Email", "")).strip().lower()
            == normalized_email
            and str(row.get("Active", "Yes")).strip().lower()
            not in {"no", "false", "0", "inactive"}
        ]

    def deactivate_push_subscription(
        self, endpoint: str, user_email: str, last_seen_at: str
    ) -> bool:
        normalized_email = str(user_email).strip().lower()
        endpoint = str(endpoint).strip()
        with self._lock:
            rows = self._read_json(self.push_subscriptions_file, [])
            changed = False
            for row in rows:
                if (
                    str(row.get("Endpoint", "")).strip() == endpoint
                    and str(row.get("User Email", "")).strip().lower()
                    == normalized_email
                ):
                    row["Active"] = "No"
                    row["Last Seen At"] = last_seen_at
                    changed = True
                    break
            if changed:
                self._write_json(self.push_subscriptions_file, rows)
            return changed

    def get_users(self) -> list[dict[str, str]]:
        with self._lock:
            users = self._read_json(self.users_file, [])
        return [
            {header: str(row.get(header, "")) for header in USER_HEADERS}
            for row in users
        ]

    def get_clients(self) -> list[dict[str, str]]:
        with self._lock:
            clients = self._read_json(self.clients_file, [])
        return [
            {header: str(row.get(header, "")) for header in CLIENT_HEADERS}
            for row in clients
        ]

    def add_client(self, client: dict[str, str]) -> None:
        client_code = str(client.get("Client Code", "")).strip()
        if not client_code:
            raise RepositoryError("Client Code is required.")

        with self._lock:
            clients = self._read_json(self.clients_file, [])
            duplicate = any(
                str(row.get("Client Code", "")).strip().lower()
                == client_code.lower()
                for row in clients
            )
            if duplicate:
                raise RepositoryError(
                    f"Client Code {client_code} already exists."
                )
            clients.append(
                {
                    header: str(client.get(header, ""))
                    for header in CLIENT_HEADERS
                }
            )
            self._write_json(self.clients_file, clients)

    def get_masters(self) -> dict[str, list[str]]:
        with self._lock:
            data = self._read_json(self.masters_file, {})
        return {
            "Associates": [
                str(value).strip()
                for value in data.get("Associates", [])
                if str(value).strip()
            ],
            "Clients": [
                str(value).strip()
                for value in data.get("Clients", [])
                if str(value).strip()
            ],
            "Status": [
                str(value).strip()
                for value in data.get("Status", DEFAULT_STATUSES)
                if str(value).strip()
            ],
            "Priority": [
                str(value).strip()
                for value in data.get("Priority", DEFAULT_PRIORITIES)
                if str(value).strip()
            ],
            "Task Categories": [
                str(value).strip()
                for value in data.get("Task Categories", DEFAULT_CATEGORIES)
                if str(value).strip()
            ],
        }


class GoogleSheetsRepository(TaskRepository):
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(
        self,
        spreadsheet_id: str,
        credentials_json: str,
        credentials_file: str,
        tasks_sheet_name: str,
        users_sheet_name: str,
        masters_sheet_name: str,
        clients_sheet_name: str,
        notifications_sheet_name: str,
        push_subscriptions_sheet_name: str,
        activity_spreadsheet_id: str,
        activity_sheet_name: str,
    ):
        if not spreadsheet_id:
            raise RepositoryError(
                "SHEET_ID is required when DATA_BACKEND=google."
            )
        if not activity_spreadsheet_id:
            raise RepositoryError(
                "ACTIVITY_SHEET_ID is required when DATA_BACKEND=google. "
                "Create a separate Google Spreadsheet for the Task Activity Log "
                "and share it with the same service account as Editor."
            )
        if activity_spreadsheet_id == spreadsheet_id:
            raise RepositoryError(
                "ACTIVITY_SHEET_ID must point to a separate Google Spreadsheet, "
                "not the main SHEET_ID."
            )

        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RepositoryError(
                "Google API packages are not installed. "
                "Run: pip install -r requirements.txt"
            ) from exc

        try:
            if credentials_json:
                info = json.loads(credentials_json)
                credentials = Credentials.from_service_account_info(
                    info,
                    scopes=self.SCOPES,
                )
            elif credentials_file:
                credential_path = Path(credentials_file).expanduser()
                if not credential_path.is_absolute():
                    credential_path = BASE_DIR / credential_path
                if not credential_path.exists():
                    raise RepositoryError(
                        f"Google credentials file was not found: "
                        f"{credential_path}"
                    )
                credentials = Credentials.from_service_account_file(
                    str(credential_path),
                    scopes=self.SCOPES,
                )
            else:
                raise RepositoryError(
                    "Set GOOGLE_CREDENTIALS_JSON or GOOGLE_CREDENTIALS_FILE "
                    "when DATA_BACKEND=google."
                )
        except json.JSONDecodeError as exc:
            raise RepositoryError(
                "GOOGLE_CREDENTIALS_JSON is not valid JSON."
            ) from exc

        # googleapiclient uses an httplib2 transport that must not be used
        # concurrently by multiple Flask/Gunicorn threads. Every Google API
        # call in this repository is therefore protected by the same re-entrant
        # lock. RLock is required because compound operations such as
        # update_task() call other locked repository methods.
        self._google_lock = threading.RLock()

        self.service = build(
            "sheets",
            "v4",
            credentials=credentials,
            cache_discovery=False,
        )
        self.spreadsheet_id = spreadsheet_id
        self.tasks_sheet_name = tasks_sheet_name
        self.users_sheet_name = users_sheet_name
        self.masters_sheet_name = masters_sheet_name
        self.clients_sheet_name = clients_sheet_name
        self.notifications_sheet_name = notifications_sheet_name
        self.push_subscriptions_sheet_name = push_subscriptions_sheet_name
        self.activity_spreadsheet_id = activity_spreadsheet_id
        self.activity_sheet_name = activity_sheet_name

    def _get_values(
        self,
        sheet_name: str,
        range_suffix: str = "A:ZZ",
        *,
        spreadsheet_id: str | None = None,
    ) -> list[list[str]]:
        target_spreadsheet_id = spreadsheet_id or self.spreadsheet_id
        with self._google_lock:
            result = (
                self.service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=target_spreadsheet_id,
                    range=f"'{sheet_name}'!{range_suffix}",
                )
                .execute()
            )
        return result.get("values", [])

    def _update_values(
        self,
        range_name: str,
        values: list[list[str]],
        *,
        spreadsheet_id: str | None = None,
    ) -> None:
        target_spreadsheet_id = spreadsheet_id or self.spreadsheet_id
        with self._google_lock:
            (
                self.service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=target_spreadsheet_id,
                    range=range_name,
                    valueInputOption="USER_ENTERED",
                    body={"values": values},
                )
                .execute()
            )

    def _append_values(
        self,
        sheet_name: str,
        headers: list[str],
        row: list[str],
        *,
        spreadsheet_id: str | None = None,
    ) -> None:
        target_spreadsheet_id = spreadsheet_id or self.spreadsheet_id
        end_column = self._column_letter(len(headers))
        with self._google_lock:
            (
                self.service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=target_spreadsheet_id,
                    range=f"'{sheet_name}'!A:{end_column}",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [row]},
                )
                .execute()
            )

    def ensure_structure(self) -> None:
        with self._google_lock:
            spreadsheet = (
                self.service.spreadsheets()
                .get(
                    spreadsheetId=self.spreadsheet_id,
                    fields="sheets.properties",
                )
                .execute()
            )
        existing = {
            sheet["properties"]["title"]
            for sheet in spreadsheet.get("sheets", [])
        }
        required = [
            self.tasks_sheet_name,
            self.users_sheet_name,
            self.masters_sheet_name,
            self.clients_sheet_name,
            self.notifications_sheet_name,
            self.push_subscriptions_sheet_name,
        ]
        requests = [
            {"addSheet": {"properties": {"title": name}}}
            for name in required
            if name not in existing
        ]
        if requests:
            with self._google_lock:
                (
                    self.service.spreadsheets()
                    .batchUpdate(
                        spreadsheetId=self.spreadsheet_id,
                        body={"requests": requests},
                    )
                    .execute()
                )

        self._ensure_header(self.tasks_sheet_name, TASK_HEADERS)
        self._ensure_header(self.users_sheet_name, USER_HEADERS)
        self._ensure_header(self.masters_sheet_name, MASTER_HEADERS)
        self._ensure_header(self.clients_sheet_name, CLIENT_HEADERS)
        self._ensure_header(self.notifications_sheet_name, NOTIFICATION_HEADERS)
        self._ensure_header(
            self.push_subscriptions_sheet_name, PUSH_SUBSCRIPTION_HEADERS
        )

        # The append-only audit log lives in a completely separate Google
        # Spreadsheet. Keeping it outside the operational workbook prevents
        # log growth from inflating the main Task Manager spreadsheet.
        with self._google_lock:
            activity_spreadsheet = (
                self.service.spreadsheets()
                .get(
                    spreadsheetId=self.activity_spreadsheet_id,
                    fields="sheets.properties",
                )
                .execute()
            )
        activity_existing = {
            sheet["properties"]["title"]
            for sheet in activity_spreadsheet.get("sheets", [])
        }
        if self.activity_sheet_name not in activity_existing:
            with self._google_lock:
                (
                    self.service.spreadsheets()
                    .batchUpdate(
                        spreadsheetId=self.activity_spreadsheet_id,
                        body={
                            "requests": [
                                {
                                    "addSheet": {
                                        "properties": {
                                            "title": self.activity_sheet_name
                                        }
                                    }
                                }
                            ]
                        },
                    )
                    .execute()
                )
        self._ensure_header(
            self.activity_sheet_name,
            TASK_ACTIVITY_HEADERS,
            spreadsheet_id=self.activity_spreadsheet_id,
        )

        masters = self._get_values(self.masters_sheet_name, "A1:E20")
        if len(masters) <= 1:
            max_len = max(
                len(DEFAULT_STATUSES),
                len(DEFAULT_PRIORITIES),
                len(DEFAULT_CATEGORIES),
            )
            seed_rows: list[list[str]] = []
            for index in range(max_len):
                seed_rows.append(
                    [
                        "",
                        "",
                        DEFAULT_STATUSES[index]
                        if index < len(DEFAULT_STATUSES)
                        else "",
                        DEFAULT_PRIORITIES[index]
                        if index < len(DEFAULT_PRIORITIES)
                        else "",
                        DEFAULT_CATEGORIES[index]
                        if index < len(DEFAULT_CATEGORIES)
                        else "",
                    ]
                )
            self._update_values(
                f"'{self.masters_sheet_name}'!A2:E{max_len + 1}",
                seed_rows,
            )

    def _ensure_header(
        self,
        sheet_name: str,
        expected: list[str],
        *,
        spreadsheet_id: str | None = None,
    ) -> None:
        values = self._get_values(
            sheet_name,
            "1:1",
            spreadsheet_id=spreadsheet_id,
        )
        current = values[0] if values else []

        if not current:
            end_column = self._column_letter(len(expected))
            self._update_values(
                f"'{sheet_name}'!A1:{end_column}1",
                [expected],
                spreadsheet_id=spreadsheet_id,
            )
            return

        # Safe migration: the existing headers are an exact prefix of the
        # revised headers, such as Tasks before Client Code/Entity Name.
        if current == expected[: len(current)]:
            if current != expected:
                end_column = self._column_letter(len(expected))
                self._update_values(
                    f"'{sheet_name}'!A1:{end_column}1",
                    [expected],
                    spreadsheet_id=spreadsheet_id,
                )
            return

        if current != expected:
            raise RepositoryError(
                f"The header order in the '{sheet_name}' sheet does not "
                "match the application. Please use the included Google "
                "Sheet template or correct Row 1 before starting the app."
            )

    @staticmethod
    def _column_letter(number: int) -> str:
        result = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def get_tasks(self) -> list[dict[str, str]]:
        # Read only the columns that belong to TASK_HEADERS. The generic
        # _get_values default extends to ZZ, which can pull hundreds of
        # irrelevant columns if stray/formula values exist in the sheet and
        # can create a large transient memory spike on a 512 MB instance.
        end_column = self._column_letter(len(TASK_HEADERS))
        values = self._get_values(self.tasks_sheet_name, f"A:{end_column}")
        if not values:
            return []
        headers = values[0]
        records: list[dict[str, str]] = []
        for sheet_row, row in enumerate(values[1:], start=2):
            if not any(str(value).strip() for value in row):
                continue
            source = _row_to_dict(headers, row)
            normalized = {
                header: source.get(header, "")
                for header in TASK_HEADERS
            }
            normalized["_sheet_row"] = str(sheet_row)
            records.append(normalized)
        return records

    def add_task(self, task: dict[str, str]) -> None:
        self._append_values(
            self.tasks_sheet_name,
            TASK_HEADERS,
            _dict_to_row(TASK_HEADERS, task),
        )

    def update_task(self, task_id: str, task: dict[str, str]) -> None:
        # Keep the row lookup and row update in one critical section. This
        # prevents another request from changing the sheet between locating
        # the task row and writing the revised task.
        with self._google_lock:
            target = next(
                (
                    row
                    for row in self.get_tasks()
                    if row.get("Task ID") == task_id
                ),
                None,
            )
            if not target:
                raise RepositoryError(f"Task {task_id} was not found.")
            row_number = int(target["_sheet_row"])
            end_column = self._column_letter(len(TASK_HEADERS))
            self._update_values(
                f"'{self.tasks_sheet_name}'!A{row_number}:"
                f"{end_column}{row_number}",
                [_dict_to_row(TASK_HEADERS, task)],
            )

    def get_task_activities(self) -> list[dict[str, str]]:
        # Digest/reporting currently needs only Activity ID through Activity At.
        # Limiting the read to A:H avoids pulling comment/detail columns when the
        # activity log becomes large.
        values = self._get_values(
            self.activity_sheet_name,
            "A:H",
            spreadsheet_id=self.activity_spreadsheet_id,
        )
        if not values:
            return []
        headers = values[0]
        records: list[dict[str, str]] = []
        for row in values[1:]:
            if not any(str(value).strip() for value in row):
                continue
            source = _row_to_dict(headers, row)
            records.append(
                {
                    header: source.get(header, "")
                    for header in TASK_ACTIVITY_HEADERS
                }
            )
        return records

    def add_task_activities(
        self, activities: list[dict[str, str]]
    ) -> None:
        if not activities:
            return
        rows = [
            _dict_to_row(TASK_ACTIVITY_HEADERS, activity)
            for activity in activities
        ]
        end_column = self._column_letter(len(TASK_ACTIVITY_HEADERS))
        with self._google_lock:
            (
                self.service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=self.activity_spreadsheet_id,
                    range=(
                        f"'{self.activity_sheet_name}'!A:{end_column}"
                    ),
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": rows},
                )
                .execute()
            )

    def add_notification(self, notification: dict[str, str]) -> None:
        self._append_values(
            self.notifications_sheet_name,
            NOTIFICATION_HEADERS,
            _dict_to_row(NOTIFICATION_HEADERS, notification),
        )

    def get_notifications(self, user_email: str) -> list[dict[str, str]]:
        normalized_email = str(user_email).strip().lower()
        values = self._get_values(self.notifications_sheet_name, "A:J")
        if not values:
            return []
        headers = values[0]
        records: list[dict[str, str]] = []
        for sheet_row, row in enumerate(values[1:], start=2):
            if not any(str(value).strip() for value in row):
                continue
            source = _row_to_dict(headers, row)
            if (
                str(source.get("User Email", "")).strip().lower()
                != normalized_email
            ):
                continue
            normalized = {
                header: source.get(header, "")
                for header in NOTIFICATION_HEADERS
            }
            normalized["_sheet_row"] = str(sheet_row)
            records.append(normalized)
        return records

    def mark_notification_read(
        self, notification_id: str, user_email: str, read_at: str
    ) -> bool:
        normalized_email = str(user_email).strip().lower()
        with self._google_lock:
            target = next(
                (
                    row
                    for row in self.get_notifications(normalized_email)
                    if row.get("Notification ID") == notification_id
                ),
                None,
            )
            if not target:
                return False
            row_number = int(target["_sheet_row"])
            self._update_values(
                f"'{self.notifications_sheet_name}'!I{row_number}:J{row_number}",
                [["Yes", read_at]],
            )
        return True

    def save_push_subscription(self, subscription: dict[str, str]) -> None:
        endpoint = str(subscription.get("Endpoint", "")).strip()
        if not endpoint:
            raise RepositoryError("Push subscription endpoint is required.")

        with self._google_lock:
            values = self._get_values(self.push_subscriptions_sheet_name, "A:J")
            headers = values[0] if values else PUSH_SUBSCRIPTION_HEADERS
            target_row_number: int | None = None
            existing_created_at = ""
            for sheet_row, row in enumerate(values[1:], start=2):
                source = _row_to_dict(headers, row)
                if str(source.get("Endpoint", "")).strip() == endpoint:
                    target_row_number = sheet_row
                    existing_created_at = str(source.get("Created At", "")).strip()
                    break

            normalized = {
                header: str(subscription.get(header, ""))
                for header in PUSH_SUBSCRIPTION_HEADERS
            }
            if existing_created_at:
                normalized["Created At"] = existing_created_at

            if target_row_number is None:
                self._append_values(
                    self.push_subscriptions_sheet_name,
                    PUSH_SUBSCRIPTION_HEADERS,
                    _dict_to_row(PUSH_SUBSCRIPTION_HEADERS, normalized),
                )
            else:
                self._update_values(
                    f"'{self.push_subscriptions_sheet_name}'!"
                    f"A{target_row_number}:J{target_row_number}",
                    [_dict_to_row(PUSH_SUBSCRIPTION_HEADERS, normalized)],
                )

    def get_push_subscriptions(
        self, user_email: str
    ) -> list[dict[str, str]]:
        normalized_email = str(user_email).strip().lower()
        values = self._get_values(self.push_subscriptions_sheet_name, "A:J")
        if not values:
            return []
        headers = values[0]
        records: list[dict[str, str]] = []
        for sheet_row, row in enumerate(values[1:], start=2):
            if not any(str(value).strip() for value in row):
                continue
            source = _row_to_dict(headers, row)
            if (
                str(source.get("User Email", "")).strip().lower()
                != normalized_email
                or str(source.get("Active", "Yes")).strip().lower()
                in {"no", "false", "0", "inactive"}
            ):
                continue
            normalized = {
                header: source.get(header, "")
                for header in PUSH_SUBSCRIPTION_HEADERS
            }
            normalized["_sheet_row"] = str(sheet_row)
            records.append(normalized)
        return records

    def deactivate_push_subscription(
        self, endpoint: str, user_email: str, last_seen_at: str
    ) -> bool:
        normalized_email = str(user_email).strip().lower()
        endpoint = str(endpoint).strip()
        with self._google_lock:
            target = next(
                (
                    row
                    for row in self.get_push_subscriptions(normalized_email)
                    if row.get("Endpoint", "").strip() == endpoint
                ),
                None,
            )
            if not target:
                return False
            row_number = int(target["_sheet_row"])
            # Last Seen At = I, Active = J.
            self._update_values(
                f"'{self.push_subscriptions_sheet_name}'!I{row_number}:J{row_number}",
                [[last_seen_at, "No"]],
            )
        return True

    def get_users(self) -> list[dict[str, str]]:
        values = self._get_values(self.users_sheet_name, "A:D")
        if not values:
            return []
        headers = values[0]
        records: list[dict[str, str]] = []
        for row in values[1:]:
            if not any(str(value).strip() for value in row):
                continue
            source = _row_to_dict(headers, row)
            records.append(
                {header: source.get(header, "") for header in USER_HEADERS}
            )
        return records

    def get_clients(self) -> list[dict[str, str]]:
        values = self._get_values(self.clients_sheet_name, "A:F")
        if not values:
            return []
        headers = values[0]
        records: list[dict[str, str]] = []
        for row in values[1:]:
            if not any(str(value).strip() for value in row):
                continue
            source = _row_to_dict(headers, row)
            records.append(
                {header: source.get(header, "") for header in CLIENT_HEADERS}
            )
        return records

    def add_client(self, client: dict[str, str]) -> None:
        client_code = str(client.get("Client Code", "")).strip()
        if not client_code:
            raise RepositoryError("Client Code is required.")

        # Keep duplicate checking and insertion together so two simultaneous
        # requests cannot both add the same client code.
        with self._google_lock:
            duplicate = any(
                row.get("Client Code", "").strip().lower()
                == client_code.lower()
                for row in self.get_clients()
            )
            if duplicate:
                raise RepositoryError(
                    f"Client Code {client_code} already exists."
                )

            self._append_values(
                self.clients_sheet_name,
                CLIENT_HEADERS,
                _dict_to_row(CLIENT_HEADERS, client),
            )

    def get_masters(self) -> dict[str, list[str]]:
        values = self._get_values(self.masters_sheet_name, "A:E")
        columns = {header: [] for header in MASTER_HEADERS}
        if values:
            headers = values[0]
            for row in values[1:]:
                record = _row_to_dict(headers, row)
                for header in MASTER_HEADERS:
                    value = record.get(header, "").strip()
                    if value and value not in columns[header]:
                        columns[header].append(value)

        columns["Status"] = columns["Status"] or DEFAULT_STATUSES.copy()
        columns["Priority"] = (
            columns["Priority"] or DEFAULT_PRIORITIES.copy()
        )
        columns["Task Categories"] = (
            columns["Task Categories"] or DEFAULT_CATEGORIES.copy()
        )
        return columns


def build_repository(config: Any) -> TaskRepository:
    backend = str(config["DATA_BACKEND"]).lower()
    if backend == "google":
        repository: TaskRepository = GoogleSheetsRepository(
            spreadsheet_id=config["SHEET_ID"],
            credentials_json=config["GOOGLE_CREDENTIALS_JSON"],
            credentials_file=config["GOOGLE_CREDENTIALS_FILE"],
            tasks_sheet_name=config["TASKS_SHEET_NAME"],
            users_sheet_name=config["USERS_SHEET_NAME"],
            masters_sheet_name=config["MASTERS_SHEET_NAME"],
            clients_sheet_name=config["CLIENTS_SHEET_NAME"],
            notifications_sheet_name=config["NOTIFICATIONS_SHEET_NAME"],
            push_subscriptions_sheet_name=config["PUSH_SUBSCRIPTIONS_SHEET_NAME"],
            activity_spreadsheet_id=config["ACTIVITY_SHEET_ID"],
            activity_sheet_name=config["ACTIVITY_SHEET_NAME"],
        )
    elif backend == "local":
        repository = LocalJsonRepository(config["LOCAL_DATA_DIR"])
    else:
        raise RepositoryError(
            "DATA_BACKEND must be either 'local' or 'google'."
        )

    try:
        repository.ensure_structure()
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError(str(exc)) from exc
    return repository
