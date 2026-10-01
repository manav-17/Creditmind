import { useState } from "react";
import { api, errorLines, setToken } from "../api";
import { Button, ErrorNote, Mark } from "../components/ui";

const PIPELINE = [
  ["Guardrails", "Personal data is masked and prompt injection is removed before any model sees the text."],
  ["Risk model", "A calibrated gradient-boosted model scores the probability of default."],
  ["Fraud and policy", "Rules run in code. Language models only explain what the code decided."],
  ["Decision votes", "Three independent votes, a grounding check and a critic that can send it back."],
  ["Credit officer", "Referred cases wait for a person, who must justify any override."],
];

export default function Login({ onSuccess }) {
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState([]);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setErrors([]);
    try {
      const data = await api("/auth/login", { method: "POST", body: { password }, auth: false });
      setToken(data.token);
      onSuccess();
    } catch (error) {
      setErrors(errorLines(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen md:grid md:grid-cols-[minmax(0,5fr)_minmax(0,4fr)]">
      <section className="flex flex-col justify-between bg-marine px-8 py-10 text-white md:px-14 md:py-14">
        <div className="flex items-center gap-3">
          <Mark className="h-10 w-10" />
          <span className="text-xl font-semibold tracking-tight">CreditMind</span>
        </div>

        <div className="my-12 md:my-0">
          <h1 className="max-w-[16ch] font-serif text-5xl leading-[1.02] tracking-tight md:text-6xl">
            Credit decisions you can audit.
          </h1>
          <ol className="mt-10 max-w-md space-y-5 border-l border-white/15 pl-6">
            {PIPELINE.map(([title, text]) => (
              <li key={title} className="relative">
                <span className="absolute top-2 -left-[27px] h-1.5 w-1.5 rounded-full bg-[#6f95f5]" aria-hidden="true" />
                <p className="text-sm font-semibold">{title}</p>
                <p className="text-sm text-white/60">{text}</p>
              </li>
            ))}
          </ol>
        </div>

        <p className="hidden text-sm text-white/45 md:block">Demonstration system on public Lending Club data.</p>
      </section>

      <section className="flex items-center px-8 py-12 md:px-16">
        <form onSubmit={submit} className="w-full max-w-sm space-y-5">
          <div>
            <h2 className="font-serif text-4xl tracking-tight">Sign in</h2>
            <p className="mt-2 text-muted">Enter the desk password to open the underwriting workspace.</p>
          </div>
          <label className="block text-sm">
            <span className="mb-1.5 block font-medium">Password</span>
            <input
              type="password"
              autoComplete="current-password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-rule bg-white px-3.5 py-2.5 text-base"
            />
          </label>
          <ErrorNote lines={errors} />
          <Button type="submit" disabled={busy || !password} className="w-full">
            {busy ? "Signing in..." : "Sign in"}
          </Button>
        </form>
      </section>
    </main>
  );
}