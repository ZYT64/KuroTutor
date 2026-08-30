import { useEffect, useState } from "react";
import { api, TASK_KIND_CN, type ScheduleItem } from "../api";

interface Mistake {
  id: number;
  student_id: number;
  subject: string;
  question: string;
  error_type: string;
  status: string;
}

const STATUS_CN: Record<string, string> = {
  to_review: "待复习",
  reviewing: "复习中",
  mastered: "已掌握",
  archived: "已归档",
};

export function Mistakes() {
  const [rows, setRows] = useState<Mistake[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    api<Mistake[]>("/api/mistakes")
      .then(setRows)
      .catch((e) => setErr(e instanceof Error ? e.message : "加载失败"));
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">错题本</h1>
      {err && <p className="text-sm text-rose-400">{err}</p>}
      {rows.length === 0 ? (
        <p className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6 text-sm text-[var(--muted)]">
          全库还没有错题记录。
        </p>
      ) : (
        <div className="overflow-hidden rounded-xl border border-[var(--border)]">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] bg-[var(--surface-soft)] text-left text-xs text-[var(--muted)]">
                <th className="px-4 py-2.5 font-medium">学生</th>
                <th className="px-4 py-2.5 font-medium">学科</th>
                <th className="px-4 py-2.5 font-medium">题目</th>
                <th className="px-4 py-2.5 font-medium">错因</th>
                <th className="px-4 py-2.5 font-medium">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {rows.map((w) => (
                <tr key={w.id} className="hover:bg-[var(--hover)]">
                  <td className="px-4 py-2.5 text-[var(--muted)]">#{w.student_id}</td>
                  <td className="px-4 py-2.5 text-[var(--muted)]">{w.subject}</td>
                  <td className="max-w-md truncate px-4 py-2.5 text-[var(--text)]">{w.question}</td>
                  <td className="px-4 py-2.5 text-[var(--muted)]">{w.error_type}</td>
                  <td className="px-4 py-2.5 text-[var(--muted)]">{STATUS_CN[w.status] ?? w.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function Schedule() {
  const [rows, setRows] = useState<ScheduleItem[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    api<ScheduleItem[]>("/api/schedule")
      .then(setRows)
      .catch((e) => setErr(e instanceof Error ? e.message : "加载失败"));
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">排课与定时任务</h1>
      {err && <p className="text-sm text-rose-400">{err}</p>}
      {rows.length === 0 ? (
        <p className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6 text-sm text-[var(--muted)]">
          没有排期中的任务。
        </p>
      ) : (
        <div className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--surface)]">
          {rows.map((t) => (
            <div key={t.id} className="flex items-center gap-4 px-5 py-3 text-sm">
              <span className="w-24 shrink-0 rounded-md bg-[var(--surface-soft)] px-2 py-0.5 text-center text-xs text-[var(--text)]">
                {TASK_KIND_CN[t.kind] ?? t.kind}
              </span>
              <span className="w-28 shrink-0 text-[var(--text)]">{t.student}</span>
              <span className="font-mono text-xs tabular-nums text-[var(--muted)]">
                {t.fire_at.replace("T", " ").slice(0, 16)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
