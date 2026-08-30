import { useEffect, useState } from "react";
import { api, STAGE_CN, COURSE_STATUS_CN, type Overview, type StudentDetail } from "../api";

const STATUS_CN: Record<string, string> = {
  to_review: "待复习",
  reviewing: "复习中",
  mastered: "已掌握",
  archived: "已归档",
};

export function Students() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [sel, setSel] = useState<StudentDetail | null>(null);

  useEffect(() => {
    api<Overview>("/api/overview").then(setOv).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!ov) return;
    const first = ov.by_student[0];
    if (first) {
      api<StudentDetail>(`/api/students/${first.id}`)
        .then(setSel)
        .catch(() => undefined);
    }
  }, [ov]);

  if (!ov) return <p className="text-sm text-[var(--muted)]">加载中…</p>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">学生</h1>

      {ov.by_student.length === 0 ? (
        <p className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6 text-sm text-[var(--muted)]">
          还没有学生。学生在 QQ 私聊互动后自动建档。
        </p>
      ) : (
        <>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {ov.by_student.map((s) => (
              <button
                key={s.id}
                onClick={() =>
                  api<StudentDetail>(`/api/students/${s.id}`)
                    .then(setSel)
                    .catch(() => undefined)
                }
                className={`shrink-0 rounded-lg border px-4 py-2 text-sm transition ${
                  sel?.id === s.id
                    ? "border-[var(--accent)] bg-[var(--surface-soft)] font-medium"
                    : "border-[var(--border)] text-[var(--muted)] hover:border-[var(--border)]"
                }`}
              >
                {s.nickname}
              </button>
            ))}
          </div>

          {sel && (
            <div className="space-y-6">
              <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
                <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
                  <p className="text-sm text-[var(--muted)]">平均掌握度</p>
                  <p className="mt-1 font-mono text-2xl tabular-nums">
                    {sel.effect.mastery_now != null
                      ? `${Math.round(sel.effect.mastery_now * 100)}%`
                      : "—"}
                    {sel.effect.mastery_delta != null && (
                      <span
                        className={`ml-2 text-sm ${
                          sel.effect.mastery_delta >= 0 ? "text-[var(--accent)]" : "text-rose-400"
                        }`}
                      >
                        {sel.effect.mastery_delta >= 0 ? "+" : ""}
                        {Math.round(sel.effect.mastery_delta * 100)}%
                      </span>
                    )}
                  </p>
                </div>
                <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
                  <p className="text-sm text-[var(--muted)]">复习通过率（7 天）</p>
                  <p className="mt-1 font-mono text-2xl tabular-nums">
                    {sel.effect.review_pass_rate != null
                      ? `${Math.round(sel.effect.review_pass_rate * 100)}%`
                      : "—"}
                  </p>
                </div>
                <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
                  <p className="text-sm text-[var(--muted)]">待复习</p>
                  <p className="mt-1 font-mono text-2xl tabular-nums">{sel.effect.due_count}</p>
                </div>
              </section>

              <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6">
                <h2 className="mb-4 text-sm font-medium text-[var(--muted)]">知识点掌握（低 → 高）</h2>
                {sel.mastery.length === 0 ? (
                  <p className="text-sm text-[var(--muted)]">还没有画像数据。</p>
                ) : (
                  <div className="space-y-2.5">
                    {sel.mastery.map((k) => (
                      <div key={`${k.subject}-${k.name}`} className="flex items-center gap-3">
                        <span className="w-40 shrink-0 truncate text-sm text-[var(--text)]">{k.name}</span>
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--surface-soft)]">
                          <div
                            className="h-full rounded-full bg-[var(--accent)]"
                            style={{ width: `${Math.round(k.mastery * 100)}%` }}
                          />
                        </div>
                        <span className="w-12 text-right font-mono text-xs tabular-nums text-[var(--muted)]">
                          {Math.round(k.mastery * 100)}%
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6">
                <h2 className="mb-4 text-sm font-medium text-[var(--muted)]">错题（最近 100 条）</h2>
                {sel.wrongs.length === 0 ? (
                  <p className="text-sm text-[var(--muted)]">错题本是空的。</p>
                ) : (
                  <div className="divide-y divide-[var(--border)]">
                    {sel.wrongs.slice(0, 15).map((w) => (
                      <div key={w.id} className="flex items-center gap-3 py-2.5 text-sm">
                        <span className="w-14 shrink-0 text-[var(--muted)]">{w.subject}</span>
                        <span className="flex-1 truncate text-[var(--text)]">{w.question}</span>
                        <span className="shrink-0 rounded-md bg-[var(--surface-soft)] px-2 py-0.5 text-xs text-[var(--muted)]">
                          {w.error_type}
                        </span>
                        <span className="w-16 shrink-0 text-right text-xs text-[var(--muted)]">
                          {STATUS_CN[w.status] ?? w.status}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              {sel.courses.length > 0 && (
                <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6">
                  <h2 className="mb-4 text-sm font-medium text-[var(--muted)]">最近课程</h2>
                  <div className="divide-y divide-[var(--border)]">
                    {sel.courses.slice(0, 8).map((c) => (
                      <div key={c.id} className="flex items-center gap-3 py-2.5 text-sm">
                        <span className="flex-1 truncate text-[var(--text)]">{c.title}</span>
                        <span className="font-mono text-xs tabular-nums text-[var(--muted)]">
                          {c.start.replace("T", " ").slice(0, 16)}
                        </span>
                        <span className="shrink-0 text-xs text-[var(--muted)]">{COURSE_STATUS_CN[c.status] ?? c.status}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
