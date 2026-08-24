(function () {
    const rootEl = document.getElementById("tc-react-root");
    if (!rootEl) {
        return;
    }

    if (!window.React || !window.ReactDOM) {
        rootEl.innerHTML =
            '<main style="padding:24px;font-family:system-ui,sans-serif"><h1>Dashboard unavailable</h1><p>The browser could not load the dashboard assets.</p></main>';
        return;
    }

    const { useEffect, useMemo, useState } = window.React;
    const h = window.React.createElement;

    function readPageData() {
        const element = document.getElementById("tc-page-data");
        if (!element) {
            return {};
        }
        try {
            return JSON.parse(element.textContent || "{}");
        } catch (_error) {
            return {};
        }
    }

    function cn() {
        return Array.from(arguments).filter(Boolean).join(" ");
    }

    function Icon({ name, className }) {
        const paths = {
            activity: [h("path", { key: 1, d: "M22 12h-4l-3 9L9 3l-3 9H2" })],
            alert: [
                h("path", {
                    key: 1,
                    d: "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
                }),
                h("path", { key: 2, d: "M12 9v4" }),
                h("path", { key: 3, d: "M12 17h.01" }),
            ],
            arrowLeft: [
                h("path", { key: 1, d: "m12 19-7-7 7-7" }),
                h("path", { key: 2, d: "M19 12H5" }),
            ],
            book: [
                h("path", { key: 1, d: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20" }),
                h("path", { key: 2, d: "M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5Z" }),
            ],
            check: [h("path", { key: 1, d: "M20 6 9 17l-5-5" })],
            external: [
                h("path", { key: 1, d: "M15 3h6v6" }),
                h("path", { key: 2, d: "M10 14 21 3" }),
                h("path", { key: 3, d: "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" }),
            ],
            home: [
                h("path", { key: 1, d: "m3 11 9-8 9 8" }),
                h("path", { key: 2, d: "M5 10v10h14V10" }),
            ],
            user: [
                h("path", { key: 1, d: "M19 21a7 7 0 0 0-14 0" }),
                h("circle", { key: 2, cx: "12", cy: "7", r: "4" }),
            ],
        };
        return h(
            "svg",
            {
                className: cn("h-4 w-4 shrink-0", className),
                viewBox: "0 0 24 24",
                fill: "none",
                stroke: "currentColor",
                strokeWidth: 2,
                strokeLinecap: "round",
                strokeLinejoin: "round",
                "aria-hidden": "true",
            },
            paths[name] || paths.activity,
        );
    }

    function Button({ children, href, icon, variant = "primary", className, ...props }) {
        const variants = {
            primary: "bg-brand-blue text-white hover:bg-brand-blueDark",
            orange: "bg-brand-orange text-white hover:bg-brand-orangeDark",
            outline: "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50",
            ghost: "text-slate-700 hover:bg-slate-100",
        };
        return h(
            "a",
            Object.assign({}, props, {
                href,
                className: cn(
                    "tc-button tc-focus-ring inline-flex h-10 items-center justify-center gap-2 rounded-md px-4 text-sm font-semibold shadow-sm transition",
                    "tc-button--" + variant,
                    variants[variant],
                    className,
                ),
            }),
            icon ? h(Icon, { name: icon }) : null,
            children,
        );
    }

    function Card({ children, className }) {
        return h(
            "section",
            {
                className: cn(
                    "tc-card rounded-xl border border-slate-200 bg-white shadow-panel",
                    className,
                ),
            },
            children,
        );
    }

    function Badge({ children, tone = "slate" }) {
        const tones = {
            success: "bg-emerald-50 text-emerald-700",
            danger: "bg-red-50 text-red-700",
            warning: "bg-amber-50 text-amber-700",
            slate: "bg-slate-100 text-slate-700",
            blue: "bg-brand-blueSoft text-brand-blueDark",
        };
        return h(
            "span",
            {
                className: cn(
                    "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold",
                    tones[tone],
                ),
            },
            children,
        );
    }

    const ERROR_DESCRIPTIONS = Object.freeze({
        bad_login: "The portal rejected the student's username or password.",
        configuration_missing: "The portal URL or login credentials are incomplete.",
        no_grades: "The portal opened successfully, but no grades were found.",
        scrape_failed: "The portal could not be read successfully.",
        unsupported_portal: "This portal does not have a supported agenda collector.",
        agenda_failed: "The student's agenda could not be collected.",
        agenda_runner_failed: "The agenda job stopped before it could finish.",
        runner_failed: "The grade job stopped before it could finish.",
        neon_unavailable: "The grade database is temporarily unavailable.",
        lease_expired: "The job took too long and its processing lease expired.",
        lease_renewal_failed: "The job could not keep its processing lease active.",
        result_post_failed: "The scrape finished, but its result could not be saved.",
    });

    const ERROR_PORTAL_LABELS = Object.freeze({
        asuprep: "ASU Prep",
        canvas: "Canvas",
        google_classroom: "Google Classroom",
        infinite_campus: "Infinite Campus",
        parentvue: "ParentVUE",
        powerschool: "PowerSchool",
        schoology: "Schoology",
    });

    function errorPortalLabel(portal) {
        return (
            ERROR_PORTAL_LABELS[portal] ||
            portal
                .split("_")
                .filter(Boolean)
                .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
                .join(" ")
        );
    }

    function errorDescription(code) {
        const normalized = String(code || "").trim().toLowerCase();
        if (!normalized) {
            return null;
        }
        if (ERROR_DESCRIPTIONS[normalized]) {
            return ERROR_DESCRIPTIONS[normalized];
        }

        const missingConfiguration = normalized.match(
            /^agenda([12])_configuration_missing$/,
        );
        if (missingConfiguration) {
            return `Portal ${missingConfiguration[1]} is missing its URL or login credentials.`;
        }

        const agendaFailure = normalized.match(/^agenda([12])_([a-z0-9_]+)_failed$/);
        if (agendaFailure) {
            return `Portal ${agendaFailure[1]}'s ${errorPortalLabel(agendaFailure[2])} agenda could not be collected.`;
        }

        return `The scraper reported an unexpected error. Reference code: ${normalized}.`;
    }

    function ErrorStatusBadge({ status, errorCode }) {
        const label = status || "unknown";
        const description = errorDescription(errorCode);
        const badge = h(Badge, { tone: statusTone(status) }, label);
        if (!description) {
            return badge;
        }
        return h(
            "span",
            {
                className: "tc-error-tooltip tc-focus-ring",
                tabIndex: 0,
                title: description,
                "aria-label": `${label}: ${description}`,
                "data-error-code": errorCode,
            },
            badge,
            h(
                "span",
                { className: "tc-error-tooltip__content", role: "tooltip" },
                description,
            ),
        );
    }

    function isSyncIssue(status) {
        return Boolean(
            status &&
                status !== "never" &&
                status !== "not_configured" &&
                status !== "synced",
        );
    }

    function syncStateText(status) {
        if (status === "synced") {
            return "Synchronized on last run";
        }
        if (status === "never" || status === "not_configured" || !status) {
            return "Not synchronized yet";
        }
        return "Issue on last run";
    }

    function SyncState({ label, status, updatedAt }) {
        const issue = isSyncIssue(status);
        const stateText = syncStateText(status);
        const description = issue ? errorDescription(status) : null;
        const accessibleLabel = `${label}: ${stateText}${description ? `. ${description}` : ""}`;
        return h(
            "div",
            {
                className: cn(
                    "flex flex-wrap items-center justify-end gap-2 rounded-md",
                    issue && "tc-focus-ring",
                ),
                tabIndex: issue ? 0 : undefined,
                title: description || undefined,
                "aria-label": accessibleLabel,
            },
            h("span", { className: "text-xs font-bold text-slate-500" }, label),
            h(
                Badge,
                { tone: issue ? "danger" : status === "synced" ? "success" : "slate" },
                h(Icon, { name: issue ? "alert" : status === "synced" ? "check" : "activity", className: "mr-1 h-3.5 w-3.5" }),
                stateText,
            ),
            h(
                "span",
                { className: "text-xs text-slate-500" },
                updatedAt ? formatDate(updatedAt) : "No run recorded",
            ),
        );
    }

    function StudentSyncWarning({ issues }) {
        const activeIssues = Array.isArray(issues) ? issues : [];
        if (!activeIssues.length) {
            return null;
        }
        const description = activeIssues
            .map((issue) => `${issue.label}: ${errorDescription(issue.status) || "Synchronization issue."}`)
            .join(" ");
        return h(
            "span",
            {
                className: "tc-sync-warning tc-focus-ring",
                tabIndex: 0,
                title: description,
                "aria-label": `Synchronization warning. ${description}`,
            },
            h(Icon, { name: "alert", className: "h-4 w-4" }),
        );
    }

    function Header({ data, title, subtitle, actions }) {
        return h(
            "header",
            { className: "tc-page-header" },
            h(
                "div",
                { className: "tc-header-ambient", "aria-hidden": "true" },
                h("span", { className: "tc-header-squares" }),
                h("span", { className: "tc-header-scanline" }),
            ),
            h(
                "div",
                { className: "tc-page-header__inner" },
                h(
                    "div",
                    { className: "tc-page-header__brand" },
                    h(
                        "div",
                        { className: "tc-logo-frame rounded-xl p-2" },
                        h("img", {
                            src: data.logoUrl,
                            alt: "Tutoring Club",
                            className: "h-12 w-12 object-contain",
                        }),
                    ),
                    h(
                        "div",
                        { className: "min-w-0" },
                        h("h1", { className: "tc-page-title text-2xl md:text-3xl" }, title),
                        h("p", { className: "tc-page-subtitle mt-1 text-sm" }, subtitle),
                    ),
                ),
                actions && actions.length
                    ? h("nav", { className: "tc-header-actions", "aria-label": "Dashboard navigation" }, actions)
                    : null,
            ),
        );
    }

    function Shell({ header, children, wide = false }) {
        return h(
            "div",
            { className: "tc-shell" },
            header,
            h("main", { className: cn("tc-page-main", wide && "tc-page-main--wide") }, children),
        );
    }

    function formatDate(value) {
        if (!value) {
            return "Not yet";
        }
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) {
            return value;
        }
        return parsed.toLocaleString(undefined, {
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
        });
    }

    function statusTone(status) {
        if (status === "synced" || status === "complete") {
            return "success";
        }
        if (
            status === "failed" ||
            (status &&
                status !== "never" &&
                status !== "not_configured" &&
                status !== "running")
        ) {
            return "danger";
        }
        if (status === "running") {
            return "blue";
        }
        return "slate";
    }

    function standingTone(standing) {
        const tones = {
            Good: "success",
            Fair: "warning",
            Poor: "danger",
        };
        return tones[standing] || "slate";
    }

    function GradeMovement({ change }) {
        if (change !== "+" && change !== "-") {
            return null;
        }
        const increased = change === "+";
        return h(
            "span",
            {
                className: cn(
                    "ml-1 font-extrabold",
                    increased ? "text-emerald-600" : "text-red-600",
                ),
                title: increased ? "Grade increased" : "Grade decreased",
                "aria-label": increased ? "Grade increased" : "Grade decreased",
            },
            change,
        );
    }

    function fuzzyNameMatch(student, query) {
        const normalize = (value) =>
            String(value || "")
                .normalize("NFD")
                .replace(/[\u0300-\u036f]/g, "")
                .toLowerCase()
                .trim();
        const name = normalize(`${student.firstName || ""} ${student.lastName || ""}`);
        const terms = normalize(query).split(/\s+/).filter(Boolean);

        return terms.every((term) => {
            if (name.includes(term)) {
                return true;
            }
            let termIndex = 0;
            for (const character of name) {
                if (character === term[termIndex]) {
                    termIndex += 1;
                }
            }
            return termIndex === term.length;
        });
    }

    const STANDING_RANK = Object.freeze({
        Unknown: 0,
        Poor: 1,
        Fair: 2,
        Good: 3,
    });
    const STUDENT_NAME_COLLATOR = new Intl.Collator("en", { sensitivity: "base" });

    function compareStudentNames(left, right) {
        const firstNameComparison = STUDENT_NAME_COLLATOR.compare(
            String(left.firstName || ""),
            String(right.firstName || ""),
        );
        if (firstNameComparison) {
            return firstNameComparison;
        }

        const lastNameComparison = STUDENT_NAME_COLLATOR.compare(
            String(left.lastName || ""),
            String(right.lastName || ""),
        );
        if (lastNameComparison) {
            return lastNameComparison;
        }

        return Number(left.id) - Number(right.id);
    }

    function studentStandingRank(student) {
        const standing = student.standing || "Unknown";
        return Object.prototype.hasOwnProperty.call(STANDING_RANK, standing)
            ? STANDING_RANK[standing]
            : STANDING_RANK.Unknown;
    }

    function compareStudents(left, right, sortConfig) {
        const comparison =
            sortConfig.key === "name"
                ? compareStudentNames(left, right)
                : sortConfig.key === "standing"
                  ? studentStandingRank(left) - studentStandingRank(right)
                  : 0;
        return sortConfig.direction === "desc" ? -comparison : comparison;
    }

    function filterAndSortStudents(students, searchQuery, sortConfig) {
        const filteredStudents = students.filter((student) => fuzzyNameMatch(student, searchQuery));
        if (!sortConfig.key) {
            return filteredStudents;
        }
        return [...filteredStudents].sort((left, right) => compareStudents(left, right, sortConfig));
    }

    function nextSortConfig(current, columnKey) {
        if (current.key !== columnKey) {
            return { key: columnKey, direction: "asc" };
        }
        return {
            key: columnKey,
            direction: current.direction === "asc" ? "desc" : "asc",
        };
    }

    function studentTabFromHash(hash) {
        return hash === "#heatmap" ? "heatmap" : "report";
    }

    function Metric({ label, value, tone = "blue" }) {
        const accents = {
            blue: "border-t-brand-blue",
            orange: "border-t-brand-orange",
            green: "border-t-emerald-500",
            red: "border-t-red-500",
        };
        return h(
            Card,
            { className: cn("border-t-4 p-5", accents[tone]) },
            h("p", { className: "text-xs font-bold uppercase tracking-wide text-slate-500" }, label),
            h("p", { className: "mt-2 text-3xl font-extrabold text-slate-900" }, String(value)),
        );
    }

    function useLiveJobs(initialJobs, jobsUrl) {
        const [jobs, setJobs] = useState(initialJobs || []);
        useEffect(() => {
            if (!jobsUrl) {
                return undefined;
            }
            let active = true;
            async function refresh() {
                try {
                    const response = await fetch(jobsUrl || "/api/jobs", {
                        headers: { Accept: "application/json" },
                        cache: "no-store",
                    });
                    if (!response.ok) {
                        return;
                    }
                    const payload = await response.json();
                    if (active && Array.isArray(payload.jobs)) {
                        setJobs(payload.jobs);
                    }
                } catch (_error) {
                    // Keep the last safe snapshot while the read dependency is unavailable.
                }
            }
            const timer = window.setInterval(refresh, 15000);
            return () => {
                active = false;
                window.clearInterval(timer);
            };
        }, [jobsUrl]);
        return jobs;
    }

    function JobCard({ job }) {
        const attempted = Number(job.attempted || 0);
        const total = Number(job.total || 0);
        const percent = total > 0 ? Math.min(100, Math.round((attempted / total) * 100)) : 0;
        const scope = job.studentId
            ? `Student ${job.studentId}`
            : job.franchiseId
              ? `Franchise ${job.franchiseId}`
              : "All franchises";
        return h(
            "article",
            { className: "rounded-lg border border-slate-200 bg-slate-50 p-4" },
            h(
                "div",
                { className: "flex flex-wrap items-center justify-between gap-3" },
                h(
                    "div",
                    null,
                    h("p", { className: "font-bold capitalize text-slate-900" }, `${job.kind} · ${scope}`),
                    h("p", { className: "mt-1 text-xs text-slate-500" }, `Started ${formatDate(job.startedAt)}`),
                ),
                h(ErrorStatusBadge, { status: job.status, errorCode: job.errorCode }),
            ),
            h(
                "div",
                { className: "mt-4 h-2 overflow-hidden rounded-full bg-slate-200" },
                h("div", {
                    className: "h-full rounded-full bg-brand-blue transition-all",
                    style: { width: `${percent}%` },
                }),
            ),
            h(
                "div",
                { className: "mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs font-semibold text-slate-600" },
                h("span", null, `${attempted}/${total} attempted`),
                h("span", { className: "text-emerald-700" }, `${job.success || 0} successful`),
                h("span", { className: "text-red-700" }, `${job.errors || 0} errors`),
                job.errorCode ? h("span", null, `Code: ${job.errorCode}`) : null,
            ),
        );
    }

    function HomePage({ data }) {
        const jobs = useLiveJobs(data.jobs, data.jobsUrl || "/api/jobs");
        const activeJobs = jobs.filter((job) => job.status === "running").length;
        return h(
            Shell,
            {
                wide: true,
                header: h(Header, {
                    data,
                    title: "Grade Operations Overview",
                    subtitle: "Public read-only view of runnable CRM students and canonical scrape activity",
                }),
            },
            h(
                "div",
                { className: "grid gap-4 sm:grid-cols-2 xl:grid-cols-4" },
                h(Metric, { label: "Runnable students", value: data.countAll || 0, tone: "blue" }),
                h(Metric, { label: "Synced", value: data.countSynced || 0, tone: "green" }),
                h(Metric, { label: "Bad logins", value: data.countBadLogins || 0, tone: "red" }),
                h(Metric, { label: "Active jobs", value: activeJobs, tone: "orange" }),
            ),
            h(
                "div",
                { className: "mt-6 grid gap-6 xl:grid-cols-[1.1fr_1.9fr]" },
                h(
                    Card,
                    { className: "p-5" },
                    h("h2", { className: "text-lg font-extrabold text-slate-900" }, "Live and recent jobs"),
                    h("p", { className: "mt-1 text-sm text-slate-500" }, "Updates automatically every 15 seconds."),
                    h(
                        "div",
                        { className: "mt-4 grid gap-3" },
                        jobs.length
                            ? jobs.map((job) => h(JobCard, { key: job.id, job }))
                            : h("p", { className: "rounded-lg bg-slate-50 p-4 text-sm text-slate-600" }, "No job history yet."),
                    ),
                ),
                h(
                    Card,
                    { className: "p-5" },
                    h("h2", { className: "text-lg font-extrabold text-slate-900" }, "Franchises"),
                    h("p", { className: "mt-1 text-sm text-slate-500" }, "Only students with complete CRM portal setup are counted."),
                    h(
                        "div",
                        { className: "mt-4 grid gap-4 md:grid-cols-2 2xl:grid-cols-3" },
                        data.franchises && data.franchises.length
                            ? data.franchises.map((franchise) =>
                                  h(
                                      "a",
                                      {
                                          key: franchise.id,
                                          href: franchise.url,
                                          className: "tc-focus-ring rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-brand-blue hover:shadow-panel",
                                      },
                                      h(
                                          "div",
                                          { className: "flex items-center justify-between gap-3" },
                                          h("h3", { className: "text-xl font-extrabold text-slate-900" }, `${franchise.name} (${franchise.id})`),
                                          h(Badge, { tone: franchise.errorCount ? "danger" : "success" }, `${franchise.synced}/${franchise.total} synced`),
                                      ),
                                      h("p", { className: "mt-3 text-sm text-slate-600" }, `${franchise.errorCount || 0} errors · ${franchise.badLogins || 0} bad logins`),
                                      h("p", { className: "mt-2 text-xs text-slate-500" }, `Last update ${formatDate(franchise.lastUpdated)}`),
                                  ),
                              )
                            : h("p", { className: "text-sm text-slate-600" }, "No runnable CRM students were found."),
                    ),
                ),
            ),
        );
    }

    function gradeCourseKey(course) {
        return String(course || "")
            .replace(/^\s*\d+\s*:\s*/, "")
            .trim()
            .replace(/\s+/g, " ")
            .toLowerCase();
    }

    function GradeRow({ grade, agenda }) {
        const course = grade.course;
        const rowContent = [
            h(
                "span",
                {
                    key: "course",
                    className: "min-w-0 flex-1 truncate text-sm font-semibold text-slate-700",
                    title: course,
                },
                course,
            ),
            agenda
                ? h(
                      "span",
                      { key: "count", className: "shrink-0 text-xs font-bold text-brand-blueDark" },
                      `${agenda.assignments.length} ${agenda.assignments.length === 1 ? "assignment" : "assignments"}`,
                  )
                : null,
            h(
                "span",
                {
                    key: "grade",
                    className: "shrink-0 whitespace-nowrap font-mono text-sm font-bold text-slate-900",
                },
                Number(grade.grade).toFixed(1),
                h(GradeMovement, { change: grade.change }),
            ),
        ];
        if (!agenda) {
            return h(
                "div",
                { className: "flex min-w-0 items-center gap-3 rounded-md bg-slate-50 px-3 py-2" },
                rowContent,
            );
        }
        return h(
            "details",
            { className: "tc-grade-agenda rounded-md border border-slate-200 bg-slate-50" },
            h(
                "summary",
                { className: "tc-focus-ring flex min-w-0 items-center gap-3 rounded-md px-3 py-2" },
                rowContent,
            ),
            h(
                "div",
                { className: "grid gap-2 border-t border-slate-200 p-3" },
                agenda.assignments.map((assignment, index) =>
                    h(AgendaAssignment, {
                        key: `${assignment.status}-${assignment.dueDate}-${assignment.title}-${index}`,
                        assignment,
                    }),
                ),
            ),
        );
    }

    function GradeList({ grades, empty = "No grade data yet.", agendaByCourse }) {
        const gradeItems = grades || [];
        if (!gradeItems.length) {
            return h("p", { className: "text-sm text-slate-500" }, empty);
        }
        return h(
            "div",
            { className: "grid gap-2" },
            gradeItems.map((grade) =>
                h(GradeRow, {
                    key: grade.course,
                    grade,
                    agenda: agendaByCourse && agendaByCourse.get(gradeCourseKey(grade.course)),
                }),
            ),
        );
    }

    function StudentCard({ student }) {
        return h(
            Card,
            { className: "flex flex-col p-5" },
            h(
                "div",
                { className: "flex items-start justify-between gap-3" },
                h(
                    "div",
                    null,
                    h("h2", { className: "text-lg font-extrabold text-slate-900" }, `${student.firstName} ${student.lastName}`),
                    h("p", { className: "mt-1 text-sm text-slate-500" }, `Grade ${student.gradeLevel || "—"} · CRM ${student.id}`),
                ),
                h(StudentSyncWarning, { issues: student.syncIssues }),
            ),
            h("div", { className: "my-4 h-px bg-slate-200" }),
            h(GradeList, { grades: student.gradesSnapshot }),
            h(
                "div",
                { className: "mt-auto flex flex-wrap gap-2 pt-5" },
                h(Button, { href: student.detailUrl, icon: "user" }, "View report"),
                h(
                    Button,
                    {
                        href: `${student.detailUrl}#heatmap`,
                        icon: "activity",
                        variant: "outline",
                    },
                    "Heatmap",
                ),
                student.portalUrl
                    ? h(
                          Button,
                          {
                              href: student.portalUrl,
                              icon: "external",
                              variant: "outline",
                              target: "_blank",
                              rel: "noopener noreferrer",
                          },
                          "Primary portal",
                      )
                    : null,
            ),
        );
    }

    function SortableHeading({ label, columnKey, sortConfig, onSort, className }) {
        const isActive = sortConfig.key === columnKey;
        const activeDirection = isActive ? sortConfig.direction : null;
        const marker = !isActive ? "↕" : activeDirection === "asc" ? "↑" : "↓";
        const nextDirection = activeDirection === "asc" ? "descending" : "ascending";
        const headingProps = { className };
        if (isActive) {
            headingProps["aria-sort"] = activeDirection === "asc" ? "ascending" : "descending";
        }

        return h(
            "th",
            headingProps,
            h(
                "button",
                {
                    type: "button",
                    className:
                        "tc-focus-ring flex w-full cursor-pointer items-center gap-2 rounded-sm bg-transparent text-left text-inherit",
                    onClick: () => onSort(columnKey),
                    "aria-label": `Sort ${label} ${nextDirection}`,
                },
                h("span", null, label),
                h("span", { className: "text-sm leading-none", "aria-hidden": "true" }, marker),
            ),
        );
    }

    function StudentTable({ students, sortConfig, onSort }) {
        const headingClass =
            "bg-brand-blue px-4 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white";
        const cellClass = "px-4 py-4 align-top";

        return h(
            Card,
            null,
            h(
                "div",
                { className: "tc-table-scroll overflow-x-auto" },
                h(
                    "table",
                    { className: "tc-data-table min-w-[1360px] w-full border-collapse" },
                    h(
                        "thead",
                        null,
                        h(
                            "tr",
                            null,
                            h(SortableHeading, {
                                label: "Student",
                                columnKey: "name",
                                sortConfig,
                                onSort,
                                className: headingClass,
                            }),
                            h("th", { className: headingClass }, "Primary Portal"),
                            h("th", { className: headingClass }, "Recent Grades"),
                            h("th", { className: headingClass }, "Low Grades"),
                            h("th", { className: headingClass }, "High Grades"),
                            h(SortableHeading, {
                                label: "Standing",
                                columnKey: "standing",
                                sortConfig,
                                onSort,
                                className: headingClass,
                            }),
                            h("th", { className: headingClass }, "Grades Updated"),
                            h("th", { className: headingClass }, "Actions"),
                        ),
                    ),
                    h(
                        "tbody",
                        null,
                        students.map((student) =>
                            h(
                                "tr",
                                { key: student.id },
                                h(
                                    "td",
                                    { className: cellClass },
                                    h(
                                        "div",
                                        { className: "flex items-center gap-2" },
                                        h(
                                            "a",
                                            {
                                                href: student.detailUrl,
                                                className: "font-extrabold text-brand-blueDark hover:text-brand-orangeDark",
                                            },
                                            `${student.firstName || ""} ${student.lastName || ""}`.trim(),
                                        ),
                                        h(StudentSyncWarning, { issues: student.syncIssues }),
                                    ),
                                    h(
                                        "div",
                                        { className: "mt-1 text-xs font-semibold text-slate-500" },
                                        `Grade ${student.gradeLevel || "—"} · CRM ${student.id}`,
                                    ),
                                ),
                                h(
                                    "td",
                                    { className: cellClass },
                                    student.portalUrl
                                        ? h(
                                              "a",
                                              {
                                                  href: student.portalUrl,
                                                  target: "_blank",
                                                  rel: "noopener noreferrer",
                                                  className:
                                                      "inline-flex items-center gap-1 font-bold text-brand-blue hover:text-brand-orangeDark",
                                              },
                                              "Open portal",
                                              h(Icon, { name: "external", className: "h-3.5 w-3.5" }),
                                          )
                                        : h("span", { className: "text-sm text-slate-500" }, "Unavailable"),
                                ),
                                h(
                                    "td",
                                    { className: cn(cellClass, "max-w-[280px]") },
                                    h(GradeList, { grades: student.gradesSnapshot }),
                                ),
                                h(
                                    "td",
                                    { className: cn(cellClass, "max-w-[280px]") },
                                    h(GradeList, { grades: student.lowGrades, empty: "No low grades." }),
                                ),
                                h(
                                    "td",
                                    { className: cn(cellClass, "max-w-[280px]") },
                                    h(GradeList, { grades: student.highGrades, empty: "No high grades." }),
                                ),
                                h(
                                    "td",
                                    { className: cellClass },
                                    h(
                                        Badge,
                                        { tone: standingTone(student.standing) },
                                        student.standing || "Unknown",
                                    ),
                                ),
                                h(
                                    "td",
                                    { className: cn(cellClass, "whitespace-nowrap text-sm text-slate-600") },
                                    formatDate(student.updatedAt),
                                ),
                                h(
                                    "td",
                                    { className: cellClass },
                                    h(
                                        "div",
                                        { className: "flex flex-wrap gap-2" },
                                        h(Button, { href: student.detailUrl, icon: "user", variant: "outline" }, "View report"),
                                        h(
                                            Button,
                                            {
                                                href: `${student.detailUrl}#heatmap`,
                                                icon: "activity",
                                                variant: "orange",
                                            },
                                            "Heatmap",
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        );
    }

    function FranchisePage({ data }) {
        const [searchQuery, setSearchQuery] = useState("");
        const [sortConfig, setSortConfig] = useState({ key: null, direction: "asc" });
        const students = data.students || [];
        const visibleStudents = useMemo(
            () => filterAndSortStudents(students, searchQuery, sortConfig),
            [students, searchQuery, sortConfig],
        );
        const handleSort = (columnKey) => {
            setSortConfig((current) => nextSortConfig(current, columnKey));
        };

        return h(
            Shell,
            {
                wide: true,
                header: h(Header, {
                    data,
                    title: data.franchiseName,
                    subtitle: `${(data.students || []).length} runnable students · read-only`,
                }),
            },
            h(
                "div",
                { className: "mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between" },
                h(
                    "nav",
                    { className: "flex flex-wrap gap-2", "aria-label": "Grade filters" },
                    (data.filters || []).map((filter) =>
                        h(
                            Button,
                            {
                                key: filter.value,
                                href: filter.url,
                                variant: data.gradeFilter === filter.value ? "orange" : "outline",
                            },
                            filter.label,
                        ),
                    ),
                ),
                h(
                    "label",
                    { className: "tc-search-field w-full lg:max-w-sm" },
                    h("span", { className: "tc-search-field__label" }, "Search students"),
                    h(
                        "input",
                        {
                            type: "search",
                            className: "tc-input",
                            value: searchQuery,
                            placeholder: "Search by student name…",
                            onChange: (event) => setSearchQuery(event.target.value),
                        },
                    ),
                ),
            ),
            visibleStudents.length
                ? h(StudentTable, {
                      students: visibleStudents,
                      sortConfig,
                      onSort: handleSort,
                  })
                : h(
                      Card,
                      { className: "p-8 text-center text-slate-600" },
                      searchQuery ? "No students match this search." : "No runnable students match this filter.",
                  ),
        );
    }

    function GradeHistory({ history }) {
        const weeks = Object.entries(history || {})
            .sort(([left], [right]) => right.localeCompare(left))
            .slice(1, 2);
        if (!weeks.length) {
            return h(
                "p",
                { className: "text-sm text-slate-500" },
                "No completed grade history yet.",
            );
        }
        return h(
            "div",
            { className: "grid gap-4" },
            weeks.map(([week, grades]) =>
                h(
                    "section",
                    { key: week, className: "rounded-lg border border-slate-200 p-4" },
                    h("h3", { className: "font-bold text-slate-900" }, week),
                    h(
                        "div",
                        { className: "mt-3 grid gap-2 sm:grid-cols-2" },
                        Object.entries(grades).map(([course, grade]) =>
                            h(
                                "div",
                                { key: course, className: "flex justify-between gap-3 rounded-md bg-slate-50 px-3 py-2 text-sm" },
                                h("span", { className: "font-semibold text-slate-700" }, course),
                                h("span", { className: "font-mono font-bold text-slate-900" }, Number(grade).toFixed(1)),
                            ),
                        ),
                    ),
                ),
            ),
        );
    }

    function GradeHeatmap({ history }) {
        const weeks = Object.keys(history || {}).sort();
        const courses = Array.from(
            new Set(
                weeks.flatMap((week) => Object.keys(history[week] || {})),
            ),
        ).sort();
        if (!weeks.length || !courses.length) {
            return h("p", { className: "text-sm text-slate-500" }, "No heatmap data yet.");
        }
        function cellColor(value) {
            if (value >= 90) return "#bbf7d0";
            if (value >= 80) return "#bfdbfe";
            if (value >= 70) return "#fde68a";
            return "#fecaca";
        }
        return h(
            "div",
            { className: "overflow-x-auto" },
            h(
                "table",
                { className: "tc-heatmap-table min-w-full border-collapse text-sm" },
                h(
                    "thead",
                    null,
                    h(
                        "tr",
                        null,
                        h(
                            "th",
                            { scope: "col" },
                            h("span", { className: "tc-heatmap-course-label" }, "Course"),
                        ),
                        weeks.map((week) => h("th", { key: week, scope: "col" }, week)),
                    ),
                ),
                h(
                    "tbody",
                    null,
                    courses.map((course) =>
                        h(
                            "tr",
                            { key: course },
                            h(
                                "th",
                                { scope: "row", title: course },
                                h("span", { className: "tc-heatmap-course-label" }, course),
                            ),
                            weeks.map((week) => {
                                const value = history[week] && history[week][course];
                                return h(
                                    "td",
                                    {
                                        key: `${course}-${week}`,
                                        className: "tc-heatmap-cell",
                                        style:
                                            typeof value === "number"
                                                ? { backgroundColor: cellColor(value) }
                                                : undefined,
                                        title:
                                            typeof value === "number"
                                                ? `${course}: ${Number(value).toFixed(1)}`
                                                : `${course}: no grade`,
                                    },
                                    typeof value === "number" ? Number(value).toFixed(1) : "—",
                                );
                            }),
                        ),
                    ),
                ),
            ),
        );
    }

    const AGENDA_STATUSES = {
        missing: { marker: "M", label: "Missing assignment" },
        low_score: { marker: "LOW GRADE", label: "Low-grade assignment" },
        due: { marker: "DUE", label: "Upcoming assignment" },
    };

    function AgendaAssignment({ assignment }) {
        const status = AGENDA_STATUSES[assignment.status] || AGENDA_STATUSES.due;
        return h(
            "article",
            { className: "tc-agenda-assignment" },
            h(
                "span",
                {
                    className: cn(
                        "tc-agenda-marker",
                        `tc-agenda-marker--${assignment.status}`,
                    ),
                    "aria-label": status.label,
                },
                status.marker,
            ),
            h(
                "span",
                { className: "min-w-0 flex-1 truncate", title: assignment.title },
                assignment.title,
            ),
            h(
                "time",
                {
                    className: "shrink-0 text-xs text-slate-500",
                    dateTime: assignment.dueDate,
                },
                assignment.dueDisplay,
            ),
        );
    }

    function AgendaClass({ classGroup }) {
        return h(
            "details",
            { className: "tc-agenda-class rounded-lg border border-slate-200" },
            h(
                "summary",
                { className: "tc-focus-ring flex items-center justify-between gap-3 rounded-lg px-3 py-3" },
                h(
                    "span",
                    {
                        className: "min-w-0 truncate font-bold text-slate-900",
                        title: classGroup.name,
                    },
                    classGroup.name,
                ),
                h(
                    "span",
                    { className: "shrink-0 text-xs font-bold text-slate-500" },
                    `${classGroup.count} ${classGroup.count === 1 ? "assignment" : "assignments"}`,
                ),
            ),
            h(
                "div",
                { className: "grid gap-2 border-t border-slate-200 p-3" },
                classGroup.assignments.map((assignment, index) => {
                    return h(
                        AgendaAssignment,
                        {
                            key: `${assignment.status}-${assignment.dueDate}-${assignment.title}-${index}`,
                            assignment,
                        },
                    );
                }),
            ),
        );
    }

    const GRADE_AGENDA_PORTALS = new Set(["infinite_campus", "parentvue", "canvas"]);

    function isConfiguredAgendaSlot(slot) {
        return Boolean(
            slot &&
                (slot.portal ||
                    (Array.isArray(slot.weeks) && slot.weeks.length) ||
                    isSyncIssue(slot.status)),
        );
    }

    function studentAgendaLayout(slots) {
        const agendaSlots = slots || [];
        const primary =
            agendaSlots.find((slot) => slot && slot.number === 1) || agendaSlots[0] || null;
        const secondary =
            agendaSlots.find((slot) => slot && slot.number === 2) || agendaSlots[1] || null;
        const gradeSlot =
            isConfiguredAgendaSlot(primary) && GRADE_AGENDA_PORTALS.has(primary.portal)
                ? primary
                : null;
        const standaloneSlot = isConfiguredAgendaSlot(secondary)
            ? secondary
            : isConfiguredAgendaSlot(primary) && !gradeSlot
              ? primary
              : null;
        return { gradeSlot, standaloneSlot };
    }

    function gradeAgendaByCourse(slot) {
        const courses = new Map();
        for (const week of (slot && slot.weeks) || []) {
            for (const classGroup of week.classes || []) {
                const key = gradeCourseKey(classGroup.name);
                const assignments = classGroup.assignments || [];
                if (!key || !assignments.length) {
                    continue;
                }
                if (!courses.has(key)) {
                    courses.set(key, { assignments: [] });
                }
                const course = courses.get(key);
                course.assignments.push(...assignments);
            }
        }
        for (const course of courses.values()) {
            course.assignments.sort(
                (left, right) =>
                    String(right.dueDate || "").localeCompare(String(left.dueDate || "")) ||
                    String(right.dueTime || "").localeCompare(String(left.dueTime || "")) ||
                    String(left.title || "").localeCompare(String(right.title || "")),
            );
        }
        return courses;
    }

    function AgendaCard({ slot }) {
        const heading = `Agenda${slot.portalLabel ? ` · ${slot.portalLabel}` : ""}`;
        return h(
            Card,
            { className: "tc-report-card tc-agenda-card p-5" },
            h(
                "div",
                { className: "flex flex-wrap items-start justify-between gap-3" },
                h("h2", { className: "text-lg font-extrabold text-slate-900" }, heading),
                h(SyncState, {
                    label: "Agenda",
                    status: slot.status,
                    updatedAt: slot.updatedAt,
                }),
            ),
            h(
                "div",
                {
                    className: "mt-2 flex flex-wrap gap-3 text-xs font-bold text-slate-500",
                    "aria-label": "Assignment status legend",
                },
                h(
                    "span",
                    null,
                    h("span", { className: "tc-agenda-marker tc-agenda-marker--missing" }, "M"),
                    " Missing",
                ),
                h(
                    "span",
                    null,
                    h( "span", { className: "tc-agenda-marker tc-agenda-marker--low_score" }, "LOW"),
                    " Low Grade",
                ),
                h(
                    "span",
                    null,
                    h("span", { className: "tc-agenda-marker tc-agenda-marker--due" }, "DUE"),
                    " Upcoming",
                ),
            ),
            h(
                "div",
                {
                    className: "tc-report-card__scroll tc-scrollbar tc-focus-ring mt-4 grid content-start gap-4 rounded-md pr-2",
                    tabIndex: 0,
                    "aria-label": `${heading} assignments`,
                },
                (slot.weeks || []).map((week) =>
                    h(
                        "section",
                        { key: week.weekStart, className: "grid gap-2" },
                        h("h3", { className: "font-extrabold text-slate-900" }, week.label),
                        week.classes.map((classGroup) =>
                            h(AgendaClass, { key: classGroup.name, classGroup }),
                        ),
                    ),
                ),
            ),
        );
    }

    function AgendaItemsCard({ items }) {
        return h(
            Card,
            { className: "tc-report-card tc-agenda-card p-5" },
            h("h2", { className: "text-lg font-extrabold text-slate-900" }, "Agenda"),
            h(
                "div",
                { className: "mt-4 grid gap-3" },
                items && items.length
                    ? items.map((item, index) =>
                          h(
                              "article",
                              {
                                  key: `${item.dueDate}-${item.course}-${index}`,
                                  className: "rounded-lg border border-slate-200 p-4",
                              },
                              h(
                                  "p",
                                  {
                                      className:
                                          "text-xs font-bold uppercase tracking-wide text-brand-orangeDark",
                                  },
                                  item.dueDate,
                              ),
                              h("h3", { className: "mt-1 font-bold text-slate-900" }, item.title),
                              h("p", { className: "mt-1 text-sm text-slate-500" }, item.course),
                          ),
                      )
                    : h("p", { className: "text-sm text-slate-500" }, "No agenda items yet."),
            ),
        );
    }

    function StudentPage({ data }) {
        const student = data.student || {};
        const agendaLayout = studentAgendaLayout(student.agendaSlots);
        const courseAgendas = gradeAgendaByCourse(agendaLayout.gradeSlot);
        const standaloneAgenda = agendaLayout.standaloneSlot
            ? h(AgendaCard, { slot: agendaLayout.standaloneSlot })
            : student.agendaItems && student.agendaItems.length
              ? h(AgendaItemsCard, { items: student.agendaItems })
              : null;
        const [activeTab, setActiveTab] = useState(
            studentTabFromHash(window.location.hash),
        );
        useEffect(() => {
            function syncTab() {
                setActiveTab(studentTabFromHash(window.location.hash));
            }
            window.addEventListener("hashchange", syncTab);
            return () => window.removeEventListener("hashchange", syncTab);
        }, []);
        const actions = [
            h(Button, { key: "back", href: data.backUrl, icon: "arrowLeft", variant: "outline" }, "Back"),
        ];
        if (student.portalUrl) {
            actions.push(
                h(
                    Button,
                    {
                        key: "portal",
                        href: student.portalUrl,
                        icon: "external",
                        variant: "orange",
                        target: "_blank",
                        rel: "noopener noreferrer",
                    },
                    "Primary portal",
                ),
            );
        }
        const currentGradesContent = [
            h(
                "div",
                { key: "heading", className: "flex flex-wrap items-start justify-between gap-3" },
                h("h2", { className: "text-lg font-extrabold text-slate-900" }, "Current grades"),
                h(
                    "div",
                    { className: "grid justify-items-end gap-2" },
                    h(SyncState, {
                        label: "Grades",
                        status: student.status,
                        updatedAt: student.updatedAt,
                    }),
                    agendaLayout.gradeSlot
                        ? h(SyncState, {
                              label: "Assignments",
                              status: agendaLayout.gradeSlot.status,
                              updatedAt: agendaLayout.gradeSlot.updatedAt,
                          })
                        : null,
                ),
            ),
            h(
                "div",
                {
                    key: "grades",
                    className: "tc-report-card__scroll tc-scrollbar tc-focus-ring mt-4 rounded-md pr-2",
                    tabIndex: 0,
                    "aria-label": "Current grades and assignments",
                },
                h(GradeList, {
                    grades: student.gradesSnapshot,
                    agendaByCourse: courseAgendas,
                }),
            ),
        ];
        const gradeHistoryHeading = h(
            "h2",
            { className: "text-lg font-extrabold text-slate-900" },
            "Grade history",
        );
        return h(
            Shell,
            {
                wide: true,
                header: h(Header, {
                    data,
                    title: `${student.firstName || "Student"} ${student.lastName || ""}`.trim(),
                    subtitle: `Grade ${student.gradeLevel || "—"} · CRM ${student.id || "—"} · read-only`,
                    actions,
                }),
            },
            h(
                "nav",
                { className: "mb-5 flex flex-wrap gap-2", "aria-label": "Student report views" },
                h(
                    Button,
                    {
                        href: "#report",
                        variant: activeTab === "report" ? "orange" : "outline",
                        onClick: () => setActiveTab("report"),
                    },
                    "Report",
                ),
                h(
                    Button,
                    {
                        href: "#heatmap",
                        variant: activeTab === "heatmap" ? "orange" : "outline",
                        onClick: () => setActiveTab("heatmap"),
                    },
                    "Heatmap",
                ),
            ),
            activeTab === "heatmap"
                ? h(
                      Card,
                      { className: "p-5" },
                      h("h2", { className: "text-lg font-extrabold text-slate-900" }, "Grade Heatmap"),
                      h("p", { className: "mt-1 text-sm text-slate-500" }, "Weeks across the top, courses down the side."),
                      h("div", { className: "mt-5" }, h(GradeHeatmap, { history: student.grades })),
                  )
                :
                  h(
                      "div",
                      { className: "grid gap-6" },
                      h(
                          "div",
                          { className: "tc-grade-row" },
                          h(
                              Card,
                              { className: "tc-report-card tc-grade-card p-5" },
                              currentGradesContent,
                          ),
                          h(
                              Card,
                              { className: "tc-report-card tc-grade-card p-5" },
                              gradeHistoryHeading,
                              h(
                                  "div",
                                  {
                                      className: "tc-report-card__scroll tc-scrollbar tc-focus-ring mt-4 rounded-md pr-2",
                                      tabIndex: 0,
                                      "aria-label": "Grade history",
                                  },
                                  h(GradeHistory, { history: student.grades }),
                              ),
                          ),
                      ),
                      standaloneAgenda
                          ? h(
                                "div",
                                { className: "grid gap-6" },
                                standaloneAgenda,
                            )
                          : null,
                  ),
        );
    }

    function App({ data }) {
        const page = useMemo(() => data.page, [data.page]);
        if (data.page === "home") {
            return h(HomePage, { data });
        }
        if (data.page === "franchise") {
            return h(FranchisePage, { data });
        }
        if (data.page === "student") {
            return h(StudentPage, { data });
        }
        return h("main", { className: "p-8 font-sans" }, `Unknown dashboard page: ${page || "none"}`);
    }

    const pageData = readPageData();
    if (window.ReactDOM.createRoot) {
        window.ReactDOM.createRoot(rootEl).render(h(App, { data: pageData }));
    } else {
        window.ReactDOM.render(h(App, { data: pageData }), rootEl);
    }
})();
