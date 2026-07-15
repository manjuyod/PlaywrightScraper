from __future__ import annotations

import inspect

from ui import ext_jobs


class _Future:
    def __init__(self):
        self.callback = None

    def add_done_callback(self, callback):
        self.callback = callback

    def cancelled(self):
        return False

    def exception(self):
        return None


class _Executor:
    def __init__(self):
        self.future = _Future()

    def submit(self, _function, coroutine):
        assert inspect.iscoroutine(coroutine)
        coroutine.close()
        return self.future


def test_grade_background_job_keeps_caller_contract_without_insert_phase(monkeypatch) -> None:
    executor = _Executor()

    async def grade_checker(**_kwargs):
        return None

    monkeypatch.setattr(ext_jobs, "executor", executor)
    monkeypatch.setattr(ext_jobs, "grade_checker", grade_checker)
    ext_jobs.jobs.clear()
    ext_jobs.runners.clear()

    assert ext_jobs.start_grade_fetch_job("19_42", total=1) == "19_42"
    assert ext_jobs.jobs["19_42"].steps == 3
    assert "19_42" in ext_jobs.runners

    executor.future.callback(executor.future)
    assert "19_42" not in ext_jobs.runners


def test_ext_jobs_has_no_obsolete_grade_insertion_callback() -> None:
    source = inspect.getsource(ext_jobs)

    assert "insert_grades" not in source
