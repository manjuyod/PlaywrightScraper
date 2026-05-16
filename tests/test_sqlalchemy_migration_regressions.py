from db import Student
import db
from scraper import runner


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_runner_student_query_uses_tuple_params(monkeypatch):
    class FakeConnection:
        def exec_driver_sql(self, query, params=None):
            if "FROM student_auth" in query:
                return FakeResult([])
            assert isinstance(params, tuple)
            return FakeResult([])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(runner, "db_conn", lambda: FakeConnection())

    assert runner.get_students_from_db(status="synced") == []


def test_add_student_fetches_returning_id_before_commit(monkeypatch):
    class ReturningResult(FakeResult):
        def __init__(self, conn):
            super().__init__([{"id": 42}])
            self._conn = conn

        def fetchone(self):
            assert self._conn.committed is False
            return super().fetchone()

    class FakeConnection:
        def __init__(self):
            self.committed = False

        def exec_driver_sql(self, _query, _params):
            return ReturningResult(self)

        def commit(self):
            self.committed = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    fake_conn = FakeConnection()
    student = Student(
        id=0,
        grade_level=10,
        first_name="Ada",
        last_name="Lovelace",
        grades={},
        status="never",
        portal="",
        portal_url="https://example.test",
        portal_username="ada",
        portal_password="secret",
    )

    monkeypatch.setattr(db, "db_conn", lambda: fake_conn)
    monkeypatch.setattr(db, "get_student", lambda student_id: student_id)

    assert db.add_student(1, student, b"0" * 32) == 42
    assert fake_conn.committed is True
