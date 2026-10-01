import { useState } from "react";
import { api, errorLines, setToken } from "../api";
import { Button, ErrorNote } from "../components/ui";

export default function Login({ onSuccess }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState([]);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setErrors([]);
    try {
      const { token } = await api("/auth/login", { method: "POST", body: { password } });
      setToken(token);
      onSuccess();
    } catch (error) {
      setErrors(error.status === 401 ? ["That password is incorrect."] : errorLines(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-5">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-semibold tracking-tight">CreditMind</h1>
        <p className="mt-1 text-muted">
          Sign in to review loan decisions made by the underwriting agents.
        </p>
        <form onSubmit={submit} className="mt-8 space-y-4">
          <label className="block text-sm">
            <span className="mb-1 block text-muted">Officer password</span>
            <input
              type="password"
              autoFocus
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-md border border-rule bg-white px-3 py-2"
            />
          </label>
          <ErrorNote lines={errors} />
          <Button type="submit" disabled={busy} className="w-full">
            {busy ? "Signing in..." : "Sign in"}
          </Button>
        </form>
      </div>
    </div>
  );
}