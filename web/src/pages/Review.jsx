import { useEffect, useState } from "react";
import { api, errorLines } from "../api";
import ReviewForm from "../components/ReviewForm";
import { Empty, ErrorNote, OutcomeBadge, PageHeader } from "../components/ui";
import { pct } from "../format";

export default function Review() {
  const [queue, setQueue] = useState(null);
  const [errors, setErrors] = useState([]);
  const [finished, setFinished] = useState([]);

  const load = () =>
    api("/review-queue")
      .then(setQueue)
      .catch((e) => setErrors(errorLines(e)));
  useEffect(() => {
    load();
  }, []);

  return (
    <div>
      <PageHeader title="Review queue" />
      <p className="-mt-5 mb-8 max-w-2xl text-muted">
        These applications were referred by the agents. Only a credit officer can approve a referral (POL-8.1).
      </p>
      <ErrorNote lines={errors} />
      {finished.map((row) => (
        <div key={row.application_id} className="mb-4 flex flex-wrap items-center gap-3 rounded-md bg-panel px-4 py-3 text-sm">
          <span className="font-medium">{row.application_id}</span>
          <OutcomeBadge outcome={row.final_outcome} />
          <a href={`#/applications/${encodeURIComponent(row.application_id)}`} className="underline underline-offset-4">
            Open the decision record
          </a>
        </div>
      ))}
      {queue === null && !errors.length && <p className="text-muted">Loading...</p>}
      {queue?.length === 0 && <Empty>No applications are waiting for review.</Empty>}
      <div className="space-y-6">
        {queue?.map((item) => {
          const req = item.review_request || {};
          return (
            <article key={item.application_id} className="rounded-md border border-rule bg-white p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <a
                  href={`#/applications/${encodeURIComponent(item.application_id)}`}
                  className="text-lg font-semibold underline-offset-4 hover:underline"
                >
                  {item.application_id}
                </a>
                <span className="text-sm text-muted">
                  PD {pct(item.probability_of_default)}, model zone {String(item.risk_zone || "").toLowerCase()}
                </span>
              </div>
              <p className="mt-3 max-w-3xl">{req.summary}</p>
              {req.reasons?.length > 0 && (
                <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
                  {req.reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              )}
              {req.fraud_indicators?.length > 0 && (
                <p className="mt-3 rounded-md bg-decline-tint px-3 py-2 text-sm text-decline">
                  Fraud indicators: {req.fraud_indicators.join("; ")}
                </p>
              )}
              {req.cited_clauses?.length > 0 && (
                <p className="mt-3 text-sm text-muted">Policy clauses: {req.cited_clauses.join(", ")}</p>
              )}
              <div className="mt-5 border-t border-rule pt-5">
                <ReviewForm
                  applicationId={item.application_id}
                  onDone={(row) => {
                    setFinished((f) => [row, ...f]);
                    setQueue((q) => q.filter((x) => x.application_id !== item.application_id));
                  }}
                />
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}