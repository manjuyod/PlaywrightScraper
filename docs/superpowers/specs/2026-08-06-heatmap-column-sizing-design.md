# Heatmap Column Sizing Design

## Goal

Keep the Course column adaptive without allowing an unusually long course name to dominate the heatmap, and size the generated grade columns independently so additional weeks do not recreate the oversized table.

## Current Behavior and Root Cause

The heatmap is one semantic HTML table inside an existing horizontal overflow container. The first column contains course names and every following column represents a generated week.

The current working-tree CSS gives both groups a 100px minimum and prevents wrapping. In the browser smoke fixture, a long course name expanded the first column to about 398px, while 12 grade columns consumed another 1,200px. The resulting table was about 1,598px wide. The regression comes from sharing the Course sizing contract with every grade column and leaving the no-wrap Course content uncapped.

## Considered Approaches

### 1. One table with independent sizing contracts — selected

Keep the semantic table and give Course and grade columns separate CSS rules. Constrain the rendered Course label inside the first-column cell, while generated grade columns use smaller responsive minimums.

This preserves native row alignment, table semantics, keyboard and screen-reader behavior, and the existing horizontal scroll container. It also directly addresses both contributors to excessive width.

### 2. Two synchronized tables

Render Course as a left-hand table and weeks as a separately scrolling table. This fully isolates horizontal sizing, but requires synchronization of row heights and vertical scrolling. It also duplicates table structure and makes accessibility and responsive behavior more fragile.

### 3. Replace the table with CSS Grid

Render Course and grade values as one grid with explicit tracks. This provides precise control over widths, but is a larger component rewrite and would require rebuilding semantic table relationships and dynamic column definitions.

## Selected Design

### Course column

- Retain a 100px minimum width.
- Cap the total first-column width at 260px.
- Keep 8px of horizontal padding on each side.
- Render the visible course name in an inner label constrained to 244px, accounting for the 16px total cell padding.
- Keep course names on one line and truncate overflow with an ellipsis.
- Put the complete course name in the row header's native `title` attribute. The complete text also remains in the DOM for assistive technology.
- Do not make the first column sticky as part of this change.

The inner label is important because maximum widths on native table cells are not consistently authoritative under automatic table layout. Constraining the label's intrinsic width gives the table layout algorithm a stable maximum contribution from long course names.

### Generated grade columns

- Target all cells after the first column independently from Course.
- Use a 58px minimum width on regular screens and restore the existing 48px mobile minimum.
- Leave generated grade widths on automatic table layout, with one-line content and 8px horizontal padding, so those columns absorb spare table width instead of stretching Course.
- Reserve `width: 1%` for the Course column; applying it to every generated column causes desktop spare width to be distributed back into Course.
- Do not impose Course's 100px minimum on grade columns.

### Table and scrolling

- Keep one semantic table and the existing `overflow-x-auto` container.
- Let native table layout align headers and values within each generated column.
- Allow horizontal scrolling when the combined compact columns still exceed the viewport.

## Component and Interface Changes

`GradeHeatmap` will wrap the header and row-header Course text in a dedicated presentational label element and add the full course name as a `title` on each row header. The history data shape, component props, routes, and public interfaces remain unchanged.

CSS will define separate contracts for the first column, the constrained Course label, and all generated grade columns.

## Testing

### Automated contract tests

- Assert the first column retains its 100px minimum and 8px side padding.
- Assert the Course label has a 244px maximum, one-line overflow, and ellipsis.
- Assert generated grade columns use a 58px minimum rather than 100px.
- Assert the mobile rule reduces generated grade columns to 48px.
- Assert `GradeHeatmap` supplies the Course label class and full-name title.

Each changed behavior will be introduced by a focused failing test before production code is changed.

### Verification

- Run the focused frontend contract tests.
- Run all dashboard frontend tests.
- Run `node --check ui/static/react-dashboard.js`.
- Run the full Python suite and compare any failures with the two known authorization-status failures.
- Restart the development server and repeat the Playwright smoke fixture at desktop and mobile widths.
- Verify Course is between 100px and 260px, long names ellipsize with the full native tooltip, grade columns use their compact independent widths, headers and cells remain aligned, and narrow screens scroll horizontally.

## Non-Goals

- No two-table synchronization.
- No sticky Course column.
- No JavaScript width measurement.
- No course-name wrapping.
- No changes to grade data, APIs, or routing.
