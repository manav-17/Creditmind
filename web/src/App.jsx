import { useEffect, useState } from "react";
import { api, getToken, setToken } from "./api";
import Applications from "./pages/Applications";
import Detail from "./pages/Detail";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Review from "./pages/Review";
import Submit from "./pages/Submit";
import { Icon, Mark } from "./components/ui";

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
  { to: "/", label: "Overview", icon: "overview" },
  { to: "/review", label: "Review queue", icon: "queue" },
  { to: "/applications", label: "Applications", icon: "list" },
  { to: "/submit", label: "New application", icon: "plus" },
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
    <div className="min-h-screen md:grid md:grid-cols-[16rem_1fr]">
      <aside className="bg-marine text-white md:sticky md:top-0 md:flex md:h-screen md:flex-col">
        <div className="flex items-center justify-between px-5 py-5 md:py-7">
          <a href="#/" className="flex items-center gap-3">
            <Mark />
            <span>
              <span className="block text-lg font-semibold leading-tight tracking-tight">CreditMind</span>
              <span className="block text-sm text-white/55">Underwriting desk</span>
            </span>
          </a>
          <button onClick={onLogout} className="text-sm text-white/60 hover:text-white md:hidden">
            Sign out
          </button>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-1 md:flex-col md:pb-0" aria-label="Main">
          {NAV.map((item) => (
            <a
              key={item.to}
              href={`#${item.to}`}
              aria-current={active(item.to) ? "page" : undefined}
              className={`relative flex items-center gap-3 whitespace-nowrap rounded-lg px-3 py-2.5 text-sm transition-colors ${
                active(item.to)
                  ? "bg-white/10 font-medium text-white"
                  : "text-white/65 hover:bg-white/5 hover:text-white"
              }`}
            >
              {active(item.to) && <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-[#6f95f5]" />}
              <Icon name={item.icon} className="h-[18px] w-[18px]" />
              <span className="flex-1">{item.label}</span>
              {item.to === "/review" && pending > 0 && (
                <span className="rounded-full bg-refer px-2 text-xs font-semibold text-white">{pending}</span>
              )}
            </a>
          ))}
        </nav>
        <div className="hidden border-t border-white/10 px-3 py-4 md:block">
          <button
            onClick={onLogout}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-white/65 transition-colors hover:bg-white/5 hover:text-white"
          >
            <Icon name="logout" className="h-[18px] w-[18px]" />
            Sign out
          </button>
        </div>
      </aside>
      <main className="mx-auto w-full max-w-6xl px-5 py-8 md:px-10 md:py-12">{children}</main>
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