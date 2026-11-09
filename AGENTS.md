# AGENTS.md

## What this repo is

Shared tooling for the Charm Tech repositories' workflows. Each tool is its own package in its own top-level directory (`ai-failure-notifier`, `changelog`, `draft-release-notes`), with its own `pyproject.toml`, `src/charm_tech_code/<name>/`, `tests/` and `uv.lock`. Nothing is released or published: other repositories run a tool with `uvx --from "git+https://github.com/canonical/charm-tech-code@<sha>#subdirectory=<package>" <tool>`, so the commit SHA is the version.

## Tests

Per package (`ai-failure-notifier`, `changelog` or `draft-release-notes`), for example:

```bash
uv run --directory changelog --locked --group unit pytest
```

Each package's tests have an autouse fixture in their `conftest.py` that fails a test which escapes its mocks: `ai-failure-notifier` blocks the network, real subprocesses (`gh`) and sleeping, `draft-release-notes` blocks the network, and `changelog` blocks the library reading the clock. Mock at the boundary instead of loosening these.

## Lint

From the root, across every package:

```bash
uv run --group lint ruff check .
uv run --group lint ruff format --check .
```

## Conventions

- Ruff's configuration lives only in the root `pyproject.toml` (copied from operator's). A package's `[tool.ruff]` just has `extend = "../pyproject.toml"`: don't add settings there.
- Python 3.10+ (CI tests 3.10, 3.12 and 3.14), line length 99, single quotes, Google-style docstrings, `from __future__ import annotations` in every module but `__init__.py`.
- Every Python file starts with the Apache 2.0 licence header (ruff's `CPY` rule checks for it).
- Keep runtime dependencies to a minimum. Each `uvx` run installs them on a GitHub runner, and the `pyproject.toml` comments say why each package has the few it has. After changing dependencies, run `uv lock` in that package: CI uses `--locked`.
- A tool's command-line interface is what other repositories' workflows call. Changing it is a breaking change (`feat!:`), and the callers need updating when they move their pin.
- Adding a package: add its directory to the matrix in `.github/workflows/ci.yaml` and to the `uv` entry in `.github/dependabot.yaml`.
- PR titles follow Conventional Commits with no scopes; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

See [SECURITY.md](SECURITY.md).
