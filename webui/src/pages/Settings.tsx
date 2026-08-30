import { useEffect, useState } from "react";
import { CloudArrowUp, CopySimple, Database, FloppyDisk } from "@phosphor-icons/react";
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
    title: "文档 OCR 识别链",
    hint: "扫描件文字识别按此顺序尝试，任一成功即止；百度/腾讯有每月免费额度，local 免费无限",
    fields: [
      { label: "识别链顺序", key: "ocr.chain", placeholder: "baidu,tencent,local（逗号分隔）" },
      { label: "百度 API Key", key: "ocr.baidu_api_key", secret: true },
      { label: "百度 Secret Key", key: "ocr.baidu_secret_key", secret: true },
      { label: "腾讯 SecretId", key: "ocr.tencent_secret_id", secret: true },
      { label: "腾讯 SecretKey", key: "ocr.tencent_secret_key", secret: true },
      { label: "MinerU 令牌", key: "ocr.mineru_token", secret: true },
    ],
  },
  {
    title: "互动课堂（可选）",
    hint: "OpenMAIC 托管站访问码",
    fields: [{ label: "访问码", key: "openmaic.access_code", secret: true }],
  },
  {
    title: "云备份（Gitee，可选）",
    hint: "加密后自动推送到你的 Gitee 私有仓库；仓库地址/令牌/口令都由你填写。留空 = 关闭云备份",
    fields: [
      {
        label: "仓库地址",
        key: "backup.gitee_repo",
        placeholder: "用户名/仓库名，如 zyt/kurotutor-backup",
      },
      { label: "Gitee 用户名", key: "backup.gitee_user", placeholder: "Gitee 登录名" },
      { label: "Gitee 私人令牌", key: "backup.gitee_token", secret: true },
      {
        label: "加密口令",
        key: "backup.encrypt_password",
        secret: true,
        placeholder: "自定；丢失后云端备份无法恢复",
      },
      { label: "自动备份开关", key: "backup.auto_enabled", placeholder: "true / false" },
      { label: "自动备份频率（天）", key: "backup.auto_interval_days", placeholder: "1 = 每天" },
    ],
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

interface CloudVersion {
  commit: string;
  date: string;
  message: string;
}

export function Settings() {
  const [masked, setMasked] = useState<Row[]>([]);
  const [err, setErr] = useState("");
  const [bak, setBak] = useState("");
  const [busy, setBusy] = useState<"local" | "cloud" | null>(null);
  const [versions, setVersions] = useState<CloudVersion[]>([]);
  const [verErr, setVerErr] = useState("");
  const [restoring, setRestoring] = useState("");
  const { copy, copied } = useCopy();

  function loadVersions() {
    api<{ ok: boolean; versions: CloudVersion[]; detail?: string }>("/api/backup/versions")
      .then((r) => {
        setVersions(r.versions ?? []);
        setVerErr(r.ok ? "" : r.detail ?? "");
      })
      .catch(() => setVerErr("云端版本获取失败（未配置或网络问题）"));
  }

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
  useEffect(loadVersions, []);

  async function doBackup(cloud: boolean) {
    setBusy(cloud ? "cloud" : "local");
    setBak("");
    try {
      const r = await api<{ ok: boolean; detail: string; file?: string; size_mb?: number }>(
        "/api/backup" + (cloud ? "?cloud=true" : ""),
        { method: "POST" },
      );
      setBak(r.ok ? r.detail : `备份失败：${r.detail}`);
    } catch (e) {
      setBak(e instanceof Error ? e.message : "备份失败");
    } finally {
      setBusy(null);
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
              本地备份存服务器 data/backups/；云端备份加密后推送到你的 Gitee 私有仓库（需在下方「云备份」分组配置）。
            </p>
          </div>
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => doBackup(false)}
            disabled={busy !== null}
            className="flex items-center gap-2 rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-medium text-[var(--accent-text)] transition hover:brightness-110 active:scale-[0.98] disabled:opacity-40"
          >
            <Database size={16} weight="bold" />
            {busy === "local" ? "备份中…" : "本地备份"}
          </button>
          <button
            onClick={() => doBackup(true)}
            disabled={busy !== null}
            className="flex items-center gap-2 rounded-lg border border-[var(--border)] px-4 py-2 text-sm font-medium transition hover:border-[var(--accent)] active:scale-[0.98] disabled:opacity-40"
          >
            <CloudArrowUp size={16} weight="bold" />
            {busy === "cloud" ? "上传中…" : "云端备份"}
          </button>
        </div>
        {bak && (
          <p
            className={`mt-3 text-sm ${
              bak.startsWith("备份完成") || bak.startsWith("云端备份完成")
                ? "text-[var(--accent)]"
                : "text-rose-400"
            }`}
          >
            {bak}
          </p>
        )}
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
