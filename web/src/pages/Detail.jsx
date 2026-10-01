import { useEffect, useState } from "react";
import { api, errorLines } from "../api";
import ReviewForm from "../components/ReviewForm";
import { Empty, ErrorNote, PDScale, Section, StatusBadge } from "../components/ui";
import { OUTCOME, when } from "../format";

const LLM_NODES = new Set(["fraud_check", "policy_check", "explain", "decide", "report"]);
const SOURCE = { rule_engine: "rule engine", system: "system", llm: "policy agent" };

// Minimal Markdown for memos: headings, bullets, numbered lists, tables, bold.
function Memo({ text }) {
  if (!text) return null;
  const inline = (str) =>
    str.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : part,
    );
  const cells = (row) => row.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const blocks = [];
  let list = [];
  let table = [];
  const flushList = () => {
    if (list.length) blocks.push(<ul key={`l${blocks.length}`} className="my-2 list-disc space-y-1 pl-5">{list}</ul>);
    list = [];
  };
  const flushTable = () => {
    const rows = table.filter((r) => !/^\|?[\s:|-]+\|?$/.test(r)); // drop the |---|---| divider
    table = [];
    if (!rows.length) return;
    const [head, ...body] = rows.map(cells);
    blocks.push(
      <div key={`t${blocks.length}`} className="my-3 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-muted">
            <tr className="border-b border-rule">
              {head.map((h, i) => <th key={i} className="py-1.5 pr-4 font-normal">{inline(h)}</th>)}
            </tr>
          </thead>
          <tbody>
            {body.map((r, i) => (
              <tr key={i} className="border-b border-rule align-top">
                {r.map((c, j) => <td key={j} className="py-1.5 pr-4">{inline(c)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>,
    );
  };
  text.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (line.startsWith("|")) {
      flushList();
      table.push(line);
      return;
    }
    flushTable();
    if (/^[-*] /.test(line)) return list.push(<li key={i}>{inline(line.slice(2))}</li>);
    flushList();
    if (!line || /^-{3,}$/.test(line)) return;
    if (line.startsWith("#")) {
      blocks.push(<h4 key={i} className="mt-4 mb-1 font-semibold">{line.replace(/^#+\s*/, "")}</h4>);
    } else {
      blocks.push(<p key={i} className="my-2">{inline(line)}</p>);
    }
  });
  flushTable();
  flushList();
  return <div className="max-w-[70ch] leading-relaxed">{blocks}</div>;
}

function Trail({ audit }) {
  if (!audit?.length) return <Empty>No steps recorded yet.</Empty>;
  return (
    <ol className="relative ml-2 border-l border-rule">
      {audit.map((step, i) => {
        const provider = step.event.match(/\[(llm:\w+|fallback[^\]]*|error[^\]]*)\]/)?.[1];
        const event = step.event.replace(/\s*\[[^\]]*\]\s*$/, "");
        const blocked = /blocked|failed|unsupported: /.test(step.event);
        return (
          <li key={i} className="relative pb-4 pl-5">
            <span
              className={`absolute top-1.5 -left-[5px] h-2.5 w-2.5 rounded-full ${
                blocked ? "bg-decline" : LLM_NODES.has(step.node) ? "bg-signal" : "bg-muted"
              }`}
              aria-hidden="true"
            />
            <div className="flex flex-wrap items-baseline gap-x-3">
              <span className="text-sm font-medium">{step.node.replace(/_/g, " ")}</span>
              {provider && (
                <span className={`rounded px-1.5 text-xs ${provider.startsWith("llm") ? "bg-blue-50 text-signal" : "bg-decline-tint text-decline"}`}>
                  {provider.replace("llm:", "")}
                </span>
              )}
            </div>
            <p className={`text-sm ${blocked ? "text-decline" : "text-muted"}`}>{event}</p>
          </li>
        );
      })}
    </ol>
  );
}

function Factors({ factors }) {
  if (!factors?.length) return <Empty>No model explanation available.</Empty>;
  const sorted = [...factors].sort((a, b) => b.impact - a.impact);
  const peak = Math.max(...sorted.map((f) => Math.abs(f.impact)), 0.01);
  return (
    <div className="space-y-2.5">
      {sorted.map((f) => {
        const raises = f.impact > 0;
        const width = `${(Math.abs(f.impact) / peak) * 50}%`;
        return (
          <div key={f.feature} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] items-center gap-4 text-sm">
            <div>
              <div className="font-medium">{f.name}</div>
              <div className="text-muted">{f.value}</div>
            </div>
            <div className="relative h-4" title={`${f.direction} (${f.impact.toFixed(3)})`}>
              <div className="absolute inset-y-0 left-1/2 w-px bg-rule" />
              <div
                className={`absolute inset-y-0.5 rounded-sm ${raises ? "left-1/2 bg-decline" : "right-1/2 bg-approve"}`}
                style={{ width }}
              />
            </div>
          </div>
        );
      })}
      <p className="pt-1 text-xs text-muted">Red bars raise the risk of default, green bars lower it.</p>
    </div>
  );
}

export default function Detail({ id }) {
  const [row, setRow] = useState(null);
  const [errors, setErrors] = useState([]);
  const [tab, setTab] = useState("factors");

  useEffect(() => {
    let alive = true;
    let timer;
    const load = async () => {
      try {
        const data = await api(`/applications/${encodeURIComponent(id)}`);
        if (!alive) return;
        setRow(data);
        if (data.status === "PROCESSING") timer = setTimeout(load, 2000);
      } catch (e) {
        if (alive) setErrors(errorLines(e));
      }
    };
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [id]);

  if (errors.length) return <ErrorNote lines={errors} />;
  if (!row) return <p className="text-muted">Loading the decision record...</p>;

  const record = row.record || {};
  const risk = record.risk || {};
  const decision = record.final_decision || {};
  const outcome = row.final_outcome || (row.status === "PENDING_REVIEW" ? "REFER" : null);
  const tone = OUTCOME[outcome] || { label: "Processing", text: "text-signal", bg: "bg-blue-50" };
  const consistency = record.consistency;
  const grounding = record.grounding;
  const review = record.human_review;
  const flags = record.input_flags || row.input_summary || {};

  if (row.status === "PROCESSING" && !record.risk) {
    return (
      <div>
        <h1 className="text-2xl font-semibold">{row.application_id}</h1>
        <p className="mt-6 text-muted">
          The agents are assessing this application: risk model, fraud screening and policy checks run in
          parallel, then three decision votes, a grounding check and the critic. This takes about 15 to 40 seconds.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <header className={`rounded-md px-6 py-6 ${tone.bg}`}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-sm text-muted">{row.application_id}</p>
            <h1 className={`text-3xl font-semibold tracking-tight ${tone.text}`}>
              {row.status === "PENDING_REVIEW" ? "Waiting for a credit officer" : tone.label}
            </h1>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <StatusBadge status={row.status} />
            <span className="text-muted">Updated {when(row.updated_at)}</span>
          </div>
        </div>
        <div className="mt-8">
          <PDScale
            pd={risk.probability_of_default}
            approveBelow={risk.thresholds?.approve_below}
            rejectAbove={risk.thresholds?.reject_above}
          />
        </div>
        <dl className="mt-6 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-muted">Required minimum</dt>
            <dd className="font-medium">{record.constraints?.required_outcome?.toLowerCase() || "–"}</dd>
          </div>
          <div>
            <dt className="text-muted">Decision votes</dt>
            <dd className="font-medium">
              {consistency
                ? `${Math.round(consistency.agreement * consistency.votes)} of ${consistency.votes} agree`
                : "–"}
            </dd>
          </div>
          <div>
            <dt className="text-muted">Figures verified</dt>
            <dd className="font-medium">
              {grounding ? `${grounding.checked - grounding.unsupported.length} of ${grounding.checked}` : "–"}
            </dd>
          </div>
          <div>
            <dt className="text-muted">Critic attempts</dt>
            <dd className="font-medium">
              {record.decision_attempts ?? "–"}
              {decision.fail_safe ? " (fail-safe applied)" : ""}
            </dd>
          </div>
        </dl>
      </header>

      {row.status === "ERROR" && <ErrorNote lines={[row.error]} />}

      {row.status === "PENDING_REVIEW" && (
        <Section title="Your decision">
          <ReviewForm applicationId={row.application_id} onDone={setRow} />
        </Section>
      )}

      {decision.decision && (
        <Section title="Why" aside={decision.cited_clauses?.length ? `Policy: ${decision.cited_clauses.join(", ")}` : null}>
          <p className="max-w-[70ch] leading-relaxed">{decision.summary}</p>
          {decision.principal_reasons?.length > 0 && (
            <ul className="mt-3 list-disc space-y-1 pl-5">
              {decision.principal_reasons.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          )}
          {review && (
            <p className="mt-4 rounded-md bg-panel px-4 py-3 text-sm">
              Officer {review.officer_id} {review.decision === "APPROVE" ? "approved" : "declined"}
              {review.override ? ", overriding the referral" : ""}: {review.note || "no note"}
            </p>
          )}
        </Section>
      )}

      <div>
        <div className="mb-5 flex flex-wrap gap-1 border-b border-rule" role="tablist">
          {[
            ["factors", "Risk factors"],
            ["trail", "Agent trail"],
            ["policy", "Policy"],
            ["security", "Fraud and security"],
            ["memo", "Memo and notice"],
          ].map(([value, text]) => (
            <button
              key={value}
              role="tab"
              aria-selected={tab === value}
              onClick={() => setTab(value)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm ${
                tab === value ? "border-ink font-medium text-ink" : "border-transparent text-muted hover:text-ink"
              }`}
            >
              {text}
            </button>
          ))}
        </div>

        {tab === "factors" && <Factors factors={risk.top_factors} />}
        {tab === "trail" && <Trail audit={record.audit} />}

        {tab === "policy" && (
          <div className="space-y-6">
            <div>
              <h3 className="mb-2 text-sm font-semibold">Rules checked by code</h3>
              {record.policy?.rule_engine?.hits?.length ? (
                <ul className="space-y-1.5 text-sm">
                  {record.policy.rule_engine.hits.map((h, i) => (
                    <li key={i}>
                      <span className="font-medium">{h.clause_id}</span>{" "}
                      <span className="text-muted">({h.outcome.toLowerCase()})</span> {h.reason}
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>No policy rules were triggered.</Empty>
              )}
            </div>
            <div>
              <h3 className="mb-2 text-sm font-semibold">Clauses retrieved for this application</h3>
              <ul className="space-y-2 text-sm">
                {(record.policy?.findings || []).map((f, i) => (
                  <li key={i} className={f.applies ? "" : "text-muted"}>
                    <span className="font-medium">{f.clause_id}</span> {f.applies ? "applies" : "does not apply"}:{" "}
                    {f.explanation}
                    {SOURCE[f.source] && (
                      <span className="ml-2 rounded bg-panel px-1.5 text-xs text-muted">{SOURCE[f.source]}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {tab === "security" && (
          <dl className="grid gap-5 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-muted">Fraud screening</dt>
              <dd className="font-medium">
                {record.fraud?.risk_level?.toLowerCase() || "–"} risk
                {record.fraud?.refer_for_fraud_review ? ", referred for fraud review" : ""}
              </dd>
              {record.fraud?.indicators?.length > 0 && (
                <ul className="mt-1 list-disc pl-5">{record.fraud.indicators.map((x, i) => <li key={i}>{x}</li>)}</ul>
              )}
              {record.fraud?.advisory_notes?.length > 0 && (
                <p className="mt-1 text-muted">Notes for the officer: {record.fraud.advisory_notes.join("; ")}</p>
              )}
            </div>
            <div>
              <dt className="text-muted">Prompt injection</dt>
              <dd className={`font-medium ${flags.injection?.detected ? "text-decline" : ""}`}>
                {flags.injection?.detected
                  ? `Detected and removed (${flags.injection.patterns.join(", ")})`
                  : "None detected"}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Personal data masked before any AI model</dt>
              <dd className="font-medium">
                {flags.pii_found && Object.keys(flags.pii_found).length
                  ? Object.entries(flags.pii_found).map(([field, types]) => `${field}: ${types.join(", ")}`).join("; ")
                  : "None found in the applicant's text"}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Model version</dt>
              <dd className="font-medium">{risk.model_version || "–"}</dd>
            </div>
          </dl>
        )}

        {tab === "memo" &&
          (record.report ? (
            <div className="grid gap-10 lg:grid-cols-2">
              <div>
                <h3 className="mb-2 text-sm font-semibold">Credit memo</h3>
                <Memo text={record.report.internal_memo} />
              </div>
              <div>
                <h3 className="mb-2 text-sm font-semibold">Letter to the applicant</h3>
                <div className="rounded-md border border-rule bg-white px-5 py-4">
                  <Memo text={record.report.applicant_notice} />
                </div>
              </div>
            </div>
          ) : (
            <Empty>The memo is written after the final decision.</Empty>
          ))}
      </div>
    </div>
  );
}