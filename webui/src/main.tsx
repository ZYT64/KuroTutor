import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  GraduationCap,
  SquaresFour,
  Notebook,
  CalendarBlank,
  GearSix,
  SignOut,
  ChartLineUp,
} from "@phosphor-icons/react";
import { api } from "./api";
import { Dashboard } from "./pages/Dashboard";
import { Students } from "./pages/Students";
import { Mistakes, Schedule } from "./pages/Mistakes";
import { Settings } from "./pages/Settings";
import "./index.css";

type Page = "dashboard" | "students" | "mistakes" | "schedule" | "settings";

const NAV: { key: Page; label: string; icon: typeof SquaresFour }[] = [
  { key: "dashboard", label: "仪表盘", icon: ChartLineUp },
  { key: "students", label: "学生", icon: GraduationCap },
  { key: "mistakes", label: "错题本", icon: Notebook },
  { key: "schedule", label: "排课", icon: CalendarBlank },
  { key: "settings", label: "设置", icon: GearSix },
];

function Login({ onOk }: { onOk: () => void }) {
  const [token, setToken] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api("/api/login", { method: "POST", body: JSON.stringify({ token }) });
      onOk();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-[100dvh] items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-xl border border-zinc-800 bg-zinc-900 p-8">
        <div className="mb-6 flex items-center gap-2">
          <GraduationCap size={28} className="text-[var(--accent)]" weight="duotone" />
          <span className="text-xl font-semibold tracking-tight">KuroTutor 面板</span>
        </div>
        <label className="mb-1 block text-sm text-zinc-400" htmlFor="token">
          访问口令
        </label>
        <input
          id="token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          autoFocus
          className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
          placeholder="kuro.json 中 webui.token 的值"
        />
        {err && <p className="mt-2 text-sm text-rose-400">{err}</p>}
        <button
          type="submit"
          disabled={busy || !token}
          className="mt-5 w-full rounded-lg bg-[var(--accent)] py-2 text-sm font-medium text-zinc-950 transition hover:brightness-110 active:scale-[0.98] disabled:opacity-40"
        >
          {busy ? "登录中…" : "进入面板"}
        </button>
      </form>
    </div>
  );
}

export function App() {
  const [authed, setAuthed] = useState(() => !!document.cookie.includes("kuro_webui="));
  const [page, setPage] = useState<Page>("dashboard");

  useEffect(() => {
    const onUnauthorized = () => setAuthed(false);
    window.addEventListener("kuro:unauthorized", onUnauthorized);
    return () => window.removeEventListener("kuro:unauthorized", onUnauthorized);
  }, []);

  if (!authed) return <Login onOk={() => setAuthed(true)} />;

  return (
    <div className="flex min-h-[100dvh]">
      <aside className="flex w-56 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/50 px-3 py-5">
        <div className="mb-8 flex items-center gap-2 px-2">
          <GraduationCap size={24} className="text-[var(--accent)]" weight="duotone" />
          <span className="font-semibold tracking-tight">KuroTutor</span>
        </div>
        <nav className="flex flex-1 flex-col gap-1">
          {NAV.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setPage(key)}
              className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${
                page === key
                  ? "bg-zinc-800 font-medium text-zinc-50"
                  : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
              }`}
            >
              <Icon size={18} weight={page === key ? "fill" : "regular"} />
              {label}
            </button>
          ))}
        </nav>
        <button
          onClick={async () => {
            await api("/api/logout", { method: "POST" }).catch(() => undefined);
            setAuthed(false);
          }}
          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-zinc-500 transition hover:bg-zinc-900 hover:text-zinc-300"
        >
          <SignOut size={18} />
          退出
        </button>
      </aside>
      <main className="mx-auto w-full max-w-6xl px-8 py-8">
        {page === "dashboard" && <Dashboard />}
        {page === "students" && <Students />}
        {page === "mistakes" && <Mistakes />}
        {page === "schedule" && <Schedule />}
        {page === "settings" && <Settings />}
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
