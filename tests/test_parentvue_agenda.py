from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeout
from tenacity import wait_none

from scraper.agenda_contract import normalize_agenda
from scraper.portals import parentvue as parentvue_module
from scraper.portals import parentvue_agenda as pv_agenda
from scraper.portals import utils as portal_utils
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


def test_visible_gradebook_no_data_marker_returns_no_records() -> None:
    """Would fail if a confirmed empty Grade Book is treated as a parse failure."""
    assert parse_parentvue_agenda(
        '<div id="gb-assignments"><div class="no-data">No assignments found.</div></div>'
    ) == []


def test_gradebook_shell_without_candidates_or_no_data_marker_raises() -> None:
    """Would fail if an ambiguous authenticated Grade Book shell is accepted as empty."""
    html = '<div id="gb-assignments"><table><tbody></tbody></table></div>'

    try:
        parse_parentvue_agenda(html)
    except ParentVueAgendaError as error:
        assert str(error) == "parentvue_agenda_parse_failed"
    else:
        raise AssertionError("ambiguous Grade Book shell did not raise")


def test_gradebook_candidates_conflicting_with_no_data_marker_raise() -> None:
    """Would fail if contradictory Grade Book states are silently accepted."""
    html = '''<div id="gb-assignments">
      <div class="no-data">No assignments found.</div>
      <div class="assignment-row" data-course-title="History">
        <span class="assignment-title">Primary source</span>
        <time datetime="2026-08-19">08/19/2026</time>
      </div>
    </div>'''

    try:
        parse_parentvue_agenda(html)
    except ParentVueAgendaError as error:
        assert str(error) == "parentvue_agenda_parse_failed"
    else:
        raise AssertionError("contradictory Grade Book state did not raise")


def test_all_malformed_gradebook_candidates_raise() -> None:
    """Would fail if non-actionable Grade Book candidates become an empty agenda."""
    html = '''<div id="gb-assignments">
      <div class="assignment-row" data-course-title="History">
        <span class="assignment-title">Undated work</span>
      </div>
    </div>'''

    try:
        parse_parentvue_agenda(html)
    except ParentVueAgendaError as error:
        assert str(error) == "parentvue_agenda_parse_failed"
    else:
        raise AssertionError("malformed Grade Book candidates did not raise")


def test_hidden_gradebook_no_data_marker_does_not_confirm_empty_agenda() -> None:
    """Would fail if hidden stale no-data UI turns an ambiguous page into an empty agenda."""
    html = '''<div id="gb-assignments">
      <div class="no-data" hidden>No assignments found.</div>
    </div>'''

    try:
        parse_parentvue_agenda(html)
    except ParentVueAgendaError as error:
        assert str(error) == "parentvue_agenda_parse_failed"
    else:
        raise AssertionError("hidden no-data marker did not raise")


def test_course_is_resolved_from_outer_gradebook_ancestor() -> None:
    """Would fail if nested class rows mask the ancestor that owns the course title."""
    html = '''<div id="gb-assignments">
      <section class="gb-class-section" data-course-title="Outer Biology">
        <h2>Upcoming Assignments</h2>
        <div class="gb-class-row">
          <div class="assignment-row">
            <span class="assignment-title">Cell lab</span>
            <time datetime="2026-08-18">08/18/2026</time>
          </div>
        </div>
      </section>
    </div>'''

    assert parse_parentvue_agenda(html) == [{
        "course": "Outer Biology",
        "title": "Cell lab",
        "dueDate": "2026-08-18",
        "dueTime": None,
        "status": "due",
    }]


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


LIVE_OVERVIEW_HTML = '''<div id="gb-assignments">
  <section>
    <h2 class="title">Upcoming Assignments</h2>
    <div class="gb-student-assignments-grid">
      <table><tbody>
        <tr class="gb-upcoming-assignment" data-guid="pv-upcoming-1">
          <td>
            <div><a href="/assignment/details/1">Systems review</a></div>
            <div>Algebra II</div>
            <div>Due Date: 08/18/2026</div>
            <div class="hide">internal</div>
          </td>
        </tr>
      </tbody></table>
    </div>
  </section>
  <section>
    <h2 class="title">Recent History</h2>
    <div class="gb-student-assignments-grid">
      <table><tbody><tr><td><a>Completed history must not be inferred</a></td></tr></tbody></table>
    </div>
  </section>
</div>'''


def test_live_overview_parses_upcoming_rows_without_inferring_recent_history() -> None:
    """Would fail if the live upcoming row or panel boundary is unsupported."""
    records = pv_agenda.parse_parentvue_overview(
        LIVE_OVERVIEW_HTML,
        reference=datetime(2026, 8, 16, 12, 0),
    )

    assert records == [
        {
            "course": "Algebra II",
            "title": "Systems review",
            "dueDate": "2026-08-18",
            "dueTime": None,
            "status": "due",
            "sourceId": "parentvue:assignment:dba3ed0ea4addf0a34121412",
        }
    ]


def test_live_overview_rejects_rows_conflicting_with_visible_empty_marker() -> None:
    """Would fail if contradictory live overview states are silently accepted."""
    html = LIVE_OVERVIEW_HTML.replace(
        '<section>\n    <h2 class="title">Upcoming Assignments</h2>',
        '<section><div class="no-data">No assignments</div>\n'
        '    <h2 class="title">Upcoming Assignments</h2>',
    )

    with pytest.raises(ParentVueAgendaError):
        pv_agenda.parse_parentvue_overview(
            html,
            reference=datetime(2026, 8, 16, 12, 0),
        )


def test_live_overview_ignores_recent_history_empty_marker() -> None:
    """Would fail if non-Upcoming panel state can invalidate current work."""
    html = LIVE_OVERVIEW_HTML.replace(
        '<h2 class="title">Recent History</h2>',
        '<h2 class="title">Recent History</h2><div class="no-data">No history</div>',
    )

    records = pv_agenda.parse_parentvue_overview(
        html,
        reference=datetime(2026, 8, 16, 12, 0),
    )

    assert [record["title"] for record in records] == ["Systems review"]


def test_live_overview_never_falls_back_to_recent_history_rows() -> None:
    """Would fail if a live empty Upcoming panel can infer old work as missing."""
    html = '''<div id="gb-assignments">
      <section>
        <h2>Recent History</h2>
        <div class="assignment-row missing" data-course-title="Algebra II">
          <span class="assignment-title">Completed history</span>
          <time datetime="2026-08-14"></time>
        </div>
      </section>
      <div class="gb-class-header gb-class-row">
        <button class="course-title">Algebra II</button>
      </div>
    </div>'''

    assert pv_agenda.parse_parentvue_overview(
        html,
        reference=datetime(2026, 8, 16, 12, 0),
    ) == []


COURSE_DETAIL_HTML = '''<div class="pxp-course-content">
  <div class="item-container">
    <div class="item-text-main">Quiz One</div>
    <div class="item-text-special">Aug 14</div>
    <div class="item-text-special">72%</div>
  </div>
  <div class="item-container missing">
    <div class="item-text-main">Worksheet</div>
    <div class="item-text-special">Aug 15</div>
    <div class="item-text-small">Missing</div>
    <div class="item-text-special">0%</div>
  </div>
  <div class="item-container">
    <div class="item-text-main">Lab</div>
    <div class="item-text-special">Aug 16</div>
    <div class="item-text-small">7 / 10</div>
  </div>
  <div class="item-container">
    <div class="item-text-main">Boundary</div>
    <div class="item-text-special">Aug 17</div>
    <div class="item-text-special">80%</div>
  </div>
  <div class="item-container">
    <div class="item-text-main">Excused work</div>
    <div class="item-text-special">Aug 18</div>
    <div class="item-text-small">Excused</div>
  </div>
  <div class="item-container">
    <div class="item-text-main">Not graded work</div>
    <div class="item-text-special">Aug 19</div>
    <div class="item-text-small">Not Graded</div>
  </div>
  <div class="item-container">
    <div class="item-text-main">Zero denominator</div>
    <div class="item-text-special">Aug 20</div>
    <div class="item-text-small">0 / 0</div>
  </div>
</div>'''


def test_course_detail_classifies_explicit_missing_and_below_eighty_scores() -> None:
    """Would fail if live percentage/points fields are misclassified or overcollected."""
    records = pv_agenda.parse_parentvue_course_assignments(
        COURSE_DETAIL_HTML,
        course="Algebra II",
        reference=datetime(2026, 8, 16, 12, 0),
    )

    assert records == [
        {
            "course": "Algebra II",
            "title": "Quiz One",
            "dueDate": "2026-08-14",
            "dueTime": None,
            "status": "low_score",
        },
        {
            "course": "Algebra II",
            "title": "Worksheet",
            "dueDate": "2026-08-15",
            "dueTime": None,
            "status": "missing",
        },
        {
            "course": "Algebra II",
            "title": "Lab",
            "dueDate": "2026-08-16",
            "dueTime": None,
            "status": "low_score",
        },
    ]


@pytest.mark.parametrize("score", ["0%", "0 / 10"])
def test_course_detail_classifies_numeric_zero_scores_as_missing(score: str) -> None:
    """Would fail if numeric zero still follows the generic low-score branch."""
    html = f'''<div class="pxp-course-content">
      <div class="item-container">
        <div class="item-text-main">Zero-score assignment</div>
        <div class="item-text-special">Aug 18</div>
        <div class="item-text-small">{score}</div>
      </div>
    </div>'''

    records = pv_agenda.parse_parentvue_course_assignments(
        html,
        course="Algebra II",
        reference=datetime(2026, 8, 16, 12, 0),
    )

    assert records == [
        {
            "course": "Algebra II",
            "title": "Zero-score assignment",
            "dueDate": "2026-08-18",
            "dueTime": None,
            "status": "missing",
        }
    ]


def test_overview_and_detail_share_assignment_link_identity_for_precedence() -> None:
    """Would fail if the same assignment survives as both due and missing."""
    overview = '''<div id="gb-assignments">
      <tr class="gb-upcoming-assignment" data-guid="overview-only-guid"><td>
        <div><a href="/assignment/details/1">Systems review</a></div>
        <div>Algebra II</div>
        <div>Due Date: 08/18/2026</div>
      </td></tr>
    </div>'''
    detail = '''<div class="pxp-course-content">
      <div class="item-container missing">
        <a href="/assignment/details/1"><span class="item-text-main">Systems review</span></a>
        <div class="item-text-special">Aug 18</div>
        <div class="item-text-small">Missing</div>
      </div>
    </div>'''
    reference = datetime(2026, 8, 16, 12, 0)

    records = pv_agenda.parse_parentvue_overview(
        overview,
        reference=reference,
    ) + pv_agenda.parse_parentvue_course_assignments(
        detail,
        course="Algebra II",
        reference=reference,
    )

    buckets = normalize_agenda(records)["2026-08-17"]["Algebra II"]
    assert buckets["missing"] == [
        {"title": "Systems review", "dueDate": "2026-08-18", "dueTime": None}
    ]
    assert buckets["due"] == []


def test_course_detail_accepts_empty_nonacademic_period() -> None:
    reference = datetime(2026, 8, 16, 12, 0)

    for html in (
        '<div class="pxp-course-content"><div class="no-data">No assignments</div></div>',
        '<div class="pxp-course-content"></div>',
    ):
        assert pv_agenda.parse_parentvue_course_assignments(
            html,
            course="Lunch",
            reference=reference,
        ) == []

    with pytest.raises(ParentVueAgendaError):
        pv_agenda.parse_parentvue_course_assignments(
            "<main></main>",
            course="Lunch",
            reference=reference,
        )


def test_course_detail_does_not_treat_a_slash_date_as_earned_points() -> None:
    """Would fail if an ungraded MM/DD/YYYY row is classified as a low ratio."""
    html = '''<div class="pxp-course-content">
      <div class="item-container">
        <div class="item-text-main">Ungraded worksheet</div>
        <div class="item-text-special">08/18/2026</div>
      </div>
    </div>'''

    assert pv_agenda.parse_parentvue_course_assignments(
        html,
        course="Algebra II",
        reference=datetime(2026, 8, 16, 12, 0),
    ) == []


def test_course_detail_excludes_pass_fail_and_blank_scores() -> None:
    """Would fail if nonnumeric grade states are labeled Low."""
    html = '''<div class="pxp-course-content">
      <div class="item-container">
        <div class="item-text-main">Pass-fail work</div>
        <div class="item-text-special">Aug 18</div>
        <div class="item-text-small">Pass/Fail</div>
      </div>
      <div class="item-container">
        <div class="item-text-main">Blank grade</div>
        <div class="item-text-special">Aug 19</div>
        <div class="item-text-small"></div>
      </div>
    </div>'''

    assert pv_agenda.parse_parentvue_course_assignments(
        html,
        course="Algebra II",
        reference=datetime(2026, 8, 16, 12, 0),
    ) == []


def test_course_detail_rejects_any_malformed_assignment_row() -> None:
    """Would fail if one malformed course item can yield a partial course snapshot."""
    html = COURSE_DETAIL_HTML.replace(
        '</div>\n</div>',
        '</div><div class="item-container">'
        '<div class="item-text-main">Undated work</div></div></div>',
    )

    with pytest.raises(ParentVueAgendaError):
        pv_agenda.parse_parentvue_course_assignments(
            html,
            course="Algebra II",
            reference=datetime(2026, 8, 16, 12, 0),
        )


class FakePage:
    def __init__(self, html: str) -> None:
        self.html = html
        self.content_calls = 0

    async def content(self) -> str:
        self.content_calls += 1
        return self.html


def test_engine_delegates_to_sequential_course_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if get_agenda bypasses the authenticated course scrub."""
    page = FakePage(FIXTURE.read_text(encoding="utf-8"))
    calls: list[FakePage] = []

    async def fake_collect(current_page: FakePage):
        calls.append(current_page)
        return [{"sourceId": "parentvue:sequential"}]

    monkeypatch.setattr(
        parentvue_module,
        "collect_parentvue_course_agenda",
        fake_collect,
        raising=False,
    )

    records = asyncio.run(
        ParentVUE(page, "student", "password", "https://parentvue.example/Login_Parent_PXP.aspx").get_agenda()
    )

    assert calls == [page]
    assert page.content_calls == 0
    assert records == [{"sourceId": "parentvue:sequential"}]
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
    def __init__(self, *, readiness_times_out: bool = False) -> None:
        self.url = "https://parentvue.example/Login_Student_PXP.aspx"
        self.readiness_times_out = readiness_times_out
        self.clicked_visible_gradebook = False
        self.selector: str | None = None
        self.waited_for_selectors: list[str] = []

    def locator(self, selector: str) -> FakeGradebookLocator:
        self.selector = selector
        return FakeGradebookLocator(self)

    async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def wait_for_selector(self, selector: str, **_kwargs: object) -> None:
        self.waited_for_selectors.append(selector)
        if self.readiness_times_out:
            raise PlaywrightTimeout("sensitive post-submit detail")

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


def test_after_login_selects_visible_href_specific_gradebook_link() -> None:
    """Would fail if navigation can click the hidden duplicate Grade Book label."""
    page = FakeNavigationPage()
    portal = ParentVUE(page, "student", "password", "https://parentvue.example/Login_Student_PXP.aspx")

    asyncio.run(portal.after_login(None))

    assert page.clicked_visible_gradebook
    assert page.selector == 'a[href*="Gradebook"]:visible, a[href*="GradeBook"]:visible'
    assert page.waited_for_selectors == [
        "#gb-assignments",
        "#gb-assignments tr.gb-upcoming-assignment:visible, "
        "div.gb-class-header.gb-class-row:visible, "
        "#gb-assignments .no-data:visible",
    ]


def test_login_does_not_resubmit_credentials_when_gradebook_readiness_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a post-submit ParentVUE timeout re-enters the login flow."""
    page = FakeNavigationPage(readiness_times_out=True)
    login_calls: list[str] = []

    async def fake_universal_login_flow(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        login_calls.append("submit")
        page.url = "https://parentvue.example/home"

    monkeypatch.setattr(portal_utils, "universal_login_flow", fake_universal_login_flow)
    engine = ParentVUE(
        page,  # type: ignore[arg-type]
        "student",
        "password",
        "https://parentvue.example/Login_Student_PXP.aspx",
    )

    with pytest.raises(Exception) as raised:
        asyncio.run(
            type(engine).login.retry_with(wait=wait_none())(engine)
        )

    assert login_calls == ["submit"]
    assert type(raised.value) is ParentVUE.LoginError
    assert str(raised.value) == "portal login rejected"


def test_parent_login_sanitizes_exhausted_student_selection_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if selection retries wrap their final timeout in RetryError."""

    class StudentSelectionPage(FakeNavigationPage):
        def __init__(self) -> None:
            super().__init__()
            self.selection_attempts = 0

        async def click(self, _selector: str) -> None:
            self.selection_attempts += 1
            raise PlaywrightTimeout("sensitive post-submit detail")

    page = StudentSelectionPage()
    login_calls: list[str] = []

    async def fake_universal_login_flow(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        login_calls.append("submit")
        page.url = "https://parentvue.example/home"

    fast_select_student = ParentVUE.select_student.retry_with(wait=wait_none())
    monkeypatch.setattr(ParentVUE, "select_student", fast_select_student)
    monkeypatch.setattr(portal_utils, "universal_login_flow", fake_universal_login_flow)
    engine = ParentVUE(
        page,  # type: ignore[arg-type]
        "student",
        "password",
        "https://parentvue.example/Login_Parent_PXP.aspx",
    )

    with pytest.raises(Exception) as raised:
        asyncio.run(engine.login("First"))

    assert login_calls == ["submit"]
    assert page.selection_attempts == 3
    assert type(raised.value) is ParentVUE.LoginError
    assert str(raised.value) == "portal login rejected"
