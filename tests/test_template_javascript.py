from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _executable_script_blocks(template: str) -> list[str]:
    script_blocks = re.findall(
        r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>",
        template,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return [
        body
        for attrs, body in script_blocks
        if "application/json" not in attrs.lower()
    ]


def test_templates_do_not_assign_raw_jinja_inside_javascript() -> None:
    template_paths = [
        PROJECT_ROOT / "ui" / "templates" / "franchise.html",
        PROJECT_ROOT / "ui" / "templates" / "student_heatmap.html",
    ]

    offenders: list[str] = []
    for template_path in template_paths:
        template = template_path.read_text(encoding="utf-8")
        for script in _executable_script_blocks(template):
            if re.search(r"=\s*{{", script):
                offenders.append(str(template_path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_dashboard_exposes_student_heatmap_deep_link() -> None:
    script = (PROJECT_ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )

    assert "`${student.detailUrl}#heatmap`" in script
    assert "Heatmap" in script
    assert "studentTabFromHash(window.location.hash)" in script
