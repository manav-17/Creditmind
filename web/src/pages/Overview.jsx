import { useEffect, useState } from "react";
import { api, errorLines } from "../api";
import { navigate } from "../router";
import { Empty, ErrorNote, OutcomeBadge, PageHeader, Section, StatusBadge } from "../components/ui";
import { OUTCOME, pct, when } from "../format";

function Figure({ label, value, tone = "text-ink" }) {
  return (
    <div className="px-5 py-4">
      <div className={`text-3xl font-semibold tracking-tight ${tone}`}>{value}</div>
      <div className="mt-1 text-sm text-muted">{label}</div>
    </div>
  );
}

export default function Overview() {
  const [stats, setStats] = useState(null);
  const [recent, setRecent] = useState([]);
  const [errors, setErrors] = useState([]);

  useEffect(() => {
    Promise.all([api("/stats"), api("/applications?limit=8")])
      .then(([s, r]) => {
        setStats(s);
        setRecent(r);
      })
      .catch((e) => setErrors(errorLines(e)));
  }, []);

  if (errors.length) return <ErrorNote lines={errors} />;
  if (!stats) return <p className="text-muted">Loading the portfolio...</p>;

  const outcomes = { APPROVE: 0, REFER: stats.pending_reviews, DECLINE: 0, ...stats.by_outcome };
  const decided = (outcomes.APPROVE || 0) + (outcomes.DECLINE || 0) + (outcomes.REFER || 0);
  const days = [...new Set(stats.daily.map((d) => d.day))];
  const perDay = days.map((day) => ({
    day,
    total: stats.daily.filter((d) => d.day === day).reduce((sum, d) => sum + d.count, 0),
  }));
  const peak = Math.max(1, ...perDay.map((d) => d.total));

  return (
    <div className="space-y-10">
      <PageHeader title="Portfolio">
        <a href="#/submit" className="rounded-md bg-ink px-4 py-2 text-sm font-medium text-white hover:bg-[#22385a]">
          Assess an application
        </a>
      </PageHeader>

      <div className="grid grid-cols-2 divide-rule rounded-md border border-rule bg-white sm:grid-cols-5 sm:divide-x">
        <Figure label="Applications" value={stats.total} />
        <Figure label="Approved" value={outcomes.APPROVE || 0} tone="text-approve" />
        <Figure label="Declined" value={outcomes.DECLINE || 0} tone="text-decline" />
        <Figure label="Awaiting review" value={stats.pending_reviews} tone="text-refer" />
        <Figure label="Average PD" value={pct(stats.avg_pd)} />
      </div>

      {stats.pending_reviews > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-refer-tint px-5 py-4">
          <p className="text-refer">
            {stats.pending_reviews} application{stats.pending_reviews > 1 ? "s are" : " is"} waiting for a credit officer.
          </p>
          <a href="#/review" className="text-sm font-medium text-refer underline underline-offset-4">
            Open the review queue
          </a>
        </div>
      )}

      <div className="grid gap-10 lg:grid-cols-2">
        <Section title="Outcomes">
          {decided === 0 ? (
            <Empty>No decisions yet.</Empty>
          ) : (
            <>
              <div className="flex h-4 overflow-hidden rounded-sm" aria-hidden="true">
                {["APPROVE", "REFER", "DECLINE"].map((o) =>
                  outcomes[o] ? (
                    <div key={o} className={OUTCOME[o].bar} style={{ width: `${(outcomes[o] / decided) * 100}%` }} />
                  ) : null,
                )}
              </div>
              <dl className="mt-4 grid grid-cols-3 gap-4 text-sm">
                {["APPROVE", "REFER", "DECLINE"].map((o) => (
                  <div key={o}>
                    <dt className="text-muted">{o === "REFER" ? "In review" : OUTCOME[o].label}</dt>
                    <dd className={`text-lg font-semibold ${OUTCOME[o].text}`}>
                      {Math.round(((outcomes[o] || 0) / decided) * 100)}%
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          )}
        </Section>

        <Section title="Applications per day" aside="Last 14 days">
          {perDay.length === 0 ? (
            <Empty>No applications in the last 14 days.</Empty>
          ) : (
            <div className="flex h-32 items-end gap-2">
              {perDay.map((d) => (
                <div key={d.day} className="flex max-w-14 flex-1 flex-col items-center gap-1">
                  <span className="text-xs text-muted">{d.total}</span>
                  <div className="w-full rounded-sm bg-signal/70" style={{ height: `${(d.total / peak) * 88}px` }} />
                  <span className="text-xs text-muted">
                    {new Date(d.day).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Section>
      </div>

      <Section title="Recent applications" aside={<a href="#/applications" className="underline underline-offset-4">View all</a>}>
        {recent.length === 0 ? (
          <Empty>Nothing assessed yet. Start with a demo applicant on the New application page.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-muted">
                <tr className="border-b border-rule">
                  <th className="py-2 pr-4 font-normal">Application</th>
                  <th className="py-2 pr-4 font-normal">Status</th>
                  <th className="py-2 pr-4 font-normal">Outcome</th>
                  <th className="py-2 pr-4 text-right font-normal">PD</th>
                  <th className="py-2 font-normal">Updated</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((r) => (
                  <tr
                    key={r.application_id}
                    onClick={() => navigate(`/applications/${encodeURIComponent(r.application_id)}`)}
                    className="cursor-pointer border-b border-rule hover:bg-panel"
                  >
                    <td className="py-2.5 pr-4 font-medium">{r.application_id}</td>
                    <td className="py-2.5 pr-4"><StatusBadge status={r.status} /></td>
                    <td className="py-2.5 pr-4"><OutcomeBadge outcome={r.final_outcome} /></td>
                    <td className="py-2.5 pr-4 text-right">{pct(r.probability_of_default)}</td>
                    <td className="py-2.5 text-muted">{when(r.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}