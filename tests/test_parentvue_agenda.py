from __future__ import annotations

import asyncio
from pathlib import Path

from scraper.agenda_contract import normalize_agenda
from scraper.portals.parentvue import ParentVUE
from scraper.portals.parentvue_agenda import ParentVueAgendaError, parse_parentvue_agenda


FIXTURE = Path(__file__).parent / "fixtures" / "parentvue_gradebook_agenda.html"


def test_parses_missing_and_upcoming_gradebook_assignments() -> None:
    """Would fail if ParentVUE misses semantic rows, dates, or stable identities."""
    records = parse_parentvue_agenda(FIXTURE.read_text(encoding="utf-8"))

    assert records == [
        {
            "course": "Algebra II",
            "title": "Linear review",
            "dueDate": "2026-08-11",
            "dueTime": None,
            "status": "missing",
            "sourceId": "parentvue:pv-41",
        },
        {
            "course": "Algebra II",
            "title": "Alternate title",
            "dueDate": "2026-08-12",
            "dueTime": None,
            "status": "missing",
            "sourceId": "parentvue:pv-43",
        },
        {
            "course": "English 11",
            "title": "Reading response",
            "dueDate": "2026-08-16",
            "dueTime": "23:59",
            "status": "due",
            "sourceId": "parentvue:pv-52",
        },
        {
            "course": "English 11",
            "title": "Hidden marker stays due",
            "dueDate": "2026-08-17",
            "dueTime": None,
            "status": "due",
            "sourceId": "parentvue:pv-53",
        },
        {
            "course": "Biology",
            "title": "Cell lab",
            "dueDate": "2026-08-18",
            "dueTime": "07:05",
            "status": "missing",
            "sourceId": "parentvue:pv-61",
        },
    ]


def test_empty_recognized_upcoming_section_returns_no_records() -> None:
    """Would fail if an empty Grade Book section is treated as a parse failure."""
    assert parse_parentvue_agenda(
        '<section><h2>Upcoming Assignments</h2></section>'
    ) == []


def test_raises_stable_error_for_unrecognizable_document() -> None:
    """Would fail if unrelated HTML is accepted as a Grade Book document."""
    try:
        parse_parentvue_agenda("<main><p>Signed in</p></main>")
    except ParentVueAgendaError as error:
        assert str(error) == "parentvue_agenda_parse_failed"
    else:
        raise AssertionError("unrecognizable document did not raise")


def test_hidden_missing_markers_do_not_override_upcoming_status() -> None:
    """Would fail if hidden Missing labels make upcoming work appear missing."""
    for hidden_attribute in ('hidden', 'aria-hidden="true"', 'style="display: none"', 'style="visibility:hidden"'):
        html = f'''<section><h2>Upcoming Assignments</h2>
          <div class="assignment-row" data-course-title="History">
            <span class="assignment-title">Primary source</span>
            <time datetime="2026-08-19">08/19/2026</time>
            <span class="status" {hidden_attribute}>Missing</span>
          </div></section>'''
        assert parse_parentvue_agenda(html)[0]["status"] == "due"


def test_hidden_ancestor_missing_marker_does_not_override_upcoming_status() -> None:
    """Would fail if visibility is checked only on the Missing marker itself."""
    html = '''<section><h2>Upcoming Assignments</h2>
      <div class="assignment-row" data-course-title="History">
        <span class="assignment-title">Primary source</span>
        <time datetime="2026-08-19">08/19/2026</time>
        <span aria-hidden=" TRUE "><span class="status">Missing</span></span>
      </div></section>'''

    assert parse_parentvue_agenda(html)[0]["status"] == "due"


def test_hidden_assignment_rows_and_hidden_ancestors_are_omitted() -> None:
    """Would fail if stale responsive rows remain actionable agenda records."""
    html = '''<section><h2>Upcoming Assignments</h2>
      <div class="assignment-row" data-course-title="History">
        <span class="assignment-title">Visible work</span>
        <time datetime="2026-08-19">08/19/2026</time>
      </div>
      <div class="assignment-row" data-course-title="History" hidden>
        <span class="assignment-title">Hidden attribute</span>
        <time datetime="2026-08-20">08/20/2026</time>
      </div>
      <div class="assignment-row" data-course-title="History" aria-hidden=" TRUE ">
        <span class="assignment-title">Aria hidden</span>
        <time datetime="2026-08-21">08/21/2026</time>
      </div>
      <div class="assignment-row" data-course-title="History" style=" DISPLAY : NONE ">
        <span class="assignment-title">Display hidden</span>
        <time datetime="2026-08-22">08/22/2026</time>
      </div>
      <div style=" visibility : HIDDEN ">
        <div class="assignment-row" data-course-title="History">
          <span class="assignment-title">Hidden ancestor</span>
          <time datetime="2026-08-23">08/23/2026</time>
        </div>
      </div>
    </section>'''

    records = parse_parentvue_agenda(html)

    assert [record["title"] for record in records] == ["Visible work"]


def test_important_hidden_rows_and_markers_are_omitted() -> None:
    """Would fail if terminal !important text bypasses inline visibility checks."""
    html = '''<section><h2>Upcoming Assignments</h2>
      <div class="assignment-row" data-course-title="History"
           style=" DISPLAY : NONE !important ">
        <span class="assignment-title">Display important row</span>
        <time datetime="2026-08-19">08/19/2026</time>
      </div>
      <div class="assignment-row" data-course-title="History"
           style="visibility: HIDDEN!important">
        <span class="assignment-title">Visibility important row</span>
        <time datetime="2026-08-20">08/20/2026</time>
      </div>
      <div class="assignment-row" data-course-title="History">
        <span class="assignment-title">Display important marker</span>
        <time datetime="2026-08-21">08/21/2026</time>
        <span class="status" style="display:none !IMPORTANT">Missing</span>
      </div>
      <div class="assignment-row" data-course-title="History">
        <span class="assignment-title">Visibility important marker</span>
        <time datetime="2026-08-22">08/22/2026</time>
        <span class="status" style="visibility:hidden!important">Missing</span>
      </div>
    </section>'''

    records = parse_parentvue_agenda(html)

    assert [(record["title"], record["status"]) for record in records] == [
        ("Display important marker", "due"),
        ("Visibility important marker", "due"),
    ]


def test_nearest_visibility_declaration_can_override_hidden_ancestor() -> None:
    """Would fail if a farther visibility:hidden always overrides a visible child."""
    html = '''<section><h2>Upcoming Assignments</h2>
      <div style="visibility: hidden !important">
        <div class="assignment-row" data-course-title="History"
             style="visibility:VISIBLE">
          <span class="assignment-title">Visible child row</span>
          <time datetime="2026-08-19">08/19/2026</time>
        </div>
      </div>
      <div class="assignment-row" data-course-title="History">
        <span class="assignment-title">Visible child marker</span>
        <time datetime="2026-08-20">08/20/2026</time>
        <span style="visibility:hidden">
          <span class="status" style="visibility: visible !important">Missing</span>
        </span>
      </div>
      <div style="display:none">
        <div class="assignment-row" data-course-title="History"
             style="visibility:visible">
          <span class="assignment-title">Display still wins</span>
          <time datetime="2026-08-21">08/21/2026</time>
        </div>
      </div>
    </section>'''

    records = parse_parentvue_agenda(html)

    assert [(record["title"], record["status"]) for record in records] == [
        ("Visible child row", "due"),
        ("Visible child marker", "missing"),
    ]


def test_normalization_keeps_missing_when_same_parentvue_assignment_is_upcoming() -> None:
    """Would fail if ParentVUE source identities cannot deduplicate status overlap."""
    records = parse_parentvue_agenda(FIXTURE.read_text(encoding="utf-8"))
    records.append({
        "course": "Algebra II",
        "title": "Linear review",
        "dueDate": "2026-08-11",
        "dueTime": None,
        "status": "due",
        "sourceId": "parentvue:pv-41",
    })

    assert normalize_agenda(records)["2026-08-10"]["Algebra II"]["due"] == []
    assert normalize_agenda(records)["2026-08-10"]["Algebra II"]["missing"][0] == {
        "title": "Linear review", "dueDate": "2026-08-11", "dueTime": None
    }


class FakePage:
    def __init__(self, html: str) -> None:
        self.html = html
        self.content_calls = 0

    async def content(self) -> str:
        self.content_calls += 1
        return self.html


def test_engine_collects_current_authenticated_gradebook_html() -> None:
    """Would fail if agenda collection makes a separate request instead of parsing the page."""
    page = FakePage(FIXTURE.read_text(encoding="utf-8"))

    records = asyncio.run(
        ParentVUE(page, "student", "password", "https://parentvue.example/Login_Parent_PXP.aspx").get_agenda()
    )

    assert page.content_calls == 1
    assert records[0]["sourceId"] == "parentvue:pv-41"
    assert ParentVUE.agenda_capable is True


class FakeGradebookLink:
    def __init__(self, page: "FakeNavigationPage") -> None:
        self.page = page

    async def click(self) -> None:
        self.page.clicked_visible_gradebook = True


class FakeGradebookLocator:
    def __init__(self, page: "FakeNavigationPage") -> None:
        self.first = FakeGradebookLink(page)


class FakeNavigationPage:
    def __init__(self) -> None:
        self.clicked_visible_gradebook = False
        self.selector: str | None = None

    def locator(self, selector: str) -> FakeGradebookLocator:
        self.selector = selector
        return FakeGradebookLocator(self)

    async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


def test_after_login_selects_visible_href_specific_gradebook_link() -> None:
    """Would fail if navigation can click the hidden duplicate Grade Book label."""
    page = FakeNavigationPage()
    portal = ParentVUE(page, "student", "password", "https://parentvue.example/Login_Student_PXP.aspx")

    asyncio.run(portal.after_login(None))

    assert page.clicked_visible_gradebook
    assert page.selector == 'a[href*="Gradebook"]:visible, a[href*="GradeBook"]:visible'
