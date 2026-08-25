# Infinite Campus Class-Grades Agenda Design

## Goal

Collect Infinite Campus agenda records by opening each class once from the
Grades page and parsing the class's rendered assignment rows in bulk. Assignment
detail pages are not opened.

## Navigation

1. Navigate to the portal's Grades page using the same menu route used for
   grade collection.
2. Enumerate the visible course cards from
   `div.collapsible-card.grades__card` and read each course title from `h4 a`.
3. Open one course.
4. If necessary, select that course's Grades tab.
5. Expand collapsed assignment categories.
6. Parse every rendered `.selcat-assignment-row` in one pass.
7. Return to the main Grades page, reacquire the course cards, and repeat for
   the next course.

Course navigation remains sequential within the student's authenticated
browser session. The number of browser navigation cycles is proportional to
the number of courses, not the number of assignments. Assignment processing is
an in-memory DOM parse.

## Row extraction

Each assignment row provides:

- title from `.assignment__largeScreen--cell-assignmentName`;
- due date from `.assignment__largeScreen--cell-courseDueDate`;
- status flags from `tl-curriculum-flags .label`;
- score text from `tl-student-assignment-score`.

The course title comes from the course card used to reach the page. Infinite
Campus exposes a due date but not a time in this view, so `dueTime` remains
`null`.

## Classification

Status precedence is:

1. an explicit `Missing` flag produces `missing`;
2. a numeric percentage below 80 produces `low_score`;
3. an unscored assignment due today or later produces `due`.

Assignments scoring 80 percent or higher are omitted. Completed or excluded
score states such as `Excused`, and unscored assignments explicitly marked
`Turned In`, are also omitted. Old unscored assignments without a missing flag
are omitted rather than inferred to be missing.

Rows retained in the agenda must have a valid title and due date. Unexpected or
ambiguous retained data fails the Infinite Campus agenda slot instead of
silently returning incomplete data.

## Boundaries

- No undocumented Infinite Campus API is used.
- No previously stored agenda is reused.
- No assignment row or assignment detail is clicked.
- The collector does not create extra pages, contexts, or workers.
- Slot persistence remains atomic: a malformed class or failed navigation does
  not commit a partial Infinite Campus agenda.
- Logging remains structural and does not include student credentials or
  assignment content.

## Verification

Pure parser tests cover missing, low-score, due, completed, excluded, malformed,
and empty states. Navigation tests cover course enumeration, one bulk parse per
course, forced return to Grades between courses, and course-list reacquisition.
