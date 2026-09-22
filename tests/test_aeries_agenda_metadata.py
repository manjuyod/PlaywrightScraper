from datetime import date

import pytest

from scraper.agenda_contract import normalize_agenda
from scraper.portals.aeries_agenda import parse_aeries_gradebook


def assignment(score, *, category='', completed='0 / 10', graded=True, actual=True):
    score_html = f'<div id="test_scoreData">{score}</div>' if actual else ''
    return f'''<div id="test_assignmentsView"><div class="Card">
        {score_html}<div id="test_completeData">Complete {completed}</div>
        <span id="test_spTransfer">0.00%</span>
        <div class="TextHeading">1 - Practice</div>
        <div class="TextSubSectionCategory">{category}</div>
        <div>Due Date: 09/21/2026</div><div>Grading Complete: {graded}</div>
    </div></div>'''


def parse(html):
    return parse_aeries_gradebook(html, course='2- Science- Fall 8/13/2026 - 12/18/2026*',
                                 reference=date(2026, 9, 22))[0]


def test_tustin_score_and_explicit_category_survive_normalization():
    record = parse(assignment('6.50 / 10', category='<i title="Summative"></i> Writing'))
    assert record['score'] == '6.5/10'
    assert record['category'] == 'summative'
    assert record['status'] == 'low_score'
    normalized = normalize_agenda([record], known_course_titles=['SCIENCE'])
    assert normalized['2026-09-21']['SCIENCE']['low_score'] == [{
        'title':'Practice', 'dueDate':'2026-09-21', 'dueTime':None,
        'score':'6.5/10', 'category':'summative',
    }]


@pytest.mark.parametrize('score,expected', [
    ('0 / 10', '0/10'), ('7 / 10 70%', '7/10'), ('72.50%', '72.5%'),
])
def test_actual_scores_keep_valid_display_values(score, expected):
    record = parse(assignment(score))
    assert record['score'] == expected


@pytest.mark.parametrize('score', ['-1 / 10', '1,000 / 2,000', '1e3 / 10', '1/10 2/10'])
def test_unsupported_scores_are_not_reinterpreted_as_positive_fragments(score):
    assert 'score' not in parse(assignment(score))


@pytest.mark.parametrize('score', ['', '/ 10'])
def test_blank_actual_score_does_not_emit_a_synthetic_completion_zero(score):
    record = parse(assignment(score, graded=False, category='<i title="Formative"></i> Classwork'))
    assert record['status'] == 'due'
    assert 'score' not in record
    assert record['category'] == 'formative'


def test_legacy_layout_can_emit_complete_score_when_actual_field_is_absent():
    assert parse(assignment('', completed='7 / 10', actual=False))['score'] == '7/10'


@pytest.mark.parametrize('category,expected', [
    ('Formative', 'formative'),
    ('<i title="Summative"></i> Assessment', 'summative'),
    ('Writing', None),
    ('<i title="Formative"></i><i title="Summative"></i> Mixed', None),
    ('<i title="Formative"></i> Summative', None),
])
def test_categories_require_explicit_unambiguous_portal_evidence(category, expected):
    record = parse(assignment('7 / 10', category=category))
    assert record.get('category') == expected
