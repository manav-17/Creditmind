import { useEffect, useState } from "react";
import { api, errorLines } from "../api";
import { navigate } from "../router";
import { Empty, ErrorNote, OutcomeBadge, PageHeader, StatusBadge } from "../components/ui";
import { pct, when } from "../format";

const FILTERS = [
  { value: "", label: "All" },
  { value: "PENDING_REVIEW", label: "In review" },
  { value: "COMPLETED", label: "Completed" },
  { value: "PROCESSING", label: "Processing" },
  { value: "ERROR", label: "Errors" },
];

export default function Applications() {
  const [result, setResult] = useState({ status: null, rows: [] });
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [errors, setErrors] = useState([]);

  useEffect(() => {
    let alive = true;
    api(`/applications${status ? `?status=${status}` : ""}`)
      .then((rows) => alive && setResult({ status, rows }))
      .catch((e) => alive && setErrors(errorLines(e)));
    return () => {
      alive = false;
    };
  }, [status]);

  // Rows belong to the filter they were loaded for; anything else is still loading
  const rows = result.status === status ? result.rows : null;
  const shown = (rows || []).filter((r) => r.application_id.toLowerCase().includes(query.toLowerCase()));

  return (
    <div>
      <PageHeader title="Applications" />
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by status">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setStatus(f.value)}
              aria-pressed={status === f.value}
              className={`rounded-md px-3 py-1.5 text-sm ${
                status === f.value ? "bg-ink text-white" : "text-muted hover:bg-panel hover:text-ink"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by application ID"
          aria-label="Search by application ID"
          className="w-full rounded-md border border-rule bg-white px-3 py-2 text-sm sm:w-72"
        />
      </div>
      <ErrorNote lines={errors} />
      {rows === null && !errors.length && <p className="text-muted">Loading...</p>}
      {rows !== null && shown.length === 0 && <Empty>No applications match this view.</Empty>}
      {shown.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-rule bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-panel text-muted">
              <tr>
                <th className="px-4 py-2.5 font-normal">Application</th>
                <th className="px-4 py-2.5 font-normal">Status</th>
                <th className="px-4 py-2.5 font-normal">Outcome</th>
                <th className="px-4 py-2.5 text-right font-normal">PD</th>
                <th className="px-4 py-2.5 font-normal">Model zone</th>
                <th className="px-4 py-2.5 font-normal">Updated</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr
                  key={r.application_id}
                  onClick={() => navigate(`/applications/${encodeURIComponent(r.application_id)}`)}
                  className="cursor-pointer border-t border-rule hover:bg-panel"
                >
                  <td className="px-4 py-3 font-medium">{r.application_id}</td>
                  <td className="px-4 py-3"><StatusBadge status={r.status} /></td>
                  <td className="px-4 py-3"><OutcomeBadge outcome={r.final_outcome} /></td>
                  <td className="px-4 py-3 text-right">{pct(r.probability_of_default)}</td>
                  <td className="px-4 py-3 text-muted">{r.risk_zone ? r.risk_zone.toLowerCase() : "–"}</td>
                  <td className="px-4 py-3 text-muted">{when(r.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}