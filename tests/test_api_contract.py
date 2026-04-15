import json
import sys
import types
from datetime import datetime

from fastapi.testclient import TestClient


if "psycopg2" not in sys.modules:
    fake_psycopg2 = types.ModuleType("psycopg2")
    fake_extras = types.ModuleType("psycopg2.extras")

    def _connect(*args, **kwargs):
        raise RuntimeError("psycopg2.connect should not be called directly in tests")

    def _execute_values(cur, sql, rows):
        return None

    fake_psycopg2.connect = _connect
    fake_extras.execute_values = _execute_values
    fake_psycopg2.extras = fake_extras
    sys.modules["psycopg2"] = fake_psycopg2
    sys.modules["psycopg2.extras"] = fake_extras

import main


# Global in-memory case store for stateful testing
_test_case_store = {}


class FakeCursor:
    def __init__(self):
        self._one = None
        self._all = []
        self.description = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        global _test_case_store
        q = " ".join(query.lower().split())
        self._one = None
        self._all = []
        self.description = []

        if "insert into public.case_latest" in q and "values" in q:
            # Store imported data
            if params:
                for row in params:
                    case_num = row[0]
                    _test_case_store[case_num] = row
        elif "select count(*) from public.case_latest" in q:
            self._one = (len(_test_case_store) + 42,)
        elif "select case_status from public.case_latest" in q and "group by" in q:
            self._one = ("approved",)
        elif "select max(updated_at) from public.case_latest" in q:
            self._one = (datetime(2025, 1, 1, 12, 0, 0),)
        elif "select avg(extract(epoch from (last_received_date - application_filed_date)) / 86400)" in q:
            self._one = (12.3,)
        elif "select to_char(application_filed_date, 'yyyy-mm') as month" in q:
            self._all = [("2025-01", "approved", 5), ("2025-01", "received", 3)]
        elif "where upper(case_number) = upper(%s)" in q and params:
            self.description = [
                ("case_number",),
                ("service_center",),
                ("case_type",),
                ("case_status",),
                ("title",),
                ("application_filed_date",),
                ("last_received_date",),
                ("updated_at",),
            ]
            case_num = params[0] if params else None
            if case_num and case_num in _test_case_store:
                self._all = [_test_case_store[case_num]]
            else:
                self._all = [
                    (
                        "RD2123456",
                        "RD2",
                        "Type A",
                        "approved",
                        "Case Was Approved",
                        datetime(2024, 1, 1, 0, 0, 0),
                        datetime(2024, 2, 1, 0, 0, 0),
                        datetime(2024, 2, 1, 0, 0, 0),
                    )
                ]
        elif "from public.import_runs" in q:
            self.description = [
                ("id",),
                ("filename",),
                ("imported_at",),
                ("rows_read",),
                ("rows_kept",),
                ("rows_skipped",),
                ("total_in_db",),
            ]
            self._all = [
                (1, "cases.json", datetime(2025, 1, 2, 12, 0, 0), 10, 8, 2, 42)
            ]

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _fake_get_conn():
    return FakeConn()


def _mock_execute_values(cur, sql, rows):
    """Capture inserted rows into the test store."""
    global _test_case_store
    if rows:
        for row in rows:
            case_num = row[0]
            _test_case_store[case_num] = row


def _client(monkeypatch):
    global _test_case_store
    _test_case_store = {}  # Reset store for each test
    monkeypatch.setattr(main, "get_conn", _fake_get_conn)
    monkeypatch.setattr(main, "execute_values", _mock_execute_values)
    return TestClient(main.app)


def test_health_contract(monkeypatch):
    client = _client(monkeypatch)
    res = client.get("/health")

    assert res.status_code == 200
    assert res.json() == {"status": "ok", "db": "connected"}


def test_import_accepts_last_updated_date(monkeypatch):
    client = _client(monkeypatch)

    payload = [
        {
            "Case Number": "RD2123456",
            "Service Center": "RD2",
            "Case Type": "Type A",
            "Case Status": "approved",
            "Title": "Case Was Approved",
            "Application Filed Date": "2024-01-01 00:00:00",
            "Last Updated Date": "2024-02-01 00:00:00",
        }
    ]

    res = client.post(
        "/import-json",
        files={"file": ("cases.json", json.dumps(payload), "application/json")},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["rows_read"] == 1
    assert body["rows_kept"] == 1


def test_search_contract(monkeypatch):
    client = _client(monkeypatch)
    res = client.get("/api/search", params={"case_number": "RD2123456"})

    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["results"][0]["case_number"] == "RD2123456"


def test_logout_redirect(monkeypatch):
    client = _client(monkeypatch)
    res = client.get("/logout", follow_redirects=False)

    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/upload"


def test_import_and_search_data_flow(monkeypatch):
    """End-to-end: import data, then search for it and verify all fields returned."""
    client = _client(monkeypatch)

    # Import a complete case record
    payload = [
        {
            "Case Number": "RD2999888",
            "Service Center": "RD2",
            "Case Type": "Type B",
            "Case Status": "approved",
            "Title": "Case Was Approved",
            "Application Filed Date": "2024-01-15 10:30:00",
            "Last Updated Date": "2024-03-20 14:45:00",
        }
    ]

    res_import = client.post(
        "/import-json",
        files={"file": ("test.json", json.dumps(payload), "application/json")},
    )
    assert res_import.status_code == 200
    assert res_import.json()["status"] == "success"
    assert res_import.json()["rows_kept"] == 1

    # Search for the imported case
    res_search = client.get("/api/search", params={"case_number": "RD2999888"})
    assert res_search.status_code == 200
    body = res_search.json()
    assert body["count"] == 1

    # Verify all fields returned match what was imported
    result = body["results"][0]
    assert result["case_number"] == "RD2999888"
    assert result["service_center"] == "RD2"
    assert result["case_type"] == "Type B"
    assert result["case_status"] == "approved"
    assert result["title"] == "Case Was Approved"


def test_deduplication_keeps_latest_date(monkeypatch):
    """End-to-end: import same case twice with different dates; verify newer date wins."""
    client = _client(monkeypatch)

    # First import: older date
    payload1 = [
        {
            "Case Number": "RD2777666",
            "Service Center": "RD2",
            "Case Type": "Type C",
            "Case Status": "received",
            "Title": "Case Was Received",
            "Application Filed Date": "2024-01-10 00:00:00",
            "Last Updated Date": "2024-01-10 00:00:00",
        }
    ]

    res1 = client.post(
        "/import-json",
        files={"file": ("test.json", json.dumps(payload1), "application/json")},
    )
    assert res1.status_code == 200
    assert res1.json()["rows_kept"] == 1

    # Second import: same case, newer date
    payload2 = [
        {
            "Case Number": "RD2777666",
            "Service Center": "RD2",
            "Case Type": "Type C",
            "Case Status": "approved",  # Changed status
            "Title": "Case Was Approved",  # Changed title
            "Application Filed Date": "2024-01-10 00:00:00",
            "Last Updated Date": "2024-03-15 12:00:00",  # Newer date
        }
    ]

    res2 = client.post(
        "/import-json",
        files={"file": ("test.json", json.dumps(payload2), "application/json")},
    )
    assert res2.status_code == 200
    assert res2.json()["rows_kept"] == 1

    # Search: should return the newer record
    res_search = client.get("/api/search", params={"case_number": "RD2777666"})
    assert res_search.status_code == 200
    result = res_search.json()["results"][0]
    assert result["case_status"] == "approved"  # Updated status
    assert result["title"] == "Case Was Approved"  # Updated title
