from scraper.portals.utils import canonicalize_grade


def test_canonicalize_grade_accepts_supported_numeric_formats() -> None:
    assert canonicalize_grade("93.4%") == 93.4
    assert canonicalize_grade("( 87.5% )") == 87.5
    assert canonicalize_grade(" 91 ") == 91.0


def test_canonicalize_grade_accepts_case_insensitive_letter_grades() -> None:
    assert canonicalize_grade("a") == 95.0
    assert canonicalize_grade("b+") == 89.0


def test_canonicalize_grade_rejects_unavailable_or_mixed_text() -> None:
    assert canonicalize_grade("N/A") is None
    assert canonicalize_grade("-") is None
    assert canonicalize_grade("Grade: 91%") is None
