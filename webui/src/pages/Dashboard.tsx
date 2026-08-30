import { useEffect, useState } from "react";
import { ListChecks, Notebook, GraduationCap, CheckCircle } from "@phosphor-icons/react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { api, STAGE_CN, type Overview, type StudentDetail } from "../api";

function Stat({ label, value, icon: Icon }: { label: string; value: number | string; icon: typeof ListChecks }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="flex items-center justify-between">
        <span className="text-sm text-[var(--muted)]">{label}</span>
        <Icon size={20} className="text-[var(--accent)]" weight="duotone" />
      </div>
      <p className="mt-2 font-mono text-3xl font-semibold tabular-nums tracking-tight">{value}</p>
    </div>
  );
}

export function Dashboard() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [detail, setDetail] = useState<StudentDetail | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api<Overview>("/api/overview")
      .then(setOv)
      .catch((e) => setErr(e instanceof Error ? e.message : "加载失败"));
  }, []);

  useEffect(() => {
    if (ov && ov.by_student.length > 0) {
      api<StudentDetail>(`/api/students/${ov.by_student[0].id}`)
        .then(setDetail)
        .catch(() => undefined);
    }
  }, [ov]);

  if (err) return <p className="text-sm text-rose-400">{err}</p>;
  if (!ov) return <p className="text-sm text-[var(--muted)]">加载中…</p>;

  const trend = detail?.effect?.trend ?? [];

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold tracking-tight">仪表盘</h1>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="学生总数" value={ov.students_total} icon={GraduationCap} />
        <Stat label="待攻克错题" value={ov.wrong_open} icon={Notebook} />
        <Stat label="已掌握错题" value={ov.wrong_mastered} icon={CheckCircle} />
        <Stat label="今日打卡" value={ov.checkin_today} icon={ListChecks} />
      </div>

      {trend.length >= 2 && (
        <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6">
          <h2 className="mb-4 text-sm font-medium text-[var(--muted)]">掌握度趋势 · {detail?.nickname}</h2>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={trend}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "var(--muted, #a1a1aa)", fontSize: 12 }} tickLine={false} axisLine={false} />
              <YAxis
                domain={[0, 1]}
                tick={{ fill: "var(--muted, #a1a1aa)", fontSize: 12 }}
                tickLine={false}
                axisLine={false}
                width={40}
              />
              <Tooltip
                contentStyle={{ background: "var(--surface-soft, #18181b)", border: "1px solid var(--border, #3f3f46)", borderRadius: 8 }}
                labelStyle={{ color: "var(--muted, #a1a1aa)" }}
              />
              <Line type="monotone" dataKey="avg_mastery" stroke="var(--accent)" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </section>
      )}

      <section>
        <h2 className="mb-3 text-sm font-medium text-[var(--muted)]">学生概览</h2>
        {ov.by_student.length === 0 ? (
          <p className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6 text-sm text-[var(--muted)]">
            还没有学生。学生在 QQ 私聊互动后自动建档。
          </p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {ov.by_student.map((s) => (
              <div key={s.id} className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{s.nickname}</span>
                  <span className="text-xs text-[var(--muted)]">{STAGE_CN[s.stage] ?? s.stage}</span>
                </div>
                <div className="mt-2 flex gap-6 font-mono text-sm tabular-nums text-[var(--text)]">
                  <span>掌握度 {s.avg_mastery != null ? `${Math.round(s.avg_mastery * 100)}%` : "—"}</span>
                  <span>待复习 {s.due_count ?? "—"}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
