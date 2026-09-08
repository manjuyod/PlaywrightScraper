# Remove Header Floating Squares Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the two floating square decorations from the shared dashboard header without changing its grid texture, scanline, content, spacing, or behavior.

**Architecture:** Delete the square overlay elements from the shared React `Header` component and remove only their dedicated CSS positioning, appearance, and animation rules. Preserve `tc-header-squares` (the ambient grid) and `tc-header-scanline` as the remaining decorative layer.

**Tech Stack:** React without JSX, CSS, pytest.

## Global Constraints

- Do not access any database.
- Preserve the header grid, orange scanline, logo, title, subtitle, actions, dimensions, and responsive behavior.
- Remove every singular `tc-header-square` element, modifier, and pulse animation.
- Do not commit changes unless the user asks.

---

### Task 1: Remove collision-prone header squares

**Files:**
- Modify: `ui/static/react-dashboard.js:138-176`
- Modify: `ui/static/react-dashboard.css:84-146,940-1057`
- Test: `tests/test_read_only_dashboard_frontend.py`

**Interfaces:**
- Consumes: the existing `Header({ data, title, subtitle, actions })` component.
- Produces: the same header DOM except that its ambient layer contains only `tc-header-squares` and `tc-header-scanline`.

- [ ] **Step 1: Add the failing frontend regression test**

```python
def test_header_ambient_texture_omits_floating_square_overlays() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "ui" / "static" / "react-dashboard.css").read_text(
        encoding="utf-8"
    )
    header = javascript.split("function Header", 1)[1].split("function Shell", 1)[0]

    assert 'className: "tc-header-squares"' in header
    assert 'className: "tc-header-scanline"' in header
    assert 'className: "tc-header-square ' not in header
    assert ".tc-header-square {" not in css
    assert ".tc-header-square--" not in css
    assert "tc-header-square-pulse" not in css
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest -q tests/test_read_only_dashboard_frontend.py::test_header_ambient_texture_omits_floating_square_overlays`

Expected: FAIL because the singular square elements and CSS rules still exist.

- [ ] **Step 3: Remove only the square overlay implementation**

Delete the two `tc-header-square` spans from `Header`, then delete the singular square base/modifier selectors, `tc-header-square-pulse` keyframes, and singular square animation declarations. Do not alter the plural `tc-header-squares` grid or `tc-header-scanline` rules.

- [ ] **Step 4: Verify the focused and surrounding frontend tests**

Run: `uv run pytest -q tests/test_read_only_dashboard_frontend.py`

Expected: all frontend contract tests pass.

- [ ] **Step 5: Verify static assets and affected scope**

Run `node --check ui/static/react-dashboard.js`, `uv run ruff check tests/test_read_only_dashboard_frontend.py`, `git diff --check`, and `gitnexus_detect_changes(scope="all")`. Review the diff to confirm that only the square overlays and their regression test changed.
