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
                # case-insensitive fallback, mirroring UPPER() in real SQL
                upper_key = case_num.upper() if case_num else None
                matched = next(
                    (v for k, v in _test_case_store.items() if k.upper() == upper_key),
                    None,
                )
                if matched:
                    self._all = [matched]
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
        elif "select to_char(application_filed_date, 'yyyy-mm-dd') as day" in q:
            self._all = [("2025-01-15", "approved", 3), ("2025-01-16", "received", 2)]
        elif "where to_char(application_filed_date, 'yyyy-mm') = %s" in q:
            self._all = [("approved", 5), ("received", 3)]
        elif "where date(application_filed_date) = %s" in q and "order by application_filed_date" in q:
            self.description = [
                ("case_number",), ("service_center",), ("case_type",),
                ("case_status",), ("title",), ("application_filed_date",),
                ("last_received_date",), ("updated_at",),
            ]
            self._all = [
                (
                    "RD2123456", "RD2", "Type A", "approved", "Case Was Approved",
                    datetime(2024, 1, 1), datetime(2024, 2, 1), datetime(2024, 2, 1),
                )
            ]
        elif "where date(application_filed_date) = %s" in q and "group by case_status" in q:
            self._all = [("approved", 4), ("received", 2)]

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
    """Verifies /health returns 200 with status ok and db connected."""
    client = _client(monkeypatch)
    res = client.get("/health")

    assert res.status_code == 200
    assert res.json() == {"status": "ok", "db": "connected"}


def test_import_accepts_last_updated_date(monkeypatch):
    """Verifies a valid single-record import returns success with rows_read and rows_kept of 1."""
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
    """Verifies /api/search by case number returns count and a results list."""
    client = _client(monkeypatch)
    res = client.get("/api/search", params={"case_number": "RD2123456"})

    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["results"][0]["case_number"] == "RD2123456"


def test_logout_redirect(monkeypatch):
    """Verifies /logout redirects to /upload."""
    client = _client(monkeypatch)
    res = client.get("/logout", follow_redirects=False)

    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/upload"


def test_import_and_search_data_flow(monkeypatch):
    """Imports a record then searches for it and verifies all fields are returned correctly."""
    client = _client(monkeypatch)

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

    res_search = client.get("/api/search", params={"case_number": "RD2999888"})
    assert res_search.status_code == 200
    body = res_search.json()
    assert body["count"] == 1

    result = body["results"][0]
    assert result["case_number"] == "RD2999888"
    assert result["service_center"] == "RD2"
    assert result["case_type"] == "Type B"
    assert result["case_status"] == "approved"
    assert result["title"] == "Case Was Approved"


def test_deduplication_keeps_latest_date(monkeypatch):
    """Imports the same case twice with different dates and verifies the newer record wins."""
    client = _client(monkeypatch)

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

    payload2 = [
        {
            "Case Number": "RD2777666",
            "Service Center": "RD2",
            "Case Type": "Type C",
            "Case Status": "approved",
            "Title": "Case Was Approved",
            "Application Filed Date": "2024-01-10 00:00:00",
            "Last Updated Date": "2024-03-15 12:00:00",
        }
    ]

    res2 = client.post(
        "/import-json",
        files={"file": ("test.json", json.dumps(payload2), "application/json")},
    )
    assert res2.status_code == 200
    assert res2.json()["rows_kept"] == 1

    res_search = client.get("/api/search", params={"case_number": "RD2777666"})
    assert res_search.status_code == 200
    result = res_search.json()["results"][0]
    assert result["case_status"] == "approved"
    assert result["title"] == "Case Was Approved"


# ── New tests ─────────────────────────────────────────────────────────────────

def test_root_redirects_to_upload(monkeypatch):
    """Verifies GET / redirects to /upload."""
    client = _client(monkeypatch)
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert "/upload" in res.headers["location"]


def test_upload_page_returns_html(monkeypatch):
    """Verifies the /upload page returns a 200 HTML response."""
    client = _client(monkeypatch)
    res = client.get("/upload")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_stats_contract(monkeypatch):
    """Verifies /api/stats returns all expected summary fields with valid types."""
    client = _client(monkeypatch)
    res = client.get("/api/stats")
    assert res.status_code == 200
    body = res.json()
    assert "total_cases" in body
    assert "top_status" in body
    assert "last_import" in body
    assert "avg_processing_days" in body
    assert body["total_cases"] >= 0


def test_import_runs_contract(monkeypatch):
    """Verifies /api/import-runs returns a list of runs each containing filename and row counts."""
    client = _client(monkeypatch)
    res = client.get("/api/import-runs")
    assert res.status_code == 200
    body = res.json()
    assert "runs" in body
    assert isinstance(body["runs"], list)
    assert len(body["runs"]) >= 1
    run = body["runs"][0]
    assert "filename" in run
    assert "rows_read" in run
    assert "rows_kept" in run


def test_charts_by_month_contract(monkeypatch):
    """Verifies /api/charts/by-month returns a data dict keyed by month."""
    client = _client(monkeypatch)
    res = client.get("/api/charts/by-month")
    assert res.status_code == 200
    body = res.json()
    assert "data" in body
    assert isinstance(body["data"], dict)


def test_charts_by_day_contract(monkeypatch):
    """Verifies /api/charts/by-day returns data grouped by day for the requested month."""
    client = _client(monkeypatch)
    res = client.get("/api/charts/by-day", params={"month": "2025-01"})
    assert res.status_code == 200
    body = res.json()
    assert body["month"] == "2025-01"
    assert "data" in body
    assert isinstance(body["data"], dict)


def test_charts_pie_month_contract(monkeypatch):
    """Verifies /api/charts/pie-month returns a list of status counts for the requested month."""
    client = _client(monkeypatch)
    res = client.get("/api/charts/pie-month", params={"month": "2025-01"})
    assert res.status_code == 200
    body = res.json()
    assert body["month"] == "2025-01"
    assert "data" in body
    assert isinstance(body["data"], list)


def test_charts_pie_day_filed_contract(monkeypatch):
    """Verifies /api/charts/pie-day-filed returns a list of status counts for the requested date."""
    client = _client(monkeypatch)
    res = client.get("/api/charts/pie-day-filed", params={"date": "2025-01-15"})
    assert res.status_code == 200
    body = res.json()
    assert body["date"] == "2025-01-15"
    assert "data" in body
    assert isinstance(body["data"], list)


def test_search_no_params_returns_400(monkeypatch):
    """Verifies /api/search with no parameters returns 400."""
    client = _client(monkeypatch)
    res = client.get("/api/search")
    assert res.status_code == 400


def test_import_invalid_json_returns_400(monkeypatch):
    """Verifies uploading unparseable bytes returns 400 with an Invalid JSON detail."""
    client = _client(monkeypatch)
    res = client.post(
        "/import-json",
        files={"file": ("bad.json", b"not valid json", "application/json")},
    )
    assert res.status_code == 400
    assert "Invalid JSON" in res.json()["detail"]


def test_import_not_array_returns_400(monkeypatch):
    """Verifies uploading a JSON object instead of an array returns 400."""
    client = _client(monkeypatch)
    payload = json.dumps({"Case Number": "RD2000001"})
    res = client.post(
        "/import-json",
        files={"file": ("obj.json", payload.encode(), "application/json")},
    )
    assert res.status_code == 400
    assert "array" in res.json()["detail"].lower()


def test_import_missing_keys_returns_400(monkeypatch):
    """Verifies uploading a record missing required fields returns 400 with a Missing keys detail."""
    client = _client(monkeypatch)
    payload = json.dumps([{"Case Number": "RD2000002"}])
    res = client.post(
        "/import-json",
        files={"file": ("miss.json", payload.encode(), "application/json")},
    )
    assert res.status_code == 400
    assert "Missing keys" in res.json()["detail"]


def test_import_empty_array(monkeypatch):
    """Verifies importing an empty array returns success with rows_read and rows_kept of 0."""
    client = _client(monkeypatch)
    res = client.post(
        "/import-json",
        files={"file": ("empty.json", b"[]", "application/json")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["rows_read"] == 0
    assert body["rows_kept"] == 0


def test_import_skips_blank_case_number(monkeypatch):
    """Verifies a record with a blank Case Number is skipped and counted in rows_skipped."""
    client = _client(monkeypatch)
    payload = json.dumps([
        {
            "Case Number": "",
            "Service Center": "RD2",
            "Case Type": "Type A",
            "Case Status": "approved",
            "Title": "Blank Case",
            "Application Filed Date": "2024-01-01 00:00:00",
            "Last Updated Date": "2024-02-01 00:00:00",
        }
    ])
    res = client.post(
        "/import-json",
        files={"file": ("blank.json", payload.encode(), "application/json")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["rows_read"] == 1
    assert body["rows_kept"] == 0
    assert body["rows_skipped"] == 1


def test_import_dedup_within_payload(monkeypatch):
    """Verifies that when a payload contains duplicate case numbers only the newer date is kept."""
    client = _client(monkeypatch)
    payload = json.dumps([
        {
            "Case Number": "RD2555444",
            "Service Center": "RD2",
            "Case Type": "Type A",
            "Case Status": "received",
            "Title": "Old",
            "Application Filed Date": "2024-01-01 00:00:00",
            "Last Updated Date": "2024-01-01 00:00:00",
        },
        {
            "Case Number": "RD2555444",
            "Service Center": "RD2",
            "Case Type": "Type A",
            "Case Status": "approved",
            "Title": "New",
            "Application Filed Date": "2024-01-01 00:00:00",
            "Last Updated Date": "2024-06-01 00:00:00",
        },
    ])
    res = client.post(
        "/import-json",
        files={"file": ("dup.json", payload.encode(), "application/json")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["rows_read"] == 2
    assert body["rows_kept"] == 1


def test_search_by_date_contract(monkeypatch):
    """Verifies /api/search by application filed date returns results and count."""
    client = _client(monkeypatch)
    res = client.get("/api/search", params={"date": "2024-01-01"})
    assert res.status_code == 200
    body = res.json()
    assert "results" in body
    assert "count" in body
    assert isinstance(body["results"], list)


def test_import_normalises_status_to_lowercase(monkeypatch):
    """Verifies Case Status is lowercased on import regardless of the original casing."""
    client = _client(monkeypatch)
    payload = json.dumps([
        {
            "Case Number": "RD2111001",
            "Service Center": "RD2",
            "Case Type": "Type A",
            "Case Status": "APPROVED",
            "Title": "Upper Case Status",
            "Application Filed Date": "2024-01-01 00:00:00",
            "Last Updated Date": "2024-02-01 00:00:00",
        }
    ])
    res = client.post(
        "/import-json",
        files={"file": ("norm.json", payload.encode(), "application/json")},
    )
    assert res.status_code == 200
    assert res.json()["rows_kept"] == 1

    res_search = client.get("/api/search", params={"case_number": "RD2111001"})
    assert res_search.status_code == 200
    result = res_search.json()["results"][0]
    assert result["case_status"] == "approved"


def test_search_case_insensitive(monkeypatch):
    """Verifies searching with a lowercase case number still returns the correct record."""
    client = _client(monkeypatch)
    payload = json.dumps([
        {
            "Case Number": "RD2111002",
            "Service Center": "RD2",
            "Case Type": "Type A",
            "Case Status": "approved",
            "Title": "Case Insensitive Search",
            "Application Filed Date": "2024-01-01 00:00:00",
            "Last Updated Date": "2024-02-01 00:00:00",
        }
    ])
    client.post(
        "/import-json",
        files={"file": ("ci.json", payload.encode(), "application/json")},
    )

    res = client.get("/api/search", params={"case_number": "rd2111002"})
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["results"][0]["case_number"] == "RD2111002"


def test_import_date_only_format(monkeypatch):
    """Verifies date strings in YYYY-MM-DD format (no time) are accepted and imported."""
    client = _client(monkeypatch)
    payload = json.dumps([
        {
            "Case Number": "RD2111003",
            "Service Center": "RD2",
            "Case Type": "Type A",
            "Case Status": "received",
            "Title": "Date Only Format",
            "Application Filed Date": "2024-03-10",
            "Last Updated Date": "2024-04-01",
        }
    ])
    res = client.post(
        "/import-json",
        files={"file": ("dateonly.json", payload.encode(), "application/json")},
    )
    assert res.status_code == 200
    assert res.json()["rows_kept"] == 1


def test_import_skips_record_with_null_last_updated_date(monkeypatch):
    """Verifies a record with a null Last Updated Date is skipped and counted in rows_skipped."""
    client = _client(monkeypatch)
    payload = json.dumps([
        {
            "Case Number": "RD2111004",
            "Service Center": "RD2",
            "Case Type": "Type A",
            "Case Status": "received",
            "Title": "No Date",
            "Application Filed Date": "2024-01-01 00:00:00",
            "Last Updated Date": None,
        }
    ])
    res = client.post(
        "/import-json",
        files={"file": ("nulldate.json", payload.encode(), "application/json")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["rows_read"] == 1
    assert body["rows_kept"] == 0
    assert body["rows_skipped"] == 1


def test_import_multi_record(monkeypatch):
    """Verifies importing a payload of 3 distinct cases keeps all 3 with rows_skipped of 0."""
    client = _client(monkeypatch)
    payload = json.dumps([
        {
            "Case Number": f"RD2222{i:03d}",
            "Service Center": "RD2",
            "Case Type": "Type A",
            "Case Status": "received",
            "Title": f"Case {i}",
            "Application Filed Date": "2024-01-01 00:00:00",
            "Last Updated Date": "2024-02-01 00:00:00",
        }
        for i in range(3)
    ])
    res = client.post(
        "/import-json",
        files={"file": ("multi.json", payload.encode(), "application/json")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["rows_read"] == 3
    assert body["rows_kept"] == 3
    assert body["rows_skipped"] == 0


def test_import_response_echoes_filename(monkeypatch):
    """Verifies the import response body includes the original uploaded filename."""
    client = _client(monkeypatch)
    payload = json.dumps([
        {
            "Case Number": "RD2333001",
            "Service Center": "RD2",
            "Case Type": "Type A",
            "Case Status": "approved",
            "Title": "Filename Echo",
            "Application Filed Date": "2024-01-01 00:00:00",
            "Last Updated Date": "2024-02-01 00:00:00",
        }
    ])
    res = client.post(
        "/import-json",
        files={"file": ("my_export.json", payload.encode(), "application/json")},
    )
    assert res.status_code == 200
    assert res.json()["filename"] == "my_export.json"


def test_search_result_dates_are_iso_strings(monkeypatch):
    """Verifies date fields in search results are serialised as ISO 8601 strings."""
    client = _client(monkeypatch)
    res = client.get("/api/search", params={"case_number": "RD2123456"})
    assert res.status_code == 200
    result = res.json()["results"][0]
    for field in ("application_filed_date", "last_received_date", "updated_at"):
        assert isinstance(result[field], str), f"{field} should be a string"
        # ISO format contains a 'T' separator
        assert "T" in result[field], f"{field} should be ISO format"
