import { useEffect, useState } from "react";
import { CopySimple, Database } from "@phosphor-icons/react";
import { api } from "../api";

function useCopy() {
  const [copied, setCopied] = useState("");
  return {
    copied,
    copy: (text: string) => {
      navigator.clipboard
        .writeText(text)
        .then(() => {
          setCopied(text);
          setTimeout(() => setCopied(""), 1500);
        })
        .catch(() => undefined);
    },
  };
}

function flatten(obj: unknown, prefix = ""): { key: string; value: string }[] {
  const rows: { key: string; value: string }[] = [];
  if (obj === null || obj === undefined) return rows;
  if (Array.isArray(obj)) {
    obj.forEach((v, i) => rows.push(...flatten(v, `${prefix}[${i}]`)));
  } else if (typeof obj === "object") {
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      rows.push(...flatten(v, prefix ? `${prefix}.${k}` : k));
    }
  } else {
    rows.push({ key: prefix, value: String(obj) });
  }
  return rows;
}

export function Settings() {
  const [rows, setRows] = useState<{ key: string; value: string }[]>([]);
  const [err, setErr] = useState("");
  const [bak, setBak] = useState("");
  const [busy, setBusy] = useState(false);
  const { copy, copied } = useCopy();

  useEffect(() => {
    api<Record<string, unknown>>("/api/config")
      .then((cfg) => setRows(flatten(cfg)))
      .catch((e) => setErr(e instanceof Error ? e.message : "加载失败"));
  }, []);

  async function doBackup() {
    setBusy(true);
    setBak("");
    try {
      const r = await api<{ ok: boolean; file: string; size_mb: number }>("/api/backup", {
        method: "POST",
      });
      setBak(`备份完成：${r.file}（${r.size_mb} MB）`);
    } catch (e) {
      setBak(e instanceof Error ? e.message : "备份失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">设置</h1>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-medium text-zinc-300">数据备份</h2>
            <p className="mt-0.5 text-xs text-zinc-500">
              打包数据库与工作区到 data/backups/，与 kuro backup 命令等效。
            </p>
          </div>
          <button
            onClick={doBackup}
            disabled={busy}
            className="flex items-center gap-2 rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-medium text-zinc-950 transition hover:brightness-110 active:scale-[0.98] disabled:opacity-40"
          >
            <Database size={16} weight="bold" />
            {busy ? "备份中…" : "立即备份"}
          </button>
        </div>
        {bak && <p className="text-sm text-zinc-300">{bak}</p>}
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
        <h2 className="mb-4 text-sm font-medium text-zinc-300">当前配置（密钥已脱敏）</h2>
        {err && <p className="text-sm text-rose-400">{err}</p>}
        {rows.length === 0 && !err && <p className="text-sm text-zinc-500">加载中…</p>}
        <div className="space-y-1.5">
          {rows.map((r) => (
            <div
              key={r.key}
              className="group flex items-center gap-3 rounded-lg px-2 py-1.5 text-sm transition hover:bg-zinc-800/60"
            >
              <span className="w-72 shrink-0 truncate font-mono text-xs text-zinc-500">{r.key}</span>
              <span className="flex-1 truncate font-mono text-xs text-zinc-200">{r.value}</span>
              <button
                onClick={() => copy(r.value)}
                className="shrink-0 text-zinc-600 opacity-0 transition group-hover:opacity-100 hover:text-zinc-300"
                title="复制"
              >
                {copied === r.value ? (
                  <span className="text-xs text-[var(--accent)]">已复制</span>
                ) : (
                  <CopySimple size={14} />
                )}
              </button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
