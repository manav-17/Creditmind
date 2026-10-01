// Shared UI components (helpers and constants live in ../format.js).
import { OUTCOME, pct } from "../format";

const STATUS = {
  COMPLETED: { label: "Completed", cls: "text-ink bg-panel" },
  PENDING_REVIEW: { label: "In review", cls: "text-refer bg-refer-tint" },
  PROCESSING: { label: "Processing", cls: "text-signal bg-blue-50" },
  ERROR: { label: "Error", cls: "text-decline bg-decline-tint" },
};

/* CreditMind logo mark */
export function Mark({ className = "h-8 w-8" }) {
  return (
    <svg
      viewBox="0 0 40 40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      <rect
        x="3"
        y="3"
        width="34"
        height="34"
        rx="10"
        stroke="currentColor"
        strokeWidth="2"
      />
      <path
        d="M12 20h16M20 12v16"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
      <circle cx="20" cy="20" r="4" fill="currentColor" />
    </svg>
  );
}

/* Small navigation/action icons */
export function Icon({ name, className = "h-5 w-5" }) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round",
    strokeLinejoin: "round",
  };

  const paths = {
    overview: (
      <>
        <rect {...common} x="3" y="3" width="7" height="7" rx="1" />
        <rect {...common} x="14" y="3" width="7" height="7" rx="1" />
        <rect {...common} x="3" y="14" width="7" height="7" rx="1" />
        <rect {...common} x="14" y="14" width="7" height="7" rx="1" />
      </>
    ),

    queue: (
      <>
        <path {...common} d="M4 6h16" />
        <path {...common} d="M4 12h16" />
        <path {...common} d="M4 18h10" />
        <circle {...common} cx="18" cy="18" r="2.5" />
      </>
    ),

    list: (
      <>
        <path {...common} d="M9 6h11" />
        <path {...common} d="M9 12h11" />
        <path {...common} d="M9 18h11" />
        <circle cx="4.5" cy="6" r="1.2" fill="currentColor" />
        <circle cx="4.5" cy="12" r="1.2" fill="currentColor" />
        <circle cx="4.5" cy="18" r="1.2" fill="currentColor" />
      </>
    ),

    plus: (
      <>
        <path {...common} d="M12 5v14" />
        <path {...common} d="M5 12h14" />
      </>
    ),

    logout: (
      <>
        <path {...common} d="M10 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19H10" />
        <path {...common} d="M14 8l4 4-4 4" />
        <path {...common} d="M18 12H9" />
      </>
    ),
  };

  return (
    <svg
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      {paths[name] || null}
    </svg>
  );
}

export function OutcomeBadge({ outcome }) {
  const o = OUTCOME[outcome];

  if (!o) return <span className="text-muted">–</span>;

  return (
    <span
      className={`inline-flex rounded px-2 py-0.5 text-sm font-medium ${o.text} ${o.bg}`}
    >
      {o.label}
    </span>
  );
}

export function StatusBadge({ status }) {
  const s = STATUS[status] || {
    label: status,
    cls: "text-muted bg-panel",
  };

  return (
    <span className={`inline-flex rounded px-2 py-0.5 text-sm ${s.cls}`}>
      {s.label}
    </span>
  );
}

export function Button({
  variant = "primary",
  className = "",
  ...props
}) {
  const styles = {
    primary:
      "bg-ink text-white hover:bg-[#22385a] disabled:bg-muted",
    approve:
      "bg-approve text-white hover:brightness-110 disabled:opacity-50",
    decline:
      "bg-decline text-white hover:brightness-110 disabled:opacity-50",
    quiet:
      "border border-rule bg-white text-ink hover:bg-panel disabled:opacity-50",
  };

  return (
    <button
      className={`rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed ${styles[variant]} ${className}`}
      {...props}
    />
  );
}

export function Section({
  title,
  aside,
  children,
  className = "",
}) {
  return (
    <section className={`border-t border-rule pt-5 ${className}`}>
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 className="text-base font-semibold">{title}</h2>
        {aside && (
          <div className="text-sm text-muted">{aside}</div>
        )}
      </div>

      {children}
    </section>
  );
}

export function PageHeader({ title, children }) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">
        {title}
      </h1>

      {children}
    </header>
  );
}

export function ErrorNote({ lines }) {
  if (!lines?.length) return null;

  return (
    <div
      role="alert"
      className="rounded-md border border-decline/30 bg-decline-tint px-4 py-3 text-sm text-decline"
    >
      {lines.map((line, i) => (
        <p key={i}>{line}</p>
      ))}
    </div>
  );
}

export function Empty({ children }) {
  return (
    <p className="rounded-md bg-panel px-4 py-6 text-sm text-muted">
      {children}
    </p>
  );
}

// Where the applicant's probability of default sits against the decision thresholds.
export function PDScale({
  pd,
  approveBelow,
  rejectAbove,
}) {
  if (
    pd === null ||
    pd === undefined ||
    !approveBelow ||
    !rejectAbove
  ) {
    return null;
  }

  const max = Math.max(0.5, pd * 1.15);
  const at = (v) =>
    `${Math.min(100, (v / max) * 100)}%`;

  return (
    <div className="w-full">
      <div
        className="relative h-3 overflow-hidden rounded-sm"
        aria-hidden="true"
      >
        <div
          className="absolute inset-y-0 left-0 bg-approve/25"
          style={{ width: at(approveBelow) }}
        />

        <div
          className="absolute inset-y-0 bg-refer/30"
          style={{
            left: at(approveBelow),
            width: `calc(${at(rejectAbove)} - ${at(approveBelow)})`,
          }}
        />

        <div
          className="absolute inset-y-0 right-0 bg-decline/20"
          style={{ left: at(rejectAbove) }}
        />
      </div>

      <div className="relative h-8">
        <div
          className="absolute -top-5 h-7 w-0.5 bg-ink"
          style={{ left: at(pd) }}
        />

        <div
          className="absolute top-2 -translate-x-1/2 whitespace-nowrap text-sm font-semibold"
          style={{ left: at(pd) }}
        >
          {pct(pd)}
        </div>
      </div>

      <div className="relative h-4 text-xs text-muted">
        <span className="absolute left-0">
          Approve below {pct(approveBelow)}
        </span>

        <span
          className="absolute hidden sm:block"
          style={{ left: at(approveBelow) }}
        >
          Officer review
        </span>

        <span className="absolute right-0">
          Decline above {pct(rejectAbove)}
        </span>
      </div>

      <p className="sr-only">
        Probability of default {pct(pd)}. Approve below{" "}
        {pct(approveBelow)}, decline above {pct(rejectAbove)}.
      </p>
    </div>
  );
}