# Contributing

Thanks for helping. Small, focused pull requests are easiest to review.

## Setup

```bash
uv sync
uv run pre-commit install
```

## Before you open a PR

```bash
uv run ruff check .
uv run black --check .
uv run mypy --strict src tests scripts
uv run pytest
python scripts/check_no_personal.py
```

## Rules

- Full type annotations on every function; no `Any`. Runtime dependencies stay empty (standard library only).
- One job per module and per function. `cli.py` parses and dispatches; logic lives elsewhere.
- Tests use real temporary directories and real processes. No mocks, stubs or fakes at any layer. For a service you cannot run locally, write a contract test that skips without credentials.
- A new test must be able to fail. Break the code once and watch it fail.
- No personal data, real names or company names anywhere. `scripts/check_no_personal.py` runs in CI.
- Keep skills at 60 lines or less; put detail in `references/`.
- Terse CLI output is a feature. `tests/test_output_budget.py` guards it.

## Adding things

- A chat source: [docs/writing-a-source.md](docs/writing-a-source.md)
- A storage backend: [docs/writing-a-storage-backend.md](docs/writing-a-storage-backend.md)

Open an issue with the `new-source` template first if you want feedback on the design.
