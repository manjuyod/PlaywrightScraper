(function () {
    const rootEl = document.getElementById("tc-react-root");
    if (!rootEl) {
        return;
    }

    if (!window.React || !window.ReactDOM) {
        rootEl.innerHTML =
            '<div style="padding:24px;font-family:system-ui,sans-serif">The dashboard UI could not load. Check the browser network connection and refresh.</div>';
        return;
    }

    const { useEffect, useMemo, useRef, useState } = window.React;
    const h = window.React.createElement;

    function readPageData() {
        const dataEl = document.getElementById("tc-page-data");
        if (!dataEl) {
            return {};
        }
        try {
            return JSON.parse(dataEl.textContent || "{}");
        } catch (error) {
            console.error("Unable to parse dashboard data", error);
            return {};
        }
    }

    function cn() {
        return Array.from(arguments).filter(Boolean).join(" ");
    }

    function Icon({ name, className }) {
        const common = {
            className: cn("h-4 w-4 shrink-0", className),
            viewBox: "0 0 24 24",
            fill: "none",
            stroke: "currentColor",
            strokeWidth: 2,
            strokeLinecap: "round",
            strokeLinejoin: "round",
            "aria-hidden": "true",
            focusable: "false",
        };

        const paths = {
            activity: [
                ["path", { d: "M22 12h-4l-3 9L9 3l-3 9H2" }],
            ],
            alert: [
                ["path", { d: "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" }],
                ["path", { d: "M12 9v4" }],
                ["path", { d: "M12 17h.01" }],
            ],
            arrowLeft: [
                ["path", { d: "m12 19-7-7 7-7" }],
                ["path", { d: "M19 12H5" }],
            ],
            book: [
                ["path", { d: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20" }],
                ["path", { d: "M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5Z" }],
            ],
            check: [
                ["path", { d: "M20 6 9 17l-5-5" }],
            ],
            chevronDown: [
                ["path", { d: "m6 9 6 6 6-6" }],
            ],
            down: [
                ["path", { d: "M12 5v14" }],
                ["path", { d: "m19 12-7 7-7-7" }],
            ],
            external: [
                ["path", { d: "M15 3h6v6" }],
                ["path", { d: "M10 14 21 3" }],
                ["path", { d: "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" }],
            ],
            grid: [
                ["path", { d: "M3 3h7v7H3z" }],
                ["path", { d: "M14 3h7v7h-7z" }],
                ["path", { d: "M14 14h7v7h-7z" }],
                ["path", { d: "M3 14h7v7H3z" }],
            ],
            key: [
                ["circle", { cx: "7.5", cy: "15.5", r: "5.5" }],
                ["path", { d: "m21 2-9.6 9.6" }],
                ["path", { d: "m15.5 7.5 3 3L22 7l-3-3" }],
            ],
            logOut: [
                ["path", { d: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" }],
                ["path", { d: "m16 17 5-5-5-5" }],
                ["path", { d: "M21 12H9" }],
            ],
            minus: [
                ["path", { d: "M5 12h14" }],
            ],
            plus: [
                ["path", { d: "M12 5v14" }],
                ["path", { d: "M5 12h14" }],
            ],
            refresh: [
                ["path", { d: "M21 12a9 9 0 0 1-9 9 9.8 9.8 0 0 1-6.9-2.9L3 16" }],
                ["path", { d: "M3 21v-5h5" }],
                ["path", { d: "M3 12a9 9 0 0 1 9-9 9.8 9.8 0 0 1 6.9 2.9L21 8" }],
                ["path", { d: "M21 3v5h-5" }],
            ],
            search: [
                ["circle", { cx: "11", cy: "11", r: "8" }],
                ["path", { d: "m21 21-4.3-4.3" }],
            ],
            shield: [
                ["path", { d: "M20 13c0 5-3.5 7.5-7.7 8.8a1 1 0 0 1-.6 0C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.3-2.5a1.2 1.2 0 0 1 1.4 0C14.5 3.8 17 5 19 5a1 1 0 0 1 1 1Z" }],
            ],
            trash: [
                ["path", { d: "M3 6h18" }],
                ["path", { d: "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" }],
                ["path", { d: "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" }],
                ["path", { d: "M10 11v6" }],
                ["path", { d: "M14 11v6" }],
            ],
            up: [
                ["path", { d: "M12 19V5" }],
                ["path", { d: "m5 12 7-7 7 7" }],
            ],
            user: [
                ["path", { d: "M19 21a7 7 0 0 0-14 0" }],
                ["circle", { cx: "12", cy: "7", r: "4" }],
            ],
            x: [
                ["path", { d: "M18 6 6 18" }],
                ["path", { d: "m6 6 12 12" }],
            ],
        };

        return h(
            "svg",
            common,
            (paths[name] || paths.activity).map(([tag, attrs], index) =>
                h(tag, Object.assign({ key: index }, attrs)),
            ),
        );
    }

    function Button({
        children,
        className,
        href,
        icon,
        size = "md",
        variant = "primary",
        ...props
    }) {
        const variants = {
            primary:
                "bg-brand-blue text-white shadow-sm hover:bg-brand-blueDark",
            orange:
                "bg-brand-orange text-white shadow-sm hover:bg-brand-orangeDark",
            outline:
                "border border-slate-300 bg-white text-ink shadow-sm hover:bg-slate-50",
            ghost: "text-slate-700 hover:bg-slate-100",
            danger: "bg-red-600 text-white shadow-sm hover:bg-red-700",
            gold: "bg-amber-300 text-slate-900 shadow-sm hover:bg-amber-400",
        };
        const sizes = {
            sm: "h-9 px-3 text-xs",
            md: "h-10 px-4 text-sm",
            lg: "h-11 px-5 text-sm",
            icon: "h-10 w-10 p-0",
        };
        const base =
            "tc-focus-ring inline-flex items-center justify-center gap-2 rounded-md font-semibold transition disabled:pointer-events-none disabled:opacity-60";
        const tag = href ? "a" : "button";
        const finalProps = Object.assign({}, props, {
            className: cn(
                "tc-button",
                `tc-button--${variant}`,
                `tc-button--${size}`,
                base,
                variants[variant],
                sizes[size],
                className,
            ),
        });
        if (href) {
            finalProps.href = href;
        } else if (!finalProps.type) {
            finalProps.type = "button";
        }
        return h(tag, finalProps, icon ? h(Icon, { name: icon }) : null, children);
    }

    function Card({ children, className }) {
        return h(
            "section",
            {
                className: cn(
                    "tc-card",
                    "rounded-lg border border-slate-200 bg-white shadow-panel",
                    className,
                ),
            },
            children,
        );
    }

    function Badge({ children, tone = "neutral", className }) {
        const tones = {
            neutral: "bg-brand-blueSoft text-brand-blueDark",
            success: "bg-emerald-50 text-emerald-700",
            warning: "bg-brand-orangeSoft text-brand-orangeDark",
            danger: "bg-red-50 text-red-700",
            slate: "bg-slate-100 text-slate-700",
        };
        return h(
            "span",
            {
                className: cn(
                    "tc-badge",
                    `tc-badge--${tone}`,
                    "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold",
                    tones[tone],
                    className,
                ),
            },
            children,
        );
    }

    function Field({ label, children, optional }) {
        return h(
            "label",
            { className: "tc-field grid gap-1.5 text-sm font-semibold text-slate-800" },
            h(
                "span",
                null,
                label,
                optional
                    ? h(
                          "span",
                          { className: "font-medium text-slate-500" },
                          " optional",
                      )
                    : null,
            ),
            children,
        );
    }

    function Input(props) {
        return h(
            "input",
            Object.assign({}, props, {
                className: cn(
                    "tc-input",
                    "tc-focus-ring h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900 shadow-sm placeholder:text-slate-400",
                    props.className,
                ),
            }),
        );
    }

    function HiddenCsrf({ token }) {
        return h("input", {
            type: "hidden",
            name: "csrf_token",
            value: token || "",
        });
    }

    function HiddenMasterPassword({ value }) {
        return h("input", {
            type: "hidden",
            name: "master_password",
            value: value || "",
        });
    }

    function FlashMessages({ messages }) {
        if (!messages || messages.length === 0) {
            return null;
        }
        return h(
            "div",
            { className: "tc-flash-stack mb-4 grid gap-2" },
            messages.map((message, index) =>
                h(
                    "div",
                    {
                        key: index,
                        className:
                            "tc-flash rounded-md border border-brand-orange/25 bg-brand-orangeSoft px-4 py-3 text-sm font-semibold text-slate-800 shadow-sm",
                    },
                    message,
                ),
            ),
        );
    }

    function PageHeader({ actions, logoUrl, meta, subtitle, title }) {
        return h(
            "header",
            {
                className:
                    "tc-page-header border-b shadow-sm",
            },
            h(
                "div",
                { className: "tc-header-ambient", "aria-hidden": "true" },
                h("span", { className: "tc-header-squares" }),
                h("span", { className: "tc-header-square tc-header-square--one" }),
                h("span", { className: "tc-header-square tc-header-square--two" }),
                h("span", { className: "tc-header-square tc-header-square--three" }),
                h("span", { className: "tc-header-scanline" }),
            ),
            h(
                "div",
                {
                    className:
                        "tc-page-header__inner mx-auto flex w-[min(1800px,calc(100%-32px))] flex-col gap-4 py-4 md:flex-row md:items-center md:justify-between",
                },
                h(
                    "div",
                    { className: "tc-page-header__brand flex min-w-0 items-center gap-4" },
                    h(
                        "div",
                        {
                            className:
                                "tc-logo-frame grid h-14 w-14 shrink-0 place-items-center overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm",
                        },
                        h("img", {
                            src: logoUrl,
                            alt: "Tutoring Club Logo",
                            className: "h-11 w-11 object-contain",
                        }),
                    ),
                    h(
                        "div",
                        { className: "min-w-0" },
                        h(
                            "h1",
                            {
                                className:
                                    "tc-page-title truncate text-2xl font-extrabold tracking-normal text-brand-blueDark md:text-3xl",
                            },
                            title,
                        ),
                        subtitle
                            ? h(
                                  "p",
                                  {
                                      className:
                                          "tc-page-subtitle mt-1 text-sm font-medium text-slate-500",
                                  },
                                  subtitle,
                              )
                            : null,
                    ),
                ),
                h(
                    "div",
                    {
                        className:
                            "tc-page-header__meta flex flex-col gap-3 text-left md:items-end md:text-right",
                    },
                    meta
                        ? h(
                              "div",
                              {
                                  className:
                                      "tc-page-meta text-sm font-medium leading-6 text-slate-500",
                              },
                              meta,
                          )
                        : null,
                    actions
                        ? h(
                              "div",
                              {
                                  className:
                                      "tc-header-actions flex flex-wrap items-center gap-2 md:justify-end",
                              },
                              actions,
                          )
                        : null,
                ),
            ),
        );
    }

    function Shell({ children }) {
        return h(
            "div",
            { className: "tc-shell" },
            children,
        );
    }

    function PageMain({ children, wide }) {
        return h(
            "main",
            {
                className: cn(
                    "tc-page-main",
                    wide && "tc-page-main--wide",
                    "mx-auto my-6 w-[min(1800px,calc(100%-32px))] pb-10",
                    wide && "w-[min(1900px,calc(100%-32px))]",
                ),
            },
            children,
        );
    }

    function useJobStatus(jobId) {
        const [state, setState] = useState(null);

        useEffect(() => {
            if (!jobId) {
                setState(null);
                return undefined;
            }

            let active = true;
            let timerId = null;

            async function poll() {
                try {
                    const response = await fetch(`/status/${jobId}`);
                    if (!response.ok) {
                        if (active) {
                            setState(null);
                        }
                        return;
                    }

                    const data = await response.json();
                    if (!active) {
                        return;
                    }

                    if (data.step >= 0 && data.step < data.steps) {
                        setState(data);
                        timerId = window.setTimeout(poll, 2000);
                    } else if (data.step === data.steps) {
                        setState(data);
                        window.setTimeout(() => window.location.reload(), 1800);
                    } else {
                        setState(null);
                    }
                } catch (error) {
                    if (active) {
                        setState(null);
                    }
                }
            }

            poll();

            return () => {
                active = false;
                if (timerId) {
                    window.clearTimeout(timerId);
                }
            };
        }, [jobId]);

        return state;
    }

    function ProgressPanel({ jobId, label }) {
        const state = useJobStatus(jobId);
        if (!state) {
            return null;
        }
        const pct = Math.max(0, Math.min(100, Math.round((state.pct || 0) * 100)));
        const complete = state.step === state.steps;
        return h(
            Card,
            { className: "tc-progress-card p-4" },
            h(
                "div",
                { className: "mb-2 flex items-center justify-between gap-3" },
                h(
                    "div",
                    {
                        className:
                            "flex items-center gap-2 text-sm font-bold text-brand-orangeDark",
                    },
                    h(Icon, { name: "refresh", className: "h-4 w-4" }),
                    complete
                        ? `${label} complete. Reloading...`
                        : `${label}: ${state.step} / ${state.total + 2}`,
                ),
                h(
                    "span",
                    { className: "text-xs font-bold text-slate-500" },
                    `${pct}%`,
                ),
            ),
            h(
                "div",
                {
                    className:
                        "tc-progress-track h-2.5 overflow-hidden rounded-full bg-slate-100",
                },
                h("div", {
                    className:
                        "tc-progress-fill h-full rounded-full bg-brand-orange transition-all",
                    style: { width: `${pct}%` },
                }),
            ),
        );
    }

    function gradeToneClass(grade) {
        if (typeof grade !== "number") {
            return "text-slate-500";
        }
        if (grade < 70) {
            return "text-red-600";
        }
        if (grade < 80) {
            return "text-brand-orangeDark";
        }
        if (grade < 90) {
            return "text-amber-600";
        }
        return "text-emerald-700";
    }

    function formatGrade(grade) {
        if (typeof grade !== "number") {
            return "";
        }
        return Number.isInteger(grade) ? String(grade) : grade.toFixed(1);
    }

    function ChangeIcon({ change }) {
        if (change === "+") {
            return h(Icon, { name: "up", className: "h-3.5 w-3.5 text-emerald-600" });
        }
        if (change === "-") {
            return h(Icon, { name: "down", className: "h-3.5 w-3.5 text-red-600" });
        }
        return h(Icon, { name: "minus", className: "h-3.5 w-3.5 text-slate-400" });
    }

    function GradeList({ grades, compact }) {
        if (!grades || grades.length === 0) {
            return h(
                "p",
                { className: "text-sm font-medium text-slate-500" },
                "No grades available.",
            );
        }
        return h(
            "ul",
            { className: cn("grid gap-2", compact && "gap-1.5") },
            grades.map((grade, index) =>
                h(
                    "li",
                    {
                        key: `${grade.course}-${index}`,
                        className:
                            "flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm leading-5 text-slate-800",
                    },
                    h(
                        "span",
                        { className: "font-semibold" },
                        grade.course,
                        ":",
                    ),
                    h(
                        "span",
                        {
                            className: cn(
                                "inline-flex items-center gap-1 font-extrabold",
                                gradeToneClass(grade.grade),
                            ),
                        },
                        formatGrade(grade.grade),
                        "change" in grade ? h(ChangeIcon, { change: grade.change }) : null,
                    ),
                ),
            ),
        );
    }

    function standingTone(standing) {
        if (standing === "Good") {
            return "success";
        }
        if (standing === "Fair") {
            return "warning";
        }
        if (standing === "Poor") {
            return "danger";
        }
        return "slate";
    }

    function StatusBadge({ status }) {
        const normalized = String(status || "").toLowerCase();
        const tone =
            normalized === "synced"
                ? "success"
                : normalized === "never"
                  ? "slate"
                  : "warning";
        return h(Badge, { tone }, status || "Unknown");
    }

    function StatCard({ icon, label, note, tone, value }) {
        const iconTone = {
            blue: "bg-brand-blueSoft text-brand-blueDark",
            orange: "bg-brand-orangeSoft text-brand-orangeDark",
            green: "bg-emerald-50 text-emerald-700",
            red: "bg-red-50 text-red-700",
        }[tone || "blue"];
        return h(
            Card,
            { className: "tc-stat-card flex min-h-28 justify-between gap-4 p-5" },
            h(
                "div",
                null,
                h(
                    "div",
                    {
                        className:
                            "tc-stat-card__label text-xs font-extrabold uppercase tracking-normal text-slate-500",
                    },
                    label,
                ),
                h(
                    "div",
                    {
                        className:
                            "tc-stat-card__value mt-2 text-2xl font-extrabold tracking-normal text-brand-blueDark",
                    },
                    value,
                ),
                note
                    ? h(
                          "div",
                          { className: "tc-stat-card__note mt-2 text-sm font-medium text-slate-500" },
                          note,
                      )
                    : null,
            ),
            h(
                "div",
                {
                    className: cn(
                        "tc-stat-card__icon grid h-11 w-11 shrink-0 place-items-center rounded-lg",
                        iconTone,
                    ),
                },
                h(Icon, { name: icon || "activity" }),
            ),
        );
    }

    function LoginPage({ data }) {
        return h(
            Shell,
            null,
            h(
                "main",
                {
                    className:
                        "tc-login-page grid min-h-screen place-items-center px-4 py-10",
                },
                h(
                    "div",
                    { className: "tc-login-ambient", "aria-hidden": "true" },
                    h("span", { className: "tc-login-aurora tc-login-aurora--one" }),
                    h("span", { className: "tc-login-aurora tc-login-aurora--two" }),
                    h("span", { className: "tc-login-aurora tc-login-aurora--three" }),
                    h("span", { className: "tc-login-aurora tc-login-aurora--four" }),
                    h("span", { className: "tc-login-sheen" }),
                ),
                h(
                    "section",
                    {
                        className:
                            "tc-login-card w-full max-w-md rounded-lg border border-slate-200 bg-white p-6 shadow-accent",
                    },
                    h(
                        "div",
                        { className: "tc-login-brand mb-6 flex items-center gap-4" },
                        h(
                            "div",
                            {
                                className:
                                    "tc-login-logo-frame grid h-14 w-14 place-items-center rounded-lg border border-slate-200 bg-white shadow-sm",
                            },
                            h("img", {
                                src: data.logoUrl,
                                alt: "Tutoring Club Logo",
                                className: "h-11 w-11 object-contain",
                            }),
                        ),
                        h(
                            "div",
                            null,
                            h(
                                "h1",
                                {
                                    className:
                                        "text-2xl font-extrabold tracking-normal text-brand-blueDark",
                                },
                                "CRM Login",
                            ),
                            h(
                                "p",
                                { className: "mt-1 text-sm font-medium text-slate-500" },
                                "Tutoring Club Dashboard",
                            ),
                        ),
                    ),
                    h(FlashMessages, { messages: data.messages }),
                    h(
                        "form",
                        { method: "POST", className: "grid gap-4" },
                        h(HiddenCsrf, { token: data.csrfToken }),
                        h(
                            Field,
                            { label: "Username" },
                            h(Input, {
                                id: "username",
                                name: "username",
                                type: "text",
                                autoComplete: "username",
                                required: true,
                            }),
                        ),
                        h(
                            Field,
                            { label: "Password" },
                            h(Input, {
                                id: "password",
                                name: "password",
                                type: "password",
                                autoComplete: "current-password",
                                required: true,
                            }),
                        ),
                        h(
                            Button,
                            {
                                type: "submit",
                                variant: "orange",
                                size: "lg",
                                className: "mt-1 w-full",
                            },
                            "Sign in",
                        ),
                    ),
                ),
            ),
        );
    }

    function LogoutForm({ csrfToken, logoutUrl }) {
        if (!logoutUrl) {
            return null;
        }
        return h(
            "form",
            { method: "POST", action: logoutUrl },
            h(HiddenCsrf, { token: csrfToken }),
            h(
                Button,
                { type: "submit", variant: "outline", icon: "logOut" },
                "Logout",
            ),
        );
    }

    function HealthPage({ data }) {
        const health = data.health || [];
        const jobs = data.jobs || [];
        return h(
            Shell,
            null,
            h(PageHeader, {
                logoUrl: data.logoUrl,
                title: "Tutoring Club Scraper Health",
                subtitle: "Internal scraper and portal status",
                meta: h(
                    "div",
                    null,
                    h("strong", { className: "block text-brand-orangeDark" }, "Health Check"),
                    `Active students: ${data.countAll || 0} / Synced: ${data.countSynced || 0} / Bad logins: ${data.countBadLogins || 0}`,
                ),
                actions: h(LogoutForm, {
                    csrfToken: data.csrfToken,
                    logoutUrl: data.logoutUrl,
                }),
            }),
            h(
                PageMain,
                null,
                h(
                    "details",
                    {
                        className:
                            "mb-5 rounded-lg border border-slate-200 bg-white shadow-panel",
                    },
                    h(
                        "summary",
                        {
                            className:
                                "flex cursor-pointer list-none items-center justify-between gap-4 rounded-lg bg-brand-orange px-5 py-4 text-white",
                        },
                        h(
                            "span",
                            { className: "flex items-center gap-2 font-extrabold" },
                            h(Icon, { name: "activity" }),
                            "Active Jobs",
                        ),
                        h(Badge, { tone: "warning", className: "bg-white text-brand-orangeDark" }, jobs.length ? `${jobs.length} running` : "None"),
                    ),
                    h(
                        "div",
                        { className: "grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-3" },
                        jobs.length
                            ? jobs.map((job) =>
                                  h(
                                      "div",
                                      {
                                          key: job.id,
                                          className:
                                              "rounded-lg border border-slate-200 bg-slate-50 p-4",
                                      },
                                      h(
                                          "div",
                                          {
                                              className:
                                                  "mb-1 font-extrabold text-brand-orangeDark",
                                          },
                                          `Job ${job.id}`,
                                      ),
                                      h(
                                          "div",
                                          { className: "text-sm font-medium text-slate-500" },
                                          `Step ${job.step} / ${job.steps} / Total ${job.total}`,
                                      ),
                                  ),
                              )
                            : h(
                                  "p",
                                  { className: "text-sm font-medium text-slate-500" },
                                  "No active jobs.",
                              ),
                    ),
                ),
                h(
                    "section",
                    { className: "mb-6 grid gap-4 md:grid-cols-3" },
                    h(StatCard, {
                        icon: "user",
                        label: "Active Students",
                        value: data.countAll || 0,
                        note: "Tracked students",
                        tone: "blue",
                    }),
                    h(StatCard, {
                        icon: "check",
                        label: "Synced Students",
                        value: data.countSynced || 0,
                        note: "Current portal data",
                        tone: "green",
                    }),
                    h(StatCard, {
                        icon: "alert",
                        label: "Bad Logins",
                        value: data.countBadLogins || 0,
                        note: "Needs attention",
                        tone: "orange",
                    }),
                ),
                h(
                    "div",
                    { className: "mb-4 flex items-center justify-between gap-4" },
                    h(
                        "h2",
                        {
                            className:
                                "text-xl font-extrabold tracking-normal text-brand-blueDark",
                        },
                        "Franchise Health",
                    ),
                    h(Badge, { tone: "neutral" }, `${health.length} franchises`),
                ),
                h(
                    "section",
                    {
                        className:
                            "grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4",
                    },
                    health.map((item) =>
                        h(HealthFranchiseCard, { item, key: item.id }),
                    ),
                ),
            ),
        );
    }

    function HealthFranchiseCard({ item }) {
        const errorGroups = item.errorGroups || [];
        const rows = [
            ["Synced students", `${item.synced || 0} / ${item.total || 0}`, "neutral"],
            ["Malformed inputs", item.malformedInputs || 0, item.malformedInputs ? "warning" : "neutral"],
            ["Nonconfigured portals", item.nonconfiguredPortals || 0, item.nonconfiguredPortals ? "warning" : "neutral"],
            ["Bad logins", item.badLogins || 0, item.badLogins ? "warning" : "neutral"],
            ["Last updated", item.lastUpdated || "n/a", "slate"],
            ["Errors", item.errorCount ? `${item.errorCount} active` : "None", item.errorCount ? "danger" : "success"],
        ];
        return h(
            "article",
            {
                className:
                    "relative overflow-hidden rounded-lg border border-slate-200 bg-white shadow-panel transition hover:-translate-y-0.5 hover:border-brand-orange/50 hover:shadow-accent",
            },
            h("div", { className: "absolute inset-y-0 left-0 w-1.5 bg-brand-orange" }),
            h(
                "div",
                { className: "p-5 pl-6" },
                h(
                    "div",
                    { className: "mb-4 flex items-start justify-between gap-3" },
                    h(
                        "div",
                        null,
                        h(
                            "h3",
                            {
                                className:
                                    "text-lg font-extrabold tracking-normal text-brand-blueDark",
                            },
                            `Franchise ${item.id}`,
                        ),
                        h(
                            "p",
                            { className: "text-sm font-medium text-slate-500" },
                            "Student sync and portal status",
                        ),
                    ),
                    h(
                        "div",
                        {
                            className:
                                "grid h-10 w-10 place-items-center rounded-lg bg-brand-orangeSoft text-sm font-extrabold text-brand-orangeDark",
                        },
                        "TC",
                    ),
                ),
                h(
                    "div",
                    { className: "grid gap-2" },
                    rows.map(([label, value, tone]) =>
                        h(
                            "div",
                            {
                                key: label,
                                className:
                                    "flex items-center justify-between gap-4 border-t border-slate-100 pt-2 text-sm",
                            },
                            h("span", { className: "font-medium text-slate-500" }, label),
                            h(Badge, { tone }, value),
                        ),
                    ),
                ),
                errorGroups.length
                    ? h(
                          "div",
                          { className: "mt-4 grid gap-2 border-t border-slate-100 pt-3" },
                          errorGroups.slice(0, 3).map((group) =>
                              h(
                                  "div",
                                  {
                                      key: group.label,
                                      className:
                                          "flex items-start gap-2 rounded-md bg-red-50 px-3 py-2 text-xs font-semibold text-red-800",
                                  },
                                  h(Badge, { tone: "danger" }, `${group.count}x`),
                                  h("span", { className: "leading-5" }, group.label),
                              ),
                          ),
                          errorGroups.length > 3
                              ? h(
                                    "div",
                                    { className: "text-xs font-bold text-slate-500" },
                                    `${errorGroups.length - 3} more grouped errors`,
                                )
                              : null,
                      )
                    : null,
                h(
                    "div",
                    { className: "mt-5" },
                    h(Button, {
                        href: item.url,
                        variant: "outline",
                        icon: "external",
                        className: "w-full",
                    }, "Open Franchise"),
                ),
            ),
        );
    }

    function appendQuery(url, key, value) {
        const separator = url.includes("?") ? "&" : "?";
        return `${url}${separator}${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
    }

    function MasterPasswordDialog({ onCancel, onSubmit, open }) {
        const [value, setValue] = useState("");

        useEffect(() => {
            if (open) {
                setValue("");
            }
        }, [open]);

        if (!open) {
            return null;
        }

        return h(
            "div",
            {
                className:
                    "tc-modal-backdrop fixed inset-0 z-50 grid place-items-center p-4",
                role: "dialog",
                "aria-modal": "true",
            },
            h(
                "form",
                {
                    className:
                        "w-full max-w-md rounded-lg border border-slate-200 bg-white p-5 shadow-accent",
                    onSubmit: (event) => {
                        event.preventDefault();
                        onSubmit(value);
                    },
                },
                h(
                    "div",
                    { className: "mb-4 flex items-start justify-between gap-4" },
                    h(
                        "div",
                        null,
                        h(
                            "h2",
                            {
                                className:
                                    "text-lg font-extrabold tracking-normal text-brand-blueDark",
                            },
                            "Master Password Required",
                        ),
                        h(
                            "p",
                            { className: "mt-1 text-sm font-medium text-slate-500" },
                            "Enter the master password to continue.",
                        ),
                    ),
                    h(Button, {
                        variant: "outline",
                        size: "sm",
                        icon: "x",
                        onClick: onCancel,
                    }, "Close"),
                ),
                h(
                    Field,
                    { label: "Master password" },
                    h(Input, {
                        type: "password",
                        value,
                        autoFocus: true,
                        onChange: (event) => setValue(event.target.value),
                        required: true,
                    }),
                ),
                h(
                    "div",
                    { className: "mt-5 flex justify-end gap-2" },
                    h(Button, { variant: "outline", onClick: onCancel }, "Cancel"),
                    h(Button, { type: "submit", variant: "orange", icon: "key" }, "Submit"),
                ),
            ),
        );
    }

    function StudentDialog({
        csrfToken,
        dekExists,
        franchiseUrl,
        masterPassword,
        mode,
        onClose,
        onNeedPassword,
        open,
        student,
    }) {
        const emptyValues = {
            first_name: "",
            last_name: "",
            grade: "",
            portal_url: "",
            portal_username: "",
            portal_password: "",
            alt_portal_url: "",
            alt_portal_username: "",
            alt_portal_password: "",
        };
        const [values, setValues] = useState(emptyValues);

        useEffect(() => {
            if (!open) {
                return;
            }
            if (mode === "edit" && student) {
                setValues({
                    first_name: student.firstName || "",
                    last_name: student.lastName || "",
                    grade: student.gradeLevel || "",
                    portal_url: student.portalUrl || "",
                    portal_username: "",
                    portal_password: "",
                    alt_portal_url: student.altPortalUrl || "",
                    alt_portal_username: "",
                    alt_portal_password: "",
                });
            } else {
                setValues(emptyValues);
            }
        }, [mode, open, student]);

        if (!open) {
            return null;
        }

        const isEdit = mode === "edit";
        const formAction =
            isEdit && student
                ? appendQuery(franchiseUrl, "student_id", student.id)
                : franchiseUrl;

        function update(name, value) {
            setValues((current) => Object.assign({}, current, { [name]: value }));
        }

        function handleSubmit(event) {
            if (!dekExists && !masterPassword) {
                event.preventDefault();
                onNeedPassword();
            }
        }

        return h(
            "div",
            {
                className:
                    "tc-modal-backdrop fixed inset-0 z-40 overflow-y-auto p-4",
                role: "dialog",
                "aria-modal": "true",
            },
            h(
                "div",
                {
                    className:
                        "mx-auto my-8 w-full max-w-3xl rounded-lg border border-slate-200 bg-white p-5 shadow-accent",
                },
                h(
                    "div",
                    { className: "mb-4 flex items-start justify-between gap-4" },
                    h(
                        "div",
                        null,
                        h(
                            "h2",
                            {
                                className:
                                    "text-xl font-extrabold tracking-normal text-brand-blueDark",
                            },
                            isEdit ? "Edit Student" : "Add Student",
                        ),
                        h(
                            "p",
                            { className: "mt-1 text-sm font-medium text-slate-500" },
                            isEdit
                                ? "Leave credential fields blank to keep current values."
                                : "Create the student profile and connected portal.",
                        ),
                    ),
                    h(Button, {
                        variant: "outline",
                        size: "sm",
                        icon: "x",
                        onClick: onClose,
                    }, "Close"),
                ),
                h(
                    "form",
                    {
                        method: "POST",
                        action: formAction,
                        className: "grid gap-4",
                        onSubmit: handleSubmit,
                    },
                    h(HiddenCsrf, { token: csrfToken }),
                    h(HiddenMasterPassword, { value: masterPassword }),
                    h(
                        "div",
                        { className: "grid gap-4 md:grid-cols-2" },
                        h(Field, { label: "First name" }, h(Input, {
                            name: "first_name",
                            value: values.first_name,
                            onChange: (event) => update("first_name", event.target.value),
                            required: true,
                        })),
                        h(Field, { label: "Last name" }, h(Input, {
                            name: "last_name",
                            value: values.last_name,
                            onChange: (event) => update("last_name", event.target.value),
                            required: true,
                        })),
                        h(Field, { label: "Grade" }, h(Input, {
                            name: "grade",
                            type: "number",
                            value: values.grade,
                            onChange: (event) => update("grade", event.target.value),
                            required: true,
                        })),
                        h(Field, { label: "Portal URL" }, h(Input, {
                            name: "portal_url",
                            type: "url",
                            value: values.portal_url,
                            onChange: (event) => update("portal_url", event.target.value),
                            required: true,
                        })),
                        h(Field, { label: "Portal username" }, h(Input, {
                            name: "portal_username",
                            value: values.portal_username,
                            placeholder: isEdit ? "Leave blank to keep current" : "",
                            onChange: (event) => update("portal_username", event.target.value),
                            required: !isEdit,
                        })),
                        h(Field, { label: "Portal password" }, h(Input, {
                            name: "portal_password",
                            type: "password",
                            value: values.portal_password,
                            placeholder: isEdit ? "Leave blank to keep current" : "",
                            onChange: (event) => update("portal_password", event.target.value),
                            required: !isEdit,
                        })),
                        h(Field, { label: "Alt portal URL", optional: true }, h(Input, {
                            name: "alt_portal_url",
                            type: "url",
                            value: values.alt_portal_url,
                            onChange: (event) => update("alt_portal_url", event.target.value),
                        })),
                        h(Field, { label: "Alt portal username", optional: true }, h(Input, {
                            name: "alt_portal_username",
                            value: values.alt_portal_username,
                            placeholder: isEdit ? "Leave blank to keep current" : "",
                            onChange: (event) => update("alt_portal_username", event.target.value),
                        })),
                        h(Field, { label: "Alt portal password", optional: true }, h(Input, {
                            name: "alt_portal_password",
                            type: "password",
                            value: values.alt_portal_password,
                            placeholder: isEdit ? "Leave blank to keep current" : "",
                            onChange: (event) => update("alt_portal_password", event.target.value),
                        })),
                    ),
                    h(
                        "div",
                        { className: "flex justify-end gap-2 pt-2" },
                        h(Button, { variant: "outline", onClick: onClose }, "Cancel"),
                        h(
                            Button,
                            {
                                type: "submit",
                                variant: "orange",
                                icon: isEdit ? "check" : "plus",
                                name: isEdit ? "edit_student" : "add_student",
                                value: "1",
                            },
                            isEdit ? "Update Student" : "Add Student",
                        ),
                    ),
                ),
            ),
        );
    }

    function FranchisePage({ data }) {
        const students = data.students || [];
        const [search, setSearch] = useState("");
        const [sort, setSort] = useState({ field: "name", dir: "asc" });
        const [deleteMode, setDeleteMode] = useState(false);
        const [selected, setSelected] = useState(() => new Set());
        const [dialogMode, setDialogMode] = useState("add");
        const [dialogStudent, setDialogStudent] = useState(null);
        const [studentDialogOpen, setStudentDialogOpen] = useState(false);
        const [passwordDialogOpen, setPasswordDialogOpen] = useState(false);
        const [dekExists, setDekExists] = useState(Boolean(data.dekExists));
        const [masterPassword, setMasterPassword] = useState("");
        const pendingAction = useRef(null);

        const filteredStudents = useMemo(() => {
            const query = search.trim().toLowerCase();
            const visible = students.filter((student) => {
                const fullName = `${student.firstName || ""} ${student.lastName || ""}`.toLowerCase();
                return !query || fullName.includes(query);
            });

            const standingOrder = { Poor: 0, Fair: 1, Good: 2 };
            return visible.slice().sort((a, b) => {
                const aNever = String(a.status || "").toLowerCase() === "never";
                const bNever = String(b.status || "").toLowerCase() === "never";
                if (aNever !== bNever) {
                    return aNever ? 1 : -1;
                }

                let result = 0;
                if (sort.field === "standing") {
                    const aVal = standingOrder[a.standing] ?? 99;
                    const bVal = standingOrder[b.standing] ?? 99;
                    result = aVal - bVal;
                } else {
                    const aVal = `${a.firstName || ""} ${a.lastName || ""}`.toLowerCase();
                    const bVal = `${b.firstName || ""} ${b.lastName || ""}`.toLowerCase();
                    result = aVal.localeCompare(bVal);
                }
                return sort.dir === "asc" ? result : -result;
            });
        }, [search, sort, students]);

        function toggleSort(field) {
            setSort((current) => ({
                field,
                dir: current.field === field && current.dir === "asc" ? "desc" : "asc",
            }));
        }

        function requireMaster(action) {
            if (dekExists || masterPassword) {
                action();
                return;
            }
            pendingAction.current = action;
            setPasswordDialogOpen(true);
        }

        function handlePasswordSubmit(value) {
            setMasterPassword(value);
            setDekExists(true);
            setPasswordDialogOpen(false);
            const action = pendingAction.current;
            pendingAction.current = null;
            if (action) {
                window.setTimeout(action, 0);
            }
        }

        function openAddDialog() {
            requireMaster(() => {
                setDialogMode("add");
                setDialogStudent(null);
                setStudentDialogOpen(true);
            });
        }

        function openEditDialog(student) {
            requireMaster(() => {
                setDialogMode("edit");
                setDialogStudent(student);
                setStudentDialogOpen(true);
            });
        }

        function toggleSelected(id) {
            setSelected((current) => {
                const next = new Set(current);
                if (next.has(id)) {
                    next.delete(id);
                } else {
                    next.add(id);
                }
                return next;
            });
        }

        function startDeleteMode() {
            requireMaster(() => setDeleteMode(true));
        }

        function cancelDeleteMode() {
            setDeleteMode(false);
            setSelected(new Set());
        }

        function handleDeleteSubmit(event) {
            if (!selected.size) {
                event.preventDefault();
                window.alert("Select at least one student to delete.");
                return;
            }
            if (!window.confirm(`Delete ${selected.size} selected student(s)?`)) {
                event.preventDefault();
            }
        }

        const syncedCount = students.filter((student) => String(student.status || "").toLowerCase() === "synced").length;
        const attentionCount = students.length - syncedCount;
        const progressPanels = h(
            "div",
            { className: "mb-4 grid gap-3" },
            h(ProgressPanel, {
                jobId: data.jobId,
                label: "Grade collection in progress",
            }),
            h(ProgressPanel, {
                jobId: data.agendaJobId,
                label: "Agenda refresh in progress",
            }),
        );

        return h(
            Shell,
            null,
            h(PageHeader, {
                logoUrl: data.logoUrl,
                title: `Franchise ${data.franchiseId}`,
                subtitle: "Student grades, standings, and portal status",
                meta: h(
                    "div",
                    null,
                    h("strong", { className: "block text-brand-orangeDark" }, "Student Dashboard"),
                    `${students.length} students / ${syncedCount} synced / ${attentionCount} to review`,
                ),
                actions: h(LogoutForm, {
                    csrfToken: data.csrfToken,
                    logoutUrl: data.logoutUrl,
                }),
            }),
            h(
                PageMain,
                { wide: true },
                h(FlashMessages, { messages: data.messages }),
                h(
                    "section",
                    { className: "mb-4 grid gap-4 md:grid-cols-3" },
                    h(StatCard, {
                        icon: "user",
                        label: "Students",
                        value: students.length,
                        note: "Visible in this filter",
                        tone: "blue",
                    }),
                    h(StatCard, {
                        icon: "check",
                        label: "Synced",
                        value: syncedCount,
                        note: "Current portal data",
                        tone: "green",
                    }),
                    h(StatCard, {
                        icon: "alert",
                        label: "Needs Review",
                        value: attentionCount,
                        note: "Not currently synced",
                        tone: "orange",
                    }),
                ),
                h(
                    Card,
                    { className: "mb-4 p-4" },
                    h(
                        "div",
                        {
                            className:
                                "flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between",
                        },
                        h(
                            "div",
                            { className: "flex flex-wrap items-center gap-2" },
                            h(Button, {
                                variant: "orange",
                                icon: "plus",
                                onClick: openAddDialog,
                            }, "Add Student"),
                            deleteMode
                                ? [
                                      h(Button, {
                                          key: "confirm",
                                          type: "submit",
                                          form: "delete-students-form",
                                          name: "delete_students",
                                          value: "1",
                                          variant: "danger",
                                          icon: "trash",
                                      }, `Delete ${selected.size || ""}`.trim()),
                                      h(Button, {
                                          key: "cancel",
                                          variant: "outline",
                                          onClick: cancelDeleteMode,
                                      }, "Cancel"),
                                  ]
                                : h(Button, {
                                      variant: "outline",
                                      icon: "trash",
                                      onClick: startDeleteMode,
                                  }, "Delete Students"),
                            h(
                                "form",
                                {
                                    method: "POST",
                                    action: data.franchiseUrl,
                                    onSubmit: (event) => {
                                        if (!window.confirm("This will begin a long job and may take a few minutes. Continue?")) {
                                            event.preventDefault();
                                        }
                                    },
                                },
                                h(HiddenCsrf, { token: data.csrfToken }),
                                h(Button, {
                                    type: "submit",
                                    name: "run_agenda",
                                    value: "1",
                                    variant: "primary",
                                    icon: "refresh",
                                }, "Refresh Agendas"),
                            ),
                            h(
                                "form",
                                {
                                    method: "POST",
                                    action: data.franchiseUrl,
                                    onSubmit: (event) => {
                                        if (!window.confirm("This will begin a long job and may take a few minutes. Continue?")) {
                                            event.preventDefault();
                                        }
                                    },
                                },
                                h(HiddenCsrf, { token: data.csrfToken }),
                                h(Button, {
                                    type: "submit",
                                    name: "run_scraper",
                                    value: "1",
                                    variant: "gold",
                                    icon: "refresh",
                                }, "Check Grades"),
                            ),
                        ),
                        h(
                            "div",
                            {
                                className:
                                    "flex flex-col gap-3 sm:flex-row sm:items-center xl:justify-end",
                            },
                            h(
                                "label",
                                {
                                    className:
                                        "tc-search-field relative block min-w-[min(100%,22rem)]",
                                },
                                h(
                                    "span",
                                    { className: "tc-search-field__label" },
                                    "Search students",
                                ),
                                h(Icon, {
                                    name: "search",
                                    className:
                                        "tc-search-field__icon pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400",
                                }),
                                h(Input, {
                                    value: search,
                                    onChange: (event) => setSearch(event.target.value),
                                    placeholder: "Type a student name",
                                    className: "pl-9",
                                }),
                            ),
                            h(
                                "div",
                                {
                                    className:
                                        "tc-filter-control inline-flex flex-wrap gap-1 rounded-md border border-slate-200 bg-slate-50 p-1",
                                },
                                (data.filters || []).map((filter) =>
                                    h(
                                        "a",
                                        {
                                            key: filter.value,
                                            href: filter.url,
                                            className: cn(
                                                "tc-filter-option",
                                                filter.value === data.gradeFilter && "tc-filter-option--active",
                                                "tc-focus-ring rounded px-3 py-2 text-xs font-extrabold text-brand-blueDark transition hover:bg-white",
                                                filter.value === data.gradeFilter &&
                                                    "bg-brand-blue text-white hover:bg-brand-blue",
                                            ),
                                        },
                                        filter.label,
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
                progressPanels,
                h(
                    Card,
                    { className: "overflow-hidden" },
                    h(
                        "div",
                        {
                            className:
                                "flex flex-col gap-1 bg-brand-orange px-5 py-4 text-white sm:flex-row sm:items-center sm:justify-between",
                        },
                        h(
                            "h2",
                            { className: "text-base font-extrabold" },
                            "Students",
                        ),
                        h(
                            "span",
                            { className: "text-sm font-bold text-white/90" },
                            `${filteredStudents.length} shown`,
                        ),
                    ),
                    h(
                        "form",
                        {
                            id: "delete-students-form",
                            method: "POST",
                            action: data.franchiseUrl,
                            onSubmit: handleDeleteSubmit,
                        },
                        h(HiddenCsrf, { token: data.csrfToken }),
                        h(HiddenMasterPassword, { value: masterPassword }),
                        h(StudentTable, {
                            deleteMode,
                            onEdit: openEditDialog,
                            onRowSelect: toggleSelected,
                            onSort: toggleSort,
                            selected,
                            sort,
                            students: filteredStudents,
                        }),
                    ),
                ),
                h(StudentDialog, {
                    csrfToken: data.csrfToken,
                    dekExists,
                    franchiseUrl: data.franchiseUrl,
                    masterPassword,
                    mode: dialogMode,
                    onClose: () => setStudentDialogOpen(false),
                    onNeedPassword: () => setPasswordDialogOpen(true),
                    open: studentDialogOpen,
                    student: dialogStudent,
                }),
                h(MasterPasswordDialog, {
                    open: passwordDialogOpen,
                    onCancel: () => {
                        pendingAction.current = null;
                        setPasswordDialogOpen(false);
                    },
                    onSubmit: handlePasswordSubmit,
                }),
            ),
        );
    }

    function SortHeader({ field, label, onSort, sort }) {
        const active = sort.field === field;
        const suffix = active ? (sort.dir === "asc" ? "A-Z" : "Z-A") : "Sort";
        return h(
            "th",
            { className: "bg-brand-blue px-4 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white" },
            h(
                "button",
                {
                    type: "button",
                    className: "flex items-center gap-2 text-left",
                    onClick: () => onSort(field),
                },
                label,
                h("span", { className: "text-[10px] font-bold text-white/75" }, suffix),
            ),
        );
    }

    function studentTabFromHash(hash) {
        const tab = String(hash || "").replace(/^#/, "").toLowerCase();
        return tab === "heatmap" ? "heatmap" : "history";
    }

    function studentTabHash(tab) {
        return tab === "heatmap" ? "#heatmap" : "#history";
    }

    function StudentTable({
        deleteMode,
        onEdit,
        onRowSelect,
        onSort,
        selected,
        sort,
        students,
    }) {
        return h(
            "div",
            { className: "tc-table-scroll" },
            h(
                "table",
                { className: "tc-data-table min-w-[1180px] w-full border-collapse" },
                h(
                    "thead",
                    null,
                    h(
                        "tr",
                        null,
                        deleteMode
                            ? h("th", {
                                  className:
                                      "w-12 bg-brand-blue px-4 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white",
                              }, "Pick")
                            : null,
                        h(SortHeader, { field: "name", label: "Student", onSort, sort }),
                        h("th", { className: "bg-brand-blue px-4 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white" }, "Portal"),
                        h("th", { className: "bg-brand-blue px-4 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white" }, "Recent Grades"),
                        h("th", { className: "bg-brand-blue px-4 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white" }, "Low Grades"),
                        h("th", { className: "bg-brand-blue px-4 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white" }, "High Grades"),
                        h(SortHeader, { field: "standing", label: "Standing", onSort, sort }),
                        h("th", { className: "bg-brand-blue px-4 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white" }, "Status"),
                        h("th", { className: "bg-brand-blue px-4 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white" }, "Actions"),
                    ),
                ),
                h(
                    "tbody",
                    null,
                    students.map((student) =>
                        h(
                            "tr",
                            {
                                key: student.id,
                                className:
                                    "cursor-pointer border-b border-slate-100 transition hover:bg-brand-orangeSoft/40",
                                onClick: () => {
                                    if (deleteMode) {
                                        onRowSelect(student.id);
                                    } else {
                                        window.location.href = student.detailUrl;
                                    }
                                },
                            },
                            deleteMode
                                ? h(
                                      "td",
                                      { className: "px-4 py-4 align-top" },
                                      h("input", {
                                          type: "checkbox",
                                          name: "student_id",
                                          value: student.id,
                                          checked: selected.has(student.id),
                                          "aria-label": `Select ${student.firstName || ""} ${student.lastName || ""}`.trim(),
                                          onChange: () => onRowSelect(student.id),
                                          onClick: (event) => event.stopPropagation(),
                                          className:
                                              "h-4 w-4 rounded border-slate-300 text-brand-blue",
                                      }),
                                  )
                                : null,
                            h(
                                "td",
                                { className: "px-4 py-4 align-top" },
                                h(
                                    "div",
                                    {
                                        className:
                                            "font-extrabold text-brand-blueDark",
                                    },
                                    `${student.firstName || ""} ${student.lastName || ""}`,
                                ),
                                h(
                                    "div",
                                    { className: "mt-1 text-xs font-semibold text-slate-500" },
                                    `Grade ${student.gradeLevel || "n/a"}`,
                                ),
                            ),
                            h(
                                "td",
                                { className: "px-4 py-4 align-top" },
                                student.portalUrl
                                    ? h(
                                          "a",
                                          {
                                              href: student.portalUrl,
                                              target: "_blank",
                                              rel: "noreferrer",
                                              className:
                                                  "inline-flex items-center gap-1 font-bold text-brand-blue hover:text-brand-orangeDark",
                                              onClick: (event) => event.stopPropagation(),
                                          },
                                          student.portal || "Portal",
                                          h(Icon, { name: "external", className: "h-3.5 w-3.5" }),
                                      )
                                    : h("span", { className: "text-sm font-medium text-slate-500" }, student.portal || "N/A"),
                            ),
                            h("td", { className: "max-w-[260px] px-4 py-4 align-top" }, h(GradeList, { grades: student.gradesSnapshot, compact: true })),
                            h("td", { className: "max-w-[260px] px-4 py-4 align-top" }, h(GradeList, { grades: student.lowGrades, compact: true })),
                            h("td", { className: "max-w-[260px] px-4 py-4 align-top" }, h(GradeList, { grades: student.highGrades, compact: true })),
                            h(
                                "td",
                                { className: "px-4 py-4 align-top" },
                                h(Badge, { tone: standingTone(student.standing) }, student.standing || "Unknown"),
                            ),
                            h(
                                "td",
                                { className: "px-4 py-4 align-top" },
                                h(StatusBadge, { status: student.status }),
                            ),
                            h(
                                "td",
                                { className: "px-4 py-4 align-top" },
                                h(
                                    "div",
                                    { className: "flex flex-wrap gap-2" },
                                    h(Button, {
                                        variant: "outline",
                                        size: "sm",
                                        onClick: (event) => {
                                            event.stopPropagation();
                                            onEdit(student);
                                        },
                                    }, "Edit"),
                                    h(Button, {
                                        href: `${student.detailUrl}#heatmap`,
                                        variant: "orange",
                                        size: "sm",
                                        icon: "grid",
                                        onClick: (event) => event.stopPropagation(),
                                    }, "Heatmap"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        );
    }

    function StudentPage({ data }) {
        const student = data.student || {};
        const [activeTab, setActiveTab] = useState(() => studentTabFromHash(window.location.hash));
        const grades = student.grades || {};
        useEffect(() => {
            function handleHashChange() {
                setActiveTab(studentTabFromHash(window.location.hash));
            }

            window.addEventListener("hashchange", handleHashChange);
            return () => window.removeEventListener("hashchange", handleHashChange);
        }, []);

        function selectStudentTab(tab) {
            setActiveTab(tab);
            if (window.history && window.history.replaceState) {
                window.history.replaceState(null, "", studentTabHash(tab));
            } else {
                window.location.hash = studentTabHash(tab);
            }
        }

        return h(
            Shell,
            null,
            h(PageHeader, {
                logoUrl: data.logoUrl,
                title: `${student.firstName || ""} ${student.lastName || ""}`.trim() || "Student Report",
                subtitle: "Grade, agenda, and history report",
                meta: h(
                    "div",
                    null,
                    h("strong", { className: "block text-brand-orangeDark" }, "Student Report"),
                    `Status: ${student.status || "Unknown"} / Standing: ${student.standing || "Unknown"}`,
                ),
                actions: h(LogoutForm, {
                    csrfToken: data.csrfToken,
                    logoutUrl: data.logoutUrl,
                }),
            }),
            h(
                PageMain,
                { wide: true },
                h(
                    "div",
                    { className: "mb-4" },
                    h(Button, {
                        href: data.backUrl,
                        variant: "outline",
                        icon: "arrowLeft",
                    }, "Back to Student List"),
                ),
                h(FlashMessages, { messages: data.messages }),
                h(
                    "section",
                    { className: "mb-4 grid gap-4 md:grid-cols-3" },
                    h(StatCard, {
                        icon: "user",
                        label: "Student",
                        value: `${student.firstName || ""} ${student.lastName || ""}`.trim(),
                        note: `Grade ${student.gradeLevel || "n/a"}`,
                        tone: "blue",
                    }),
                    h(
                        Card,
                        { className: "flex min-h-28 justify-between gap-4 p-5" },
                        h(
                            "div",
                            { className: "min-w-0" },
                            h(
                                "div",
                                {
                                    className:
                                        "text-xs font-extrabold uppercase tracking-normal text-slate-500",
                                },
                                "Portal",
                            ),
                            student.portalUrl
                                ? h(
                                      "a",
                                      {
                                          href: student.portalUrl,
                                          target: "_blank",
                                          rel: "noreferrer",
                                          className:
                                              "mt-2 inline-flex max-w-full items-center gap-2 truncate text-xl font-extrabold text-brand-blueDark hover:text-brand-orangeDark",
                                      },
                                      student.portal || "Open Portal",
                                      h(Icon, { name: "external" }),
                                  )
                                : h(
                                      "div",
                                      {
                                          className:
                                              "mt-2 text-xl font-extrabold text-brand-blueDark",
                                      },
                                      student.portal || "N/A",
                                  ),
                            h(
                                "div",
                                { className: "mt-2 text-sm font-medium text-slate-500" },
                                "Connected grade portal",
                            ),
                        ),
                        h(
                            "div",
                            {
                                className:
                                    "grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-brand-orangeSoft text-brand-orangeDark",
                            },
                            h(Icon, { name: "external" }),
                        ),
                    ),
                    h(StatCard, {
                        icon: "shield",
                        label: "Standing",
                        value: student.standing || "Unknown",
                        note: "Current grade standing",
                        tone: student.standing === "Good" ? "green" : "orange",
                    }),
                ),
                h(
                    Card,
                    { className: "mb-4 p-4" },
                    h(
                        "div",
                        { className: "flex flex-wrap justify-end gap-2" },
                        h(
                            "form",
                            {
                                method: "POST",
                                onSubmit: (event) => {
                                    if (!window.confirm("This will begin a long job and may take a few minutes. Continue?")) {
                                        event.preventDefault();
                                    }
                                },
                            },
                            h(HiddenCsrf, { token: data.csrfToken }),
                            h(Button, {
                                type: "submit",
                                name: "run_agenda",
                                value: "1",
                                variant: "primary",
                                icon: "refresh",
                            }, "Refresh Agendas"),
                        ),
                        h(
                            "form",
                            {
                                method: "POST",
                                onSubmit: (event) => {
                                    if (!window.confirm("This will begin a long job and may take a few minutes. Continue?")) {
                                        event.preventDefault();
                                    }
                                },
                            },
                            h(HiddenCsrf, { token: data.csrfToken }),
                            h(Button, {
                                type: "submit",
                                name: "run_scraper",
                                value: "1",
                                variant: "gold",
                                icon: "refresh",
                            }, "Check Grades"),
                        ),
                    ),
                ),
                h(
                    "div",
                    { className: "mb-4 grid gap-3" },
                    h(ProgressPanel, {
                        jobId: data.jobId,
                        label: "Grade collection in progress",
                    }),
                    h(ProgressPanel, {
                        jobId: data.agendaJobId,
                        label: "Agenda refresh in progress",
                    }),
                ),
                h(
                    "section",
                    { className: "grid gap-4 xl:grid-cols-[0.85fr_1.15fr]" },
                    h(
                        Card,
                        { className: "overflow-hidden" },
                        h(CardHeader, { title: "Grade Snapshot", meta: "Recent" }),
                        h("div", { className: "p-5" }, h(GradeList, { grades: student.gradesSnapshot })),
                    ),
                    h(
                        Card,
                        { className: "overflow-hidden" },
                        h(CardHeader, { title: "Student Agenda", meta: "Upcoming" }),
                        h("div", { className: "p-5" }, h(AgendaList, { items: student.agendaItems || [] })),
                    ),
                    h(
                        Card,
                        { className: "overflow-hidden xl:col-span-2" },
                        h(
                            "div",
                            {
                                className:
                                    "flex flex-col gap-3 bg-brand-orange px-5 py-4 text-white sm:flex-row sm:items-center sm:justify-between",
                            },
                            h("h2", { className: "text-base font-extrabold" }, "Grade History"),
                            h(
                                "div",
                                {
                                    className:
                                        "inline-flex w-fit gap-1 rounded-md bg-white/20 p-1",
                                    role: "tablist",
                                },
                                ["history", "heatmap"].map((tab) =>
                                    h(
                                        "button",
                                        {
                                            key: tab,
                                            type: "button",
                                            className: cn(
                                                "tc-tab-trigger",
                                                activeTab === tab && "tc-tab-trigger--active",
                                                "tc-focus-ring rounded px-3 py-1.5 text-sm font-extrabold capitalize transition",
                                                activeTab === tab
                                                    ? "bg-white text-brand-blueDark"
                                                    : "text-white hover:bg-white/10",
                                            ),
                                            onClick: () => selectStudentTab(tab),
                                        },
                                        tab,
                                    ),
                                ),
                            ),
                        ),
                        h(
                            "div",
                            { className: "p-5" },
                            activeTab === "history"
                                ? h(GradeHistoryTable, { grades })
                                : h(GradeHeatmap, { grades }),
                        ),
                    ),
                ),
            ),
        );
    }

    function CardHeader({ meta, title }) {
        return h(
            "div",
            {
                className:
                    "tc-card-header flex items-center justify-between gap-4 bg-brand-orange px-5 py-4 text-white",
            },
            h("h2", { className: "tc-card-header__title text-base font-extrabold" }, title),
            meta
                ? h("span", { className: "tc-card-header__meta text-sm font-bold text-white/85" }, meta)
                : null,
        );
    }

    function AgendaList({ items }) {
        if (!items.length) {
            return h(
                "p",
                { className: "text-sm font-medium text-slate-500" },
                "No agenda items found.",
            );
        }
        return h(
            "div",
            { className: "tc-scrollbar grid max-h-[24rem] gap-3 overflow-y-auto pr-2" },
            items.map((item, index) =>
                h(
                    "div",
                    {
                        key: `${item.dueDate}-${item.title}-${index}`,
                        className:
                            "rounded-lg border border-slate-200 border-l-brand-orange bg-slate-50 p-4",
                    },
                    h(
                        "h3",
                        {
                            className:
                                "font-extrabold text-brand-orangeDark",
                        },
                        item.title || "Assignment",
                    ),
                    h(
                        "p",
                        { className: "mt-1 text-sm font-semibold text-slate-500" },
                        `${item.dueDate || "No date"} / ${item.course || "Course"}`,
                    ),
                ),
            ),
        );
    }

    function GradeHistoryTable({ grades }) {
        const weeks = Object.entries(grades || {}).reverse();
        if (!weeks.length) {
            return h(
                "p",
                { className: "text-sm font-medium text-slate-500" },
                "No grade history available.",
            );
        }
        return h(
            "div",
            { className: "tc-table-scroll rounded-lg border border-slate-200" },
            h(
                "table",
                { className: "tc-data-table min-w-[780px] w-full border-collapse" },
                h(
                    "thead",
                    null,
                    h(
                        "tr",
                        null,
                        h("th", { className: "w-48 bg-brand-blue px-4 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white" }, "Date"),
                        h("th", { className: "bg-brand-blue px-4 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white" }, "Grades"),
                    ),
                ),
                h(
                    "tbody",
                    null,
                    weeks.map(([date, batch]) => {
                        const entries = Object.entries(batch || {}).filter(([, value]) => typeof value === "number");
                        return h(
                            "tr",
                            { key: date, className: "border-b border-slate-100" },
                            h(
                                "td",
                                { className: "px-4 py-4 align-top text-sm font-extrabold text-brand-blueDark" },
                                date,
                            ),
                            h(
                                "td",
                                { className: "px-4 py-4 align-top" },
                                entries.length
                                    ? h(GradeList, {
                                          compact: true,
                                          grades: entries.map(([course, grade]) => ({ course, grade })),
                                      })
                                    : h(
                                          "span",
                                          { className: "text-sm font-medium text-slate-500" },
                                          "No numeric grades recorded.",
                                      ),
                            ),
                        );
                    }),
                ),
            ),
        );
    }

    function GradeHeatmap({ grades }) {
        const weeks = Object.keys(grades || {});
        const courses = useMemo(() => {
            const set = new Set();
            weeks.forEach((week) => {
                Object.entries(grades[week] || {}).forEach(([course, value]) => {
                    if (typeof value === "number") {
                        set.add(course);
                    }
                });
            });
            return Array.from(set).sort((a, b) =>
                a.localeCompare(b, undefined, { sensitivity: "base" }),
            );
        }, [grades, weeks.join("|")]);

        if (!weeks.length || !courses.length) {
            return h(
                "p",
                { className: "text-sm font-medium text-slate-500" },
                "No heatmap data available.",
            );
        }

        return h(
            "div",
            { className: "grid gap-4" },
            h(
                "div",
                { className: "flex flex-wrap items-center justify-between gap-3" },
                h(
                    "div",
                    null,
                    h(
                        "h3",
                        {
                            className:
                                "text-lg font-extrabold tracking-normal text-brand-blueDark",
                        },
                        "Grade Heatmap",
                    ),
                    h(
                        "p",
                        { className: "mt-1 text-sm font-medium text-slate-500" },
                        "Weeks across the top, courses down the side",
                    ),
                ),
                h(Badge, { tone: "warning" }, `${courses.length} courses`),
            ),
            h(
                "div",
                {
                    className:
                        "tc-table-scroll rounded-lg border border-slate-200 bg-white",
                    role: "region",
                    "aria-label": "Student grade heatmap",
                },
                h(
                    "table",
                    { className: "tc-heatmap-table w-max min-w-full border-separate border-spacing-0" },
                    h(
                        "thead",
                        null,
                        h(
                            "tr",
                            null,
                            h(
                                "th",
                                {
                                    className:
                                        "tc-sticky-first min-w-[260px] bg-brand-blueDark px-3 py-3 text-left text-xs font-extrabold uppercase tracking-normal text-white",
                                },
                                "Course / Week",
                            ),
                            weeks.map((week) =>
                                h(
                                    "th",
                                    {
                                        key: week,
                                        title: week,
                                        className:
                                            "bg-brand-blue px-3 py-3 text-center text-xs font-extrabold uppercase tracking-normal text-white",
                                    },
                                    formatWeekLabel(week),
                                ),
                            ),
                        ),
                    ),
                    h(
                        "tbody",
                        null,
                        courses.map((course) =>
                            h(
                                "tr",
                                { key: course, className: "group" },
                                h(
                                    "td",
                                    {
                                        className:
                                            "tc-sticky-first max-w-[360px] border-b border-r-2 border-r-brand-orange bg-slate-50 px-3 py-2 text-sm font-extrabold text-slate-900 group-hover:bg-brand-orangeSoft",
                                    },
                                    course,
                                ),
                                weeks.map((week) => {
                                    const grade = grades[week]?.[course];
                                    const hasGrade = typeof grade === "number";
                                    return h(
                                        "td",
                                        {
                                            key: `${course}-${week}`,
                                            title: hasGrade
                                                ? `${course} / ${week}: ${formatGrade(grade)}`
                                                : `${course} / ${week}: no grade recorded`,
                                            className: cn(
                                                "tc-heatmap-cell border-b border-r border-slate-100 text-center text-sm font-extrabold",
                                                !hasGrade && "bg-slate-50 text-slate-400",
                                            ),
                                            style: hasGrade
                                                ? { background: gradeToColor(grade) }
                                                : undefined,
                                        },
                                        hasGrade ? formatGrade(grade) : "-",
                                    );
                                }),
                            ),
                        ),
                    ),
                ),
            ),
            h(
                "div",
                { className: "flex flex-wrap items-center justify-between gap-3" },
                h(
                    "div",
                    { className: "flex items-center gap-2 text-sm font-bold text-slate-500" },
                    h("span", null, "Lower"),
                    h("div", {
                        className:
                            "h-2.5 w-40 rounded-full border border-slate-200 bg-gradient-to-r from-red-200 via-amber-200 to-emerald-300",
                    }),
                    h("span", null, "Higher"),
                ),
            ),
        );
    }

    function gradeToColor(grade) {
        const clamped = Math.max(0, Math.min(100, grade));
        if (clamped < 70) {
            return interpolateHsl(0, 78, 88, 0, 72, 78, clamped / 70);
        }
        if (clamped < 80) {
            return interpolateHsl(35, 86, 84, 45, 88, 76, (clamped - 70) / 10);
        }
        if (clamped < 90) {
            return interpolateHsl(52, 86, 80, 78, 72, 74, (clamped - 80) / 10);
        }
        return interpolateHsl(105, 62, 76, 135, 60, 70, (clamped - 90) / 10);
    }

    function interpolateHsl(h1, s1, l1, h2, s2, l2, t) {
        const pct = Math.max(0, Math.min(1, t));
        const hValue = h1 + (h2 - h1) * pct;
        const sValue = s1 + (s2 - s1) * pct;
        const lValue = l1 + (l2 - l1) * pct;
        return `hsl(${hValue}, ${sValue}%, ${lValue}%)`;
    }

    function formatWeekLabel(week) {
        const parsed = new Date(String(week || ""));
        if (!Number.isNaN(parsed.getTime())) {
            return parsed.toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
            });
        }
        return String(week || "");
    }

    function App({ data }) {
        if (data.page === "login") {
            return h(LoginPage, { data });
        }
        if (data.page === "health") {
            return h(HealthPage, { data });
        }
        if (data.page === "franchise") {
            return h(FranchisePage, { data });
        }
        if (data.page === "student") {
            return h(StudentPage, { data });
        }
        return h(
            "div",
            { className: "p-6 text-sm font-medium text-slate-600" },
            "Unknown dashboard page.",
        );
    }

    const root = window.ReactDOM.createRoot
        ? window.ReactDOM.createRoot(rootEl)
        : null;
    if (root) {
        root.render(h(App, { data: readPageData() }));
    } else {
        window.ReactDOM.render(h(App, { data: readPageData() }), rootEl);
    }
})();
