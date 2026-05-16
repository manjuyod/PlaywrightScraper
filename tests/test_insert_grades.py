import json

from scraper.work_flows import insert_grades as insert_grades_module


class FakeResult:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def mappings(self):
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

    def execute(self, *_args, **_kwargs):
        return self


class FakeConnection:
    def __init__(self, weeklydata: dict):
        self.weeklydata = weeklydata
        self.committed = False
        self.updated_weeklydata = None
        self.status_updates: list[tuple[str, tuple]] = []

    def commit(self):
        self.committed = True

    def exec_driver_sql(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        if params is None:
            params = ()
        if sql.startswith("select weeklydata from student where id = %s"):
            return FakeResult([{"weeklydata": self.weeklydata}])
        if sql.startswith("update student set weeklydata = %s where id = %s"):
            self.updated_weeklydata = params[0]
            return FakeResult([])
        if sql.startswith("update student set status = %s where id = %s"):
            self.status_updates.append(("status", params))
            return FakeResult([])
        if sql.startswith("update student set error_msg = null where id = %s"):
            self.status_updates.append(("error_msg_clear", params))
            return FakeResult([])
        if sql.startswith("update student set error_msg = %s where id = %s"):
            self.status_updates.append(("error_msg_set", params))
            return FakeResult([])
        raise AssertionError(f"Unexpected SQL: {query}")

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

    fake_conn = FakeConnection(history)

    monkeypatch.setattr(insert_grades_module, "JSONL_PATH", grades_path)
    monkeypatch.setattr(insert_grades_module, "db_conn", lambda: fake_conn)
    monkeypatch.setattr(insert_grades_module, "get_monday_anchor", lambda: "2026-03-23")

    insert_grades_module.insert_grades()

    assert fake_conn.committed is True
    assert fake_conn.updated_weeklydata is not None

    saved = json.loads(fake_conn.updated_weeklydata)
    assert saved["2026-03-09"] == {"Math": {"percentage": 88}}
    assert saved["2026-03-16"] == {"Science": {"percentage": 91}}
    assert saved["2026-03-23"] == {"English": {"percentage": 95}}
