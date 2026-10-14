import asyncio
from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup

from scraper.portals import aeries


DASHBOARD = """<div id="divClass">
  <div class="Card"><a class="TextHeading">Math</a><div class="Grade"><span>0%</span></div></div>
  <div class="Card"><a class="TextHeading">English</a><div class="Grade"><span>A</span></div></div>
  <div class="Card"><a class="TextHeading">No marks</a><div class="Grade"><span></span></div></div>
</div>"""


@pytest.mark.parametrize("report_exists,report,expected", [
    (True, {}, {"MATH": 0.0, "ENGLISH": 95.0}),
    (False, {}, {"MATH": 0.0, "ENGLISH": 95.0}),
    (True, {"MATH": 82.0}, {"MATH": 82.0}),
])
def test_dashboard_grades_survive_an_empty_report_card(monkeypatch, report_exists, report, expected):
    page = AsyncMock()
    page.url = "https://school.aeries.net/student/Dashboard.aspx"
    engine = aeries.Aeries(page, "student", "password", page.url)
    engine.get_soup = AsyncMock(return_value=BeautifulSoup(DASHBOARD, "html.parser"))
    engine.nav_to_grades = AsyncMock(return_value=report_exists)
    monkeypatch.setattr(aeries, "grades_table_to_dict", AsyncMock(return_value=report))

    assert asyncio.run(engine.fetch_grades()) == expected


def test_dashboard_is_reloaded_after_selecting_student(monkeypatch):
    page = AsyncMock()
    page.url = "https://school.aeries.net/student/Dashboard.aspx"
    engine = aeries.Aeries(page, "student", "password", page.url)
    engine.get_soup = AsyncMock(side_effect=[
        BeautifulSoup("", "html.parser"),
        BeautifulSoup(DASHBOARD, "html.parser"),
        BeautifulSoup(DASHBOARD, "html.parser"),
    ])
    engine.nav_to_grades = AsyncMock(return_value=False)
    monkeypatch.setattr(aeries, "grades_table_to_dict", AsyncMock(return_value={}))

    assert asyncio.run(engine.fetch_grades()) == {"MATH": 0.0, "ENGLISH": 95.0}


def test_dashboard_waits_for_delayed_grade_content_before_leaving(monkeypatch):
    page = AsyncMock()
    page.url = "https://school.aeries.net/student/Dashboard.aspx"
    engine = aeries.Aeries(page, "student", "password", page.url)
    loading = DASHBOARD.replace('0%', '').replace('>A<', '><')
    engine.get_soup = AsyncMock(side_effect=[
        BeautifulSoup(loading, 'html.parser'),
        BeautifulSoup(DASHBOARD, 'html.parser'),
        BeautifulSoup(DASHBOARD, 'html.parser'),
    ])
    engine.nav_to_grades = AsyncMock(return_value=True)
    monkeypatch.setattr(aeries, 'grades_table_to_dict', AsyncMock(return_value={}))
    assert asyncio.run(engine.fetch_grades()) == {'MATH': 0.0, 'ENGLISH': 95.0}


def test_permanently_ungraded_dashboard_still_reads_report_cards_with_a_bound(monkeypatch):
    page = AsyncMock()
    page.url = "https://school.aeries.net/student/Dashboard.aspx"
    engine = aeries.Aeries(page, 'student', 'password', page.url)
    blank = BeautifulSoup(DASHBOARD.replace('0%', '').replace('>A<', '><'), 'html.parser')
    engine.get_soup = AsyncMock(return_value=blank)
    engine.nav_to_grades = AsyncMock(return_value=True)
    monkeypatch.setattr(aeries, 'grades_table_to_dict', AsyncMock(return_value={'MATH': 70.0}))
    ticks = iter(range(100))
    monkeypatch.setattr(aeries, 'monotonic', lambda: next(ticks))
    assert asyncio.run(engine.fetch_grades()) == {'MATH': 70.0}
    assert engine.get_soup.await_count <= 12
