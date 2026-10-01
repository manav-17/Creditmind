import { useEffect, useState } from "react";
import { api, getToken, setToken } from "./api";
import Applications from "./pages/Applications";
import Detail from "./pages/Detail";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Review from "./pages/Review";
import Submit from "./pages/Submit";

function useRoute() {
  const read = () => window.location.hash.replace(/^#/, "") || "/";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => {
      setRoute(read());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

const NAV = [
  { to: "/", label: "Overview" },
  { to: "/review", label: "Review queue" },
  { to: "/applications", label: "Applications" },
  { to: "/submit", label: "New application" },
];

function Shell({ route, onLogout, children }) {
  const [pending, setPending] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api("/stats")
        .then((s) => alive && setPending(s.pending_reviews))
        .catch(() => {});
    load();
    const timer = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [route]);

  const active = (to) => (to === "/" ? route === "/" : route.startsWith(to));

  return (
    <div className="min-h-screen md:grid md:grid-cols-[15rem_1fr]">
      <aside className="border-b border-rule bg-white md:sticky md:top-0 md:h-screen md:border-r md:border-b-0">
        <div className="flex items-center justify-between px-5 py-5 md:block">
          <div>
            <a href="#/" className="text-lg font-semibold tracking-tight">
              CreditMind
            </a>
            <p className="text-sm text-muted">Underwriting desk</p>
          </div>
          <button onClick={onLogout} className="text-sm text-muted hover:text-ink md:hidden">
            Sign out
          </button>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:pb-0" aria-label="Main">
          {NAV.map((item) => (
            <a
              key={item.to}
              href={`#${item.to}`}
              aria-current={active(item.to) ? "page" : undefined}
              className={`flex items-center justify-between whitespace-nowrap rounded-md px-3 py-2 text-sm ${
                active(item.to) ? "bg-panel font-medium text-ink" : "text-muted hover:text-ink"
              }`}
            >
              {item.label}
              {item.to === "/review" && pending > 0 && (
                <span className="ml-3 rounded bg-refer px-1.5 text-xs font-medium text-white">{pending}</span>
              )}
            </a>
          ))}
        </nav>
        <div className="absolute bottom-5 hidden px-5 md:block">
          <button onClick={onLogout} className="text-sm text-muted hover:text-ink">
            Sign out
          </button>
        </div>
      </aside>
      <main className="mx-auto w-full max-w-6xl px-5 py-8 md:px-10">{children}</main>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(Boolean(getToken()));
  const route = useRoute();

  useEffect(() => {
    const expired = () => setAuthed(false);
    window.addEventListener("auth:expired", expired);
    return () => window.removeEventListener("auth:expired", expired);
  }, []);

  if (!authed) return <Login onSuccess={() => setAuthed(true)} />;

  const detail = route.match(/^\/applications\/(.+)$/);
  let page = <Overview />;
  if (detail) page = <Detail id={decodeURIComponent(detail[1])} />;
  else if (route === "/applications") page = <Applications />;
  else if (route === "/review") page = <Review />;
  else if (route === "/submit") page = <Submit />;

  return (
    <Shell
      route={route}
      onLogout={() => {
        setToken(null);
        setAuthed(false);
      }}
    >
      {page}
    </Shell>
  );
}