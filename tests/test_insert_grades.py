from __future__ import annotations

import json

from scraper.work_flows import insert_grades as insert_grades_module


class FakeCursor:
    def __init__(self, weeklydata):
        self.weeklydata = weeklydata
        self.updated_weeklydata = None
        self.status_updates: list[tuple[str, tuple]] = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        if sql.startswith("select weeklydata from student where id = %s"):
            return
        if sql.startswith("update student set weeklydata = %s where id = %s"):
            self.updated_weeklydata = params[0]
            return
        if sql.startswith("update student set status = %s where id = %s"):
            self.status_updates.append(("status", params))
            return
        if sql.startswith("update student set error_msg = null where id = %s"):
            self.status_updates.append(("error_msg_clear", params))
            return
        if sql.startswith("update student set error_msg = %s where id = %s"):
            self.status_updates.append(("error_msg_set", params))
            return
        raise AssertionError(f"Unexpected SQL: {query}")

    def fetchone(self):
        return {"weeklydata": self.weeklydata}


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor
        self.committed = False
        self.info = "fake-db"

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_insert_grades_preserves_historical_weeklydata_when_db_returns_dict(tmp_path, monkeypatch):
    history = {
        "2026-03-09": {"Math": {"percentage": 88}},
        "2026-03-16": {"Science": {"percentage": 91}},
    }
    grades_path = tmp_path / "grades.jsonl"
    grades_path.write_text(
        json.dumps(
            {
                "db_id": 42,
                "id": "student-42",
                "parsed_grades": {"English": {"percentage": 95}},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    fake_cursor = FakeCursor(history)
    fake_conn = FakeConnection(fake_cursor)

    monkeypatch.setattr(insert_grades_module, "JSONL_PATH", grades_path)
    monkeypatch.setattr(insert_grades_module, "db_conn", lambda: fake_conn)
    monkeypatch.setattr(insert_grades_module, "get_monday_anchor", lambda: "2026-03-23")

    insert_grades_module.insert_grades()

    assert fake_conn.committed is True
    assert fake_cursor.updated_weeklydata is not None

    saved = json.loads(fake_cursor.updated_weeklydata)
    assert saved["2026-03-09"] == {"Math": {"percentage": 88}}
    assert saved["2026-03-16"] == {"Science": {"percentage": 91}}
    assert saved["2026-03-23"] == {"English": {"percentage": 95}}
