import { useEffect, useState } from "react";
import { CopySimple, Database, FloppyDisk } from "@phosphor-icons/react";
import { api } from "../api";

type Row = { key: string; value: string };

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

// 面板可编辑的配置分组（中文标签 → dot 路径 + 是否密钥）
const GROUPS: {
  title: string;
  hint: string;
  fields: { label: string; key: string; secret?: boolean; placeholder?: string }[];
}[] = [
  {
    title: "QQ 机器人",
    hint: "QQ 开放平台机器人的凭据",
    fields: [
      { label: "AppID", key: "channel.app_id" },
      { label: "AppSecret", key: "channel.secret", secret: true },
    ],
  },
  {
    title: "文本模型（必填）",
    hint: "任意 OpenAI 兼容服务，如 GLM / DeepSeek / 通义",
    fields: [
      { label: "服务商 provider", key: "models.llm.provider", placeholder: "openai" },
      { label: "模型名", key: "models.llm.model", placeholder: "glm-5.3-flash" },
      {
        label: "接口地址 base_url",
        key: "models.llm.base_url",
        placeholder: "https://open.bigmodel.cn/api/paas/v4",
      },
      { label: "API 密钥", key: "models.llm.api_key", secret: true },
    ],
  },
  {
    title: "视觉模型（可选）",
    hint: "不填时自动使用主模型看图",
    fields: [
      { label: "模型名", key: "models.vision.model", placeholder: "留空使用主模型" },
      { label: "接口地址", key: "models.vision.base_url" },
      { label: "API 密钥", key: "models.vision.api_key", secret: true },
    ],
  },
  {
    title: "嵌入模型（可选）",
    hint: "配置后知识库走语义检索",
    fields: [
      { label: "模型名", key: "models.embedding.model", placeholder: "如 embedding-3" },
      { label: "接口地址", key: "models.embedding.base_url" },
      { label: "API 密钥", key: "models.embedding.api_key", secret: true },
    ],
  },
  {
    title: "搜索与题库（可选）",
    hint: "出题时找真题用",
    fields: [
      { label: "搜索服务商", key: "models.search.provider", placeholder: "bing / tavily" },
      { label: "搜索密钥", key: "models.search.api_key", secret: true },
      { label: "火花题库密钥", key: "models.qbank.api_key", secret: true },
    ],
  },
  {
    title: "互动课堂（可选）",
    hint: "OpenMAIC 托管站访问码",
    fields: [{ label: "访问码", key: "openmaic.access_code", secret: true }],
  },
  {
    title: "面板与数据",
    hint: "修改口令后需重新登录",
    fields: [
      { label: "面板访问口令", key: "webui.token", secret: true },
      { label: "消息保留天数", key: "retention.message_days", placeholder: "180" },
      { label: "任务保留天数", key: "retention.task_days", placeholder: "90" },
    ],
  },
];

function FieldRow({
  label,
  cfgKey,
  secret,
  placeholder,
  current,
  onSaved,
}: {
  label: string;
  cfgKey: string;
  secret?: boolean;
  placeholder?: string;
  current: Row[];
  onSaved: () => void;
}) {
  const [val, setVal] = useState("");
  const [state, setState] = useState("");
  const cur = current.find((r) => r.key === cfgKey)?.value ?? "";
  const display = secret ? (cur && cur !== "None" ? "已设置" : "未设置") : cur;

  async function save() {
    setState("保存中…");
    try {
      await api("/api/config/set", { method: "POST", body: JSON.stringify({ key: cfgKey, value: val }) });
      setState("已保存 ✓");
      onSaved();
      setVal("");
      setTimeout(() => setState(""), 2000);
    } catch (e) {
      setState(e instanceof Error ? e.message : "保存失败");
    }
  }

  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="w-40 shrink-0 text-sm">{label}</span>
      <input
        type={secret ? "password" : "text"}
        value={val}
        onChange={(e) => setVal(e.target.value)}
        placeholder={secret ? `${display}，留空保持不变` : display || placeholder || ""}
        className="min-w-0 flex-1 rounded-lg border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-1.5 text-sm outline-none focus:border-[var(--accent)]"
      />
      <button
        onClick={save}
        disabled={!val}
        className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm transition hover:border-[var(--accent)] active:scale-[0.98] disabled:opacity-30"
      >
        <FloppyDisk size={14} />
        保存
      </button>
      {state && <span className="w-28 shrink-0 text-xs text-[var(--muted)]">{state}</span>}
    </div>
  );
}

export function Settings() {
  const [masked, setMasked] = useState<Row[]>([]);
  const [err, setErr] = useState("");
  const [bak, setBak] = useState("");
  const [busy, setBusy] = useState(false);
  const { copy, copied } = useCopy();

  function reload() {
    api<Record<string, unknown>>("/api/config")
      .then((cfg) => {
        const rows: Row[] = [];
        const walk = (obj: unknown, prefix = "") => {
          if (obj === null || obj === undefined) return;
          if (Array.isArray(obj)) {
            obj.forEach((v, i) => walk(v, `${prefix}[${i}]`));
          } else if (typeof obj === "object") {
            for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
              walk(v, prefix ? `${prefix}.${k}` : k);
            }
          } else {
            rows.push({ key: prefix, value: String(obj) });
          }
        };
        walk(cfg);
        setMasked(rows);
      })
      .catch((e) => setErr(e instanceof Error ? e.message : "加载失败"));
  }

  useEffect(reload, []);

  async function doBackup() {
    setBusy(true);
    setBak("");
    try {
      const r = await api<{ ok: boolean; file: string; size_mb: number }>("/api/backup", {
        method: "POST",
      });
      setBak(`备份完成：${r.file}（${r.size_mb} MB），存在服务器 data/backups/ 目录`);
    } catch (e) {
      setBak(e instanceof Error ? e.message : "备份失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">设置</h1>

      <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-medium">数据备份</h2>
            <p className="mt-0.5 text-xs text-[var(--muted)]">
              打包学生数据（数据库/工作区/知识库）为压缩包，存在服务器 data/backups/ 目录。
            </p>
          </div>
          <button
            onClick={doBackup}
            disabled={busy}
            className="flex items-center gap-2 rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-medium text-[var(--accent-text)] transition hover:brightness-110 active:scale-[0.98] disabled:opacity-40"
          >
            <Database size={16} weight="bold" />
            {busy ? "备份中…" : "立即备份"}
          </button>
        </div>
        {bak && <p className="text-sm">{bak}</p>}
      </section>

      <p className="text-sm text-[var(--muted)]">
        以下设置修改后立即写入配置文件（部分需重启服务生效）。密钥输入框留空表示保持不变。
      </p>

      {GROUPS.map((g) => (
        <section key={g.title} className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6">
          <h2 className="text-sm font-medium">{g.title}</h2>
          <p className="mb-3 mt-0.5 text-xs text-[var(--muted)]">{g.hint}</p>
          <div className="divide-y divide-[var(--border)]">
            {g.fields.map((f) => (
              <FieldRow
                key={f.key}
                label={f.label}
                cfgKey={f.key}
                secret={f.secret}
                placeholder={f.placeholder}
                current={masked}
                onSaved={reload}
              />
            ))}
          </div>
        </section>
      ))}

      <details className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-6">
        <summary className="cursor-pointer text-sm font-medium">查看完整配置（密钥已脱敏）</summary>
        <div className="mt-4 space-y-1">
          {masked.map((r) => (
            <div key={r.key} className="group flex items-center gap-3 rounded-lg px-2 py-1 text-xs">
              <span className="w-72 shrink-0 truncate font-mono text-[var(--muted)]">{r.key}</span>
              <span className="flex-1 truncate font-mono">{r.value}</span>
              <button
                onClick={() => copy(r.value)}
                className="shrink-0 text-[var(--muted)] opacity-0 transition group-hover:opacity-100"
                title="复制"
              >
                {copied === r.value ? <span className="text-[var(--accent)]">已复制</span> : <CopySimple size={13} />}
              </button>
            </div>
          ))}
        </div>
      </details>
      {err && <p className="text-sm text-rose-400">{err}</p>}
    </div>
  );
}
