# Contributing to KuroTutor

Thanks for your interest! Issues and pull requests are welcome.

## Getting started

```bash
git clone https://github.com/ZYT64/KuroTutor.git
cd KuroTutor
python -m venv .venv
.venv/Scripts/pip install -e ".[test,dev]"   # Windows; Linux/mac: .venv/bin/pip
pytest            # all tests run offline — no API keys needed
ruff check kurotutor/ tests/
```

## Ground rules

- **Agent-first**: features are Agent tools, not menus. New capability = new tool in `kurotutor/tools/`.
- **Pluggable providers**: never hardcode model/API vendors. New providers go through the existing factory pattern.
- **Tests with every change**: unit/integration tests are offline by design (echo/mock providers). Never call real model APIs from tests.
- **No secrets in code**: keys live in `kuro.json` (gitignored). `kuro.example.json` is the template.
- **Style**: `ruff check` must pass; line length 110; type hints on function signatures.

## Pull requests

1. Fork → branch from `main` → keep changes focused (one feature/fix per PR).
2. Run `pytest` and `ruff check` locally; CI must be green.
3. Describe what changed and why. Screenshots help for user-visible behavior.

## Good first issues

- More eval cases in `evals/eval_cases.json`
- New question-bank adapters (the real-question chain is pluggable)
- New channel adapters (any messaging platform fits the same adapter interface)
