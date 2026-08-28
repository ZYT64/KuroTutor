# AGENTS.md — 项目级 Agent 指令

本文件给在本仓库工作的 AI Agent。优先遵守根目录 `CLAUDE.md`，二者冲突以 `CLAUDE.md` 为准。

## 一句话定位
KuroTutor：QQ 私聊里的 24 小时 AI 私人老师（全科、从零自研、开源 MIT）。

## 开发红线（勿踩）
1. **Agent‑first**：功能 = Agent 工具；管理走 CLI，**无 WebUI**。
2. **沙箱**：文件操作限定工作区；禁改系统设置；命令白名单 + 系统黑名单。
3. **密钥**：BYOK 明文存 `kuro.json`（私有仓库），**严禁推公开仓库**。
4. **不打空壳**：无 TODO/占位/假数据；每个功能必须真实可跑、可验证。
5. **精准修改**：Bug 修复定点增量，不无理由全量重写、不动 `docs/`。

## 新增工具的约定
在 `kurotutor/tools/` 下 new 一个模块，handler 签名 `async def handler(ctx: ToolContext, **params) -> str`，
然后在 `kurotutor/tools/registry.py` 的 `build_default_registry()` 里用
`registry.register(name, description, parameters_json_schema, handler, ...)` 注册。
文件读写一律经 `kurotutor/tools/files.py` 的沙箱封装。

## 测试与验证
- 新增/改动功能必须带 `tests/test_*.py`。
- 只跑单测 **不算完成**，必须补一次真实场景端到端验证并记录（见 `SUGGESTIONS.md`）。
- 命令行快速验证：`kuro doctor`、`kuro serve --channel console`。

## 常用命令
`kuro init` · `kuro config show|get|set|validate` · `kuro doctor` · `kuro agent tools` ·
`kuro student list|show|remove` · `kuro kb status` · `kuro serve --channel console|qq`
