import { useEffect, useState } from "react";
import { api, type Overview } from "../api";

interface KbData {
  cards: { id: number; student: string; subject: string; question_type: string; method: string; steps: string }[];
  corpus: { id: number; student: string; title: string; subject: string; content: string }[];
}

export function KnowledgeBase() {
  const [data, setData] = useState<KbData | null>(null);
  const [students, setStudents] = useState<{ id: number; nickname: string }[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [tab, setTab] = useState<"cards" | "corpus">("cards");
  const [err, setErr] = useState("");

  useEffect(() => {
    api<Overview>("/api/overview").then((o) => setStudents(o.by_student)).catch(() => undefined);
  }, []);

  useEffect(() => {
    const q = sel ? `?student_id=${sel}` : "";
    api<KbData>(`/api/kb${q}`).then(setData).catch((e) => setErr(e instanceof Error ? e.message : "加载失败"));
  }, [sel]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">知识库</h1>

      <div className="flex gap-2 overflow-x-auto pb-1">
        <button
          onClick={() => setSel(null)}
          className={`shrink-0 rounded-lg border px-4 py-2 text-sm transition ${
            sel === null
              ? "border-[var(--accent)] bg-[var(--surface-soft)] font-medium"
              : "border-[var(--border)] text-[var(--muted)] hover:border-[var(--accent)]"
          }`}
        >
          全部学生
        </button>
        {students.map((s) => (
          <button
            key={s.id}
            onClick={() => setSel(s.id)}
            className={`shrink-0 rounded-lg border px-4 py-2 text-sm transition ${
              sel === s.id
                ? "border-[var(--accent)] bg-[var(--surface-soft)] font-medium"
                : "border-[var(--border)] text-[var(--muted)] hover:border-[var(--accent)]"
            }`}
          >
            {s.nickname}
          </button>
        ))}
      </div>

      {err && <p className="text-sm text-rose-400">{err}</p>}
      {!data && !err && <p className="text-sm text-[var(--muted)]">加载中…</p>}

      {data && (
        <>
          <div className="flex gap-2">
            <button
              onClick={() => setTab("cards")}
              className={`rounded-lg px-4 py-1.5 text-sm transition ${
                tab === "cards" ? "bg-[var(--surface-soft)] font-medium" : "text-[var(--muted)]"
              }`}
            >
              方法卡片（{data.cards.length}）
            </button>
            <button
              onClick={() => setTab("corpus")}
              className={`rounded-lg px-4 py-1.5 text-sm transition ${
                tab === "corpus" ? "bg-[var(--surface-soft)] font-medium" : "text-[var(--muted)]"
              }`}
            >
              语料库（{data.corpus.length}）
            </button>
          </div>

          {tab === "cards" && (
            <div className="space-y-3">
              {data.cards.length === 0 ? (
                <p className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6 text-sm text-[var(--muted)]">
                  还没有方法卡片。老师解题后会自动沉淀。
                </p>
              ) : (
                data.cards.map((c) => (
                  <div key={c.id} className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5">
                    <div className="mb-2 flex items-center gap-3">
                      <span className="rounded-md bg-[var(--surface-soft)] px-2 py-0.5 text-xs text-[var(--muted)]">
                        {c.subject}
                      </span>
                      <span className="text-sm font-medium">{c.question_type}</span>
                      <span className="ml-auto text-xs text-[var(--muted)]">{c.student}</span>
                    </div>
                    <p className="text-sm text-[var(--text)]">{c.method}</p>
                    {c.steps && <p className="mt-1.5 text-xs text-[var(--muted)]">{c.steps}</p>}
                  </div>
                ))
              )}
            </div>
          )}

          {tab === "corpus" && (
            <div className="space-y-3">
              {data.corpus.length === 0 ? (
                <p className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6 text-sm text-[var(--muted)]">
                  语料库是空的。可以对机器人说「把这段存起来」来录入。
                </p>
              ) : (
                data.corpus.map((e) => (
                  <div key={e.id} className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5">
                    <div className="mb-2 flex items-center gap-3">
                      <span className="rounded-md bg-[var(--surface-soft)] px-2 py-0.5 text-xs text-[var(--muted)]">
                        {e.subject}
                      </span>
                      <span className="text-sm font-medium">{e.title}</span>
                      <span className="ml-auto text-xs text-[var(--muted)]">{e.student}</span>
                    </div>
                    <p className="text-sm text-[var(--muted)]">{e.content}</p>
                  </div>
                ))
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
