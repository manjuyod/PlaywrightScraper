from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_franchise_sorting_scenario(scenario: str) -> object:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    marker = "    function GradeHistory({ history }) {"
    assert marker in javascript
    test_hooks = """
    window.__tcSortingTestHooks = {
        filterAndSortStudents:
            typeof filterAndSortStudents === "function" ? filterAndSortStudents : null,
        nextSortConfig: typeof nextSortConfig === "function" ? nextSortConfig : null,
        SortableHeading: typeof SortableHeading === "function" ? SortableHeading : null,
        StudentTable,
    };
    return;

"""
    instrumented = javascript.replace(marker, test_hooks + marker, 1)
    harness = f"""
global.window = {{
    React: {{
        useEffect: () => undefined,
        useMemo: (factory) => factory(),
        useState: (initial) => [initial, () => undefined],
        createElement: (type, props, ...children) => {{
            const componentProps = {{
                ...(props || {{}}),
                children: children.length <= 1 ? children[0] : children,
            }};
            if (typeof type === "function") {{
                return type(componentProps);
            }}
            return {{ type, props: props || {{}}, children }};
        }},
    }},
    ReactDOM: {{}},
}};
global.document = {{
    getElementById: (id) => (id === "tc-react-root" ? {{}} : null),
}};
eval({json.dumps(instrumented)});
const hooks = window.__tcSortingTestHooks;
{scenario}
"""
    completed = subprocess.run(
        ["node", "-"],
        input=harness,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _run_student_page_scenario(scenario: str, *, hash_value: str = "#report") -> object:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    marker = "    function App({ data }) {"
    assert marker in javascript
    test_hooks = """
    window.__tcStudentPageTestHooks = {
        StudentPage: typeof StudentPage === "function" ? StudentPage : null,
        AgendaCard: typeof AgendaCard === "function" ? AgendaCard : null,
        AgendaClass: typeof AgendaClass === "function" ? AgendaClass : null,
    };
    return;

"""
    instrumented = javascript.replace(marker, test_hooks + marker, 1)
    harness = f"""
global.window = {{
    React: {{
        useEffect: () => undefined,
        useMemo: (factory) => factory(),
        useState: (initial) => [initial, () => undefined],
        createElement: (type, props, ...children) => {{
            const componentProps = {{
                ...(props || {{}}),
                children: children.length <= 1 ? children[0] : children,
            }};
            if (typeof type === "function") {{
                return type(componentProps);
            }}
            return {{ type, props: props || {{}}, children }};
        }},
    }},
    ReactDOM: {{}},
    location: {{ hash: {json.dumps(hash_value)} }},
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
}};
global.document = {{
    getElementById: (id) => (id === "tc-react-root" ? {{}} : null),
}};
eval({json.dumps(instrumented)});
const hooks = window.__tcStudentPageTestHooks;
{scenario}
"""
    completed = subprocess.run(
        ["node", "-"],
        input=harness,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_react_bundle_is_read_only_and_polls_canonical_jobs() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )

    assert "/api/jobs" in javascript
    assert "15000" in javascript
    assert "noopener noreferrer" in javascript
    assert 'data.page === "home"' in javascript
    assert 'data.page === "franchise"' in javascript
    assert 'data.page === "student"' in javascript
    for retired in (
        "LoginPage",
        "LogoutForm",
        "StudentDialog",
        "HiddenCsrf",
        "csrfToken",
        "/status/",
        "run_scraper",
        "run_agenda",
        "delete_students",
        "add_student",
        "edit_student",
    ):
        assert retired not in javascript


def test_student_page_renders_two_ordered_portal_agenda_cards() -> None:
    result = _run_student_page_scenario(
        """
if (!hooks.StudentPage || !hooks.AgendaCard || !hooks.AgendaClass) {
    throw new Error("portal agenda components are not implemented");
}
const data = {
    backUrl: "#back",
    logoUrl: "/static/imgs/tc_logo.webp",
    student: {
        id: 77,
        firstName: "Fictional",
        lastName: "Learner",
        gradeLevel: 11,
        portalUrl: null,
        status: "synced",
        updatedAt: null,
        gradesSnapshot: [],
        grades: {},
        agendaItems: [],
        agendaSlots: [
            { number: 1, portal: "canvas", portalLabel: "Canvas", weeks: [] },
            {
                number: 2,
                portal: "parentvue",
                portalLabel: "ParentVUE",
                weeks: [
                    {
                        weekStart: "2026-08-10",
                        label: "Week of Aug 10",
                        classes: [
                            {
                                name: "Fictional Seminar",
                                count: 2,
                                assignments: [
                                    { status: "missing", title: "Reflection draft", dueDate: "2026-08-11", dueDisplay: "Aug 11" },
                                    { status: "due", title: "Reading notes", dueDate: "2026-08-16", dueDisplay: "Aug 16 · 23:59" },
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    },
};
const tree = hooks.StudentPage({ data });
function collect(node, predicate, found = []) {
    if (node === null || node === undefined || node === false) return found;
    if (Array.isArray(node)) {
        for (const child of node) collect(child, predicate, found);
        return found;
    }
    if (typeof node === "object") {
        if (predicate(node)) found.push(node);
        for (const child of (Array.isArray(node.children) ? node.children : [node.children])) {
            collect(child, predicate, found);
        }
    }
    return found;
}
function textOf(node) {
    if (node === null || node === undefined || node === false) return "";
    if (Array.isArray(node)) return node.map(textOf).join("");
    if (typeof node === "string" || typeof node === "number") return String(node);
    return (Array.isArray(node.children) ? node.children : [node.children]).map(textOf).join("");
}
const agendaHeadings = collect(tree, (node) => node.type === "h2")
    .map(textOf)
    .filter((text) => text.startsWith("Agenda"));
const scrollRegions = collect(tree, (node) => String(node.props?.["aria-label"] || "").endsWith(" assignments"));
const details = collect(tree, (node) => node.type === "details");
const summaries = collect(tree, (node) => node.type === "summary");
const statusMarkers = collect(tree, (node) => node.props?.["aria-label"] === "Missing assignment" || node.props?.["aria-label"] === "Upcoming assignment");
console.log(JSON.stringify({
    agendaHeadings,
    scrollRegions: scrollRegions.map((node) => ({ label: node.props["aria-label"], tabIndex: node.props.tabIndex, text: textOf(node) })),
    detailCount: details.length,
    summaryText: summaries.map(textOf),
    statusMarkers: statusMarkers.map((node) => ({ label: node.props["aria-label"], text: textOf(node) })),
    pageText: textOf(tree),
}));
"""
    )

    assert result["agendaHeadings"] == [
        "Agenda 1 · Canvas",
        "Agenda 2 · ParentVUE",
    ]
    assert result["scrollRegions"] == [
        {"label": "Agenda 1 · Canvas assignments", "tabIndex": 0, "text": ""},
        {
            "label": "Agenda 2 · ParentVUE assignments",
            "tabIndex": 0,
            "text": (
                "Week of Aug 10Fictional Seminar2 assignments"
                "MReflection draftAug 11DUEReading notesAug 16 · 23:59"
            ),
        },
    ]
    assert result["detailCount"] == 1
    assert result["summaryText"] == ["Fictional Seminar2 assignments"]
    assert result["statusMarkers"] == [
        {"label": "Missing assignment", "text": "M"},
        {"label": "Upcoming assignment", "text": "DUE"},
    ]
    assert "No agenda" not in result["pageText"]
    assert "scrape" not in result["pageText"].lower()


def test_agenda_class_uses_singular_assignment_count_copy() -> None:
    result = _run_student_page_scenario(
        """
if (!hooks.AgendaClass) {
    throw new Error("AgendaClass is not implemented");
}
const tree = hooks.AgendaClass({
    classGroup: {
        name: "Fictional Seminar",
        count: 1,
        assignments: [
            { status: "due", title: "Reading notes", dueDate: "2026-08-16", dueDisplay: "Aug 16" },
        ],
    },
});
function find(node, type) {
    if (!node || typeof node !== "object") return null;
    if (Array.isArray(node)) {
        for (const child of node) {
            const match = find(child, type);
            if (match) return match;
        }
        return null;
    }
    if (node.type === type) return node;
    for (const child of (Array.isArray(node.children) ? node.children : [node.children])) {
        const match = find(child, type);
        if (match) return match;
    }
    return null;
}
function textOf(node) {
    if (node === null || node === undefined || node === false) return "";
    if (Array.isArray(node)) return node.map(textOf).join("");
    if (typeof node === "string" || typeof node === "number") return String(node);
    return (Array.isArray(node.children) ? node.children : [node.children]).map(textOf).join("");
}
console.log(JSON.stringify({ summary: textOf(find(tree, "summary")) }));
"""
    )

    assert result == {"summary": "Fictional Seminar1 assignment"}


def test_student_page_preserves_legacy_agenda_and_heatmap_branches() -> None:
    legacy = _run_student_page_scenario(
        """
const tree = hooks.StudentPage({ data: {
    backUrl: "#back",
    logoUrl: "/static/imgs/tc_logo.webp",
    student: {
        id: 78,
        firstName: "Legacy",
        lastName: "Learner",
        gradeLevel: 10,
        portalUrl: null,
        status: "synced",
        updatedAt: null,
        gradesSnapshot: [],
        grades: {},
        agendaSlots: [],
        agendaItems: [{ dueDate: "2026-08-14", course: "Fictional Studies", title: "Archive response" }],
    },
} });
function textOf(node) {
    if (node === null || node === undefined || node === false) return "";
    if (Array.isArray(node)) return node.map(textOf).join("");
    if (typeof node === "string" || typeof node === "number") return String(node);
    return (Array.isArray(node.children) ? node.children : [node.children]).map(textOf).join("");
}
console.log(JSON.stringify({ text: textOf(tree) }));
"""
    )
    heatmap = _run_student_page_scenario(
        """
const tree = hooks.StudentPage({ data: {
    backUrl: "#back",
    logoUrl: "/static/imgs/tc_logo.webp",
    student: {
        id: 79,
        firstName: "Heatmap",
        lastName: "Learner",
        gradeLevel: 9,
        portalUrl: null,
        status: "synced",
        updatedAt: null,
        gradesSnapshot: [],
        grades: { "2026-08-10": { "Fictional Studies": 91 } },
        agendaSlots: [],
        agendaItems: [],
    },
} });
function collect(node, type, found = []) {
    if (!node || typeof node !== "object") return found;
    if (Array.isArray(node)) {
        for (const child of node) collect(child, type, found);
        return found;
    }
    if (node.type === type) found.push(node);
    for (const child of (Array.isArray(node.children) ? node.children : [node.children])) collect(child, type, found);
    return found;
}
function textOf(node) {
    if (node === null || node === undefined || node === false) return "";
    if (Array.isArray(node)) return node.map(textOf).join("");
    if (typeof node === "string" || typeof node === "number") return String(node);
    return (Array.isArray(node.children) ? node.children : [node.children]).map(textOf).join("");
}
console.log(JSON.stringify({ headings: collect(tree, "h2").map(textOf), tables: collect(tree, "table").length }));
""",
        hash_value="#heatmap",
    )

    assert "Agenda2026-08-14Archive responseFictional Studies" in legacy["text"]
    assert legacy["text"].count("Agenda") == 1
    assert heatmap == {"headings": ["Grade Heatmap"], "tables": 1}


def test_franchise_and_student_headers_omit_overview_actions() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    franchise_page = javascript.split("function FranchisePage", 1)[1].split(
        "function GradeHistory", 1
    )[0]
    student_page = javascript.split("function StudentPage", 1)[1].split(
        "function App", 1
    )[0]

    for page in (franchise_page, student_page):
        assert "data.homeUrl" not in page
        assert '"Overview"' not in page


def test_franchise_cover_uses_the_name_from_page_data() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    franchise_page = javascript.split("function FranchisePage", 1)[1].split(
        "function GradeHistory", 1
    )[0]

    assert "title: data.franchiseName" in franchise_page
    assert "`Franchise ${data.franchiseId}`" not in franchise_page


def test_header_ambient_texture_omits_floating_square_overlays() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "ui" / "static" / "react-dashboard.css").read_text(
        encoding="utf-8"
    )
    header = javascript.split("function Header", 1)[1].split(
        "function Shell", 1
    )[0]

    assert 'className: "tc-header-squares"' in header
    assert 'className: "tc-header-scanline"' in header
    assert 'className: "tc-header-square ' not in header
    assert ".tc-header-square {" not in css
    assert ".tc-header-square--" not in css
    assert "tc-header-square-pulse" not in css


def test_student_header_back_action_uses_generic_label() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    student_page = javascript.split("function StudentPage", 1)[1].split(
        "function App", 1
    )[0]

    assert 'href: data.backUrl' in student_page
    assert 'icon: "arrowLeft"' in student_page
    assert '"Back"' in student_page
    assert '"Franchise"' not in student_page


def test_franchise_students_render_as_a_read_only_table() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )

    assert "function StudentTable" in javascript
    assert re.search(r'h\(\s*"table"', javascript)
    assert re.search(r'h\(\s*"thead"', javascript)
    assert re.search(r'h\(\s*"tbody"', javascript)
    assert "h(StudentTable" in javascript
    assert "overflow-x-auto" in javascript
    assert "gradesSnapshot" in javascript
    assert "lowGrades" in javascript
    assert "highGrades" in javascript
    assert "data.students.map((student) => h(StudentCard" not in javascript


def test_franchise_student_sorting_orders_names_without_mutating_payload() -> None:
    result = _run_franchise_sorting_scenario(
        """
if (!hooks.filterAndSortStudents) {
    throw new Error("filterAndSortStudents is not implemented");
}
const students = [
    { id: 20, firstName: "Zoë", lastName: "Zulu" },
    { id: 40, firstName: "amy", lastName: "Zulu" },
    { id: 30, firstName: "Amy", lastName: "Able" },
    { id: 10, firstName: "AMY", lastName: "Able" },
    { id: 50, firstName: "Bob", lastName: "Beta" },
    { id: 60, firstName: "Émile", lastName: "Zulu" },
    { id: 70, firstName: "emile", lastName: "Able" },
    { id: 5, firstName: "", lastName: "" },
];
const originalIds = students.map((student) => student.id);
const ascending = hooks.filterAndSortStudents(
    students,
    "",
    { key: "name", direction: "asc" },
).map((student) => student.id);
const descending = hooks.filterAndSortStudents(
    students,
    "",
    { key: "name", direction: "desc" },
).map((student) => student.id);
console.log(JSON.stringify({
    originalIds,
    payloadIdsAfterSorting: students.map((student) => student.id),
    ascending,
    descending,
}));
"""
    )

    assert result == {
        "originalIds": [20, 40, 30, 10, 50, 60, 70, 5],
        "payloadIdsAfterSorting": [20, 40, 30, 10, 50, 60, 70, 5],
        "ascending": [5, 10, 30, 40, 50, 70, 60, 20],
        "descending": [20, 60, 70, 50, 40, 30, 10, 5],
    }


def test_franchise_student_sorting_uses_the_explicit_standing_rank() -> None:
    result = _run_franchise_sorting_scenario(
        """
if (!hooks.filterAndSortStudents) {
    throw new Error("filterAndSortStudents is not implemented");
}
const students = [
    { id: 1, firstName: "One", lastName: "Student", standing: "Good" },
    { id: 2, firstName: "Two", lastName: "Student", standing: null },
    { id: 3, firstName: "Three", lastName: "Student", standing: "Poor" },
    { id: 4, firstName: "Four", lastName: "Student", standing: "Fair" },
    { id: 5, firstName: "Five", lastName: "Student", standing: "Unexpected" },
    { id: 6, firstName: "Six", lastName: "Student", standing: "Good" },
];
const ascending = hooks.filterAndSortStudents(
    students,
    "",
    { key: "standing", direction: "asc" },
).map((student) => student.id);
const descending = hooks.filterAndSortStudents(
    students,
    "",
    { key: "standing", direction: "desc" },
).map((student) => student.id);
console.log(JSON.stringify({ ascending, descending }));
"""
    )

    assert result == {
        "ascending": [2, 5, 3, 4, 1, 6],
        "descending": [1, 6, 4, 3, 2, 5],
    }


def test_franchise_sort_state_restarts_for_a_new_column_and_survives_search() -> None:
    result = _run_franchise_sorting_scenario(
        """
if (!hooks.filterAndSortStudents || !hooks.nextSortConfig) {
    throw new Error("franchise sorting state helpers are not implemented");
}
const initial = { key: null, direction: "asc" };
const nameAscending = hooks.nextSortConfig(initial, "name");
const nameDescending = hooks.nextSortConfig(nameAscending, "name");
const standingAscending = hooks.nextSortConfig(nameDescending, "standing");
const students = [
    { id: 1, firstName: "Bob", lastName: "Baker" },
    { id: 2, firstName: "Alice", lastName: "Able" },
];
const noMatches = hooks.filterAndSortStudents(students, "missing", nameAscending);
const recovered = hooks.filterAndSortStudents(students, "", nameAscending).map(
    (student) => student.id,
);
console.log(JSON.stringify({
    nameAscending,
    nameDescending,
    standingAscending,
    noMatches,
    recovered,
}));
"""
    )

    assert result == {
        "nameAscending": {"key": "name", "direction": "asc"},
        "nameDescending": {"key": "name", "direction": "desc"},
        "standingAscending": {"key": "standing", "direction": "asc"},
        "noMatches": [],
        "recovered": [2, 1],
    }


def test_franchise_sort_headers_are_accessible_and_wire_both_columns() -> None:
    result = _run_franchise_sorting_scenario(
        """
if (!hooks.SortableHeading) {
    throw new Error("SortableHeading is not implemented");
}
const clicked = [];
const student = {
    id: 1,
    detailUrl: "/student/1",
    firstName: "Alex",
    lastName: "Able",
    gradeLevel: 8,
    portalUrl: null,
    status: "synced",
    updatedAt: null,
    standing: "Good",
    gradesSnapshot: [],
    lowGrades: [],
    highGrades: [],
};
const tree = hooks.StudentTable({
    students: [student],
    sortConfig: { key: "name", direction: "asc" },
    onSort: (columnKey) => clicked.push(columnKey),
});
function collect(node, type, found = []) {
    if (!node || typeof node !== "object") {
        return found;
    }
    if (node.type === type) {
        found.push(node);
    }
    const children = Array.isArray(node.children) ? node.children : [node.children];
    for (const child of children) {
        collect(child, type, found);
    }
    return found;
}
function summarizeHeading(heading) {
    const button = collect(heading, "button")[0];
    return {
        ariaSort: heading.props["aria-sort"] ?? null,
        buttonType: button.props.type,
        ariaLabel: button.props["aria-label"],
        marker: button.children[1].children[0],
        hasClickHandler: typeof button.props.onClick === "function",
    };
}
const sortableHeadings = collect(tree, "th").filter(
    (heading) => collect(heading, "button").length,
);
for (const heading of sortableHeadings) {
    collect(heading, "button")[0].props.onClick();
}
const descendingHeading = hooks.SortableHeading({
    label: "Standing",
    columnKey: "standing",
    sortConfig: { key: "standing", direction: "desc" },
    onSort: () => undefined,
    className: "heading",
});
console.log(JSON.stringify({
    headings: sortableHeadings.map(summarizeHeading),
    descendingHeading: summarizeHeading(descendingHeading),
    clicked,
}));
"""
    )

    assert result == {
        "headings": [
            {
                "ariaSort": "ascending",
                "buttonType": "button",
                "ariaLabel": "Sort Student descending",
                "marker": "↑",
                "hasClickHandler": True,
            },
            {
                "ariaSort": None,
                "buttonType": "button",
                "ariaLabel": "Sort Standing ascending",
                "marker": "↕",
                "hasClickHandler": True,
            },
        ],
        "descendingHeading": {
            "ariaSort": "descending",
            "buttonType": "button",
            "ariaLabel": "Sort Standing ascending",
            "marker": "↓",
            "hasClickHandler": True,
        },
        "clicked": ["name", "standing"],
    }


def test_heatmap_course_column_is_bounded_and_adaptive() -> None:
    css = (ROOT / "ui" / "static" / "react-dashboard.css").read_text(
        encoding="utf-8"
    )
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    column_selector = ".tc-heatmap-table th:first-child {"
    label_selector = ".tc-heatmap-course-label {"

    assert column_selector in css
    assert label_selector in css

    course_column = css.split(column_selector, 1)[1].split("}", 1)[0]
    course_label = css.split(label_selector, 1)[1].split("}", 1)[0]

    assert "min-width: 100px;" in course_column
    assert "width: 1%;" in course_column
    assert "padding-inline: 8px;" in course_column
    assert "display: block;" in course_label
    assert "max-width: 244px;" in course_label
    assert "overflow: hidden;" in course_label
    assert "text-overflow: ellipsis;" in course_label
    assert "white-space: nowrap;" in course_label
    assert 'className: "tc-heatmap-course-label"' in javascript
    assert "title: course" in javascript


def test_heatmap_grade_columns_are_individually_compact() -> None:
    css = (ROOT / "ui" / "static" / "react-dashboard.css").read_text(
        encoding="utf-8"
    )
    selector = ".tc-heatmap-table tr > :not(:first-child) {"

    assert selector in css

    grade_columns = css.split(selector, 1)[1].split("}", 1)[0]
    mobile_css = css.split("@media (max-width: 760px) {", 1)[1]

    assert selector in mobile_css

    mobile_grade_columns = mobile_css.split(selector, 1)[1].split("}", 1)[0]

    assert "min-width: 58px;" in grade_columns
    assert "width: 1%;" not in grade_columns
    assert "white-space: nowrap;" in grade_columns
    assert "padding-inline: 8px;" in grade_columns
    assert "min-width: 48px;" in mobile_grade_columns


def test_flask_web_path_does_not_import_legacy_writes_or_executor() -> None:
    routes = (ROOT / "ui" / "routes.py").read_text(encoding="utf-8")
    app = (ROOT / "ui" / "app.py").read_text(encoding="utf-8")

    assert "ui.ext_jobs" not in routes
    assert "import db" not in routes
    assert "flask_session" not in app
    assert "SESSION_SECRET" not in app
    assert "session" not in app.lower()
    assert "csrf" not in app.lower()
    assert "load_dotenv()" in app


def test_retired_auth_templates_and_dependency_are_removed() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()

    assert "flask-session" not in pyproject
    assert not (ROOT / "ui" / "auth.py").exists()
    for template in (
        "login.html",
        "health.html",
        "franchise.html",
        "student.html",
        "student_heatmap.html",
    ):
        assert not (ROOT / "ui" / "templates" / template).exists()


def test_unauthorized_overview_template_is_static_and_actionless() -> None:
    template = (ROOT / "ui" / "templates" / "unauthorized.html").read_text(
        encoding="utf-8"
    )

    assert "Unauthorized" in template
    assert "development environment" in template
    assert "direct franchise URL" in template
    assert "<form" not in template
    assert "tc-page-data" not in template


def test_replit_proxy_does_not_forward_retired_auth_headers() -> None:
    nginx = (ROOT / "ui" / "nginx.conf").read_text(encoding="utf-8").lower()

    assert "x-franchise" not in nginx
    assert "x-internal-key" not in nginx
