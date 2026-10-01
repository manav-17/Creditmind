// Formatting helpers and display constants (kept apart from components for React fast refresh).

export const pct = (v, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? "–" : `${(v * 100).toFixed(digits)}%`;

export const money = (v) =>
  v === null || v === undefined ? "–" : `$${Math.round(v).toLocaleString("en-US")}`;

export const when = (iso) =>
  iso
    ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
    : "–";

export const OUTCOME = {
  APPROVE: { label: "Approved", text: "text-approve", bg: "bg-approve-tint", bar: "bg-approve" },
  REFER: { label: "Referred", text: "text-refer", bg: "bg-refer-tint", bar: "bg-refer" },
  DECLINE: { label: "Declined", text: "text-decline", bg: "bg-decline-tint", bar: "bg-decline" },
};