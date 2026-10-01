import { useState } from "react";
import { api, errorLines } from "../api";
import { Button, ErrorNote } from "./ui";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Approve or decline a referred application. The API resumes the paused LangGraph run.
export default function ReviewForm({ applicationId, onDone }) {
  const [officer, setOfficer] = useState(sessionStorage.getItem("creditmind_officer") || "officer-01");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(null);
  const [errors, setErrors] = useState([]);

  async function decide(decision) {
    setErrors([]);
    if (decision === "APPROVE" && note.trim().length < 20) {
      setErrors(["Approving a referral overrides the system (POL-8.2). Write a justification of at least 20 characters."]);
      return;
    }
    setBusy(decision);
    sessionStorage.setItem("creditmind_officer", officer);
    try {
      await api(`/applications/${encodeURIComponent(applicationId)}/review`, {
        method: "POST",
        body: { decision, officer_id: officer, note },
      });
      for (let i = 0; i < 90; i++) {
        await sleep(2000);
        const row = await api(`/applications/${encodeURIComponent(applicationId)}`);
        if (row.status === "COMPLETED" || row.status === "ERROR") {
          onDone?.(row);
          return;
        }
      }
      setErrors(["The memo is still being written. Refresh in a moment."]);
    } catch (error) {
      setErrors(errorLines(error));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-[12rem_1fr]">
        <label className="text-sm">
          <span className="mb-1 block text-muted">Officer ID</span>
          <input
            value={officer}
            onChange={(e) => setOfficer(e.target.value)}
            className="w-full rounded-md border border-rule bg-white px-3 py-2"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-muted">Justification for the audit trail</span>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            placeholder="Required when approving: explain why the referral reasons are acceptable."
            className="w-full rounded-md border border-rule bg-white px-3 py-2"
          />
        </label>
      </div>
      <ErrorNote lines={errors} />
      <div className="flex gap-3">
        <Button variant="approve" disabled={!!busy} onClick={() => decide("APPROVE")}>
          {busy === "APPROVE" ? "Approving..." : "Approve"}
        </Button>
        <Button variant="decline" disabled={!!busy} onClick={() => decide("DECLINE")}>
          {busy === "DECLINE" ? "Declining..." : "Decline"}
        </Button>
      </div>
    </div>
  );
}