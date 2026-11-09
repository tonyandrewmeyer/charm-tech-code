# Contributing

Anyone can contribute to charm-tech-code. It's best to start by [opening an issue](https://github.com/canonical/charm-tech-code/issues) with a clear description of the problem or feature request, but you can also [open a pull request](https://github.com/canonical/charm-tech-code/pulls) directly.

You'll need [`uv`](https://docs.astral.sh/uv/#installation).

## Running the tests and lint

Each tool is its own package in its own top-level directory, with its own lockfile, so the tests run per package:

```shell
cd <package>
uv sync --locked --group unit
uv run pytest
```

Lint and format run once from the root, across every package, using the ruff version and configuration in the root `pyproject.toml`:

```shell
uv run --group lint ruff check .
uv run --group lint ruff format --check .
```

CI runs the same commands, with each package's tests on Python 3.10, 3.12 and 3.14.

When you add a package, add its directory to the matrix in `.github/workflows/ci.yaml` and to the `uv` entry in `.github/dependabot.yaml`.

## How the code is consumed

Nothing here is released or published. Other repositories' workflows run a tool with `uvx`, pinned by commit SHA:

```yaml
run: uvx --from "git+https://github.com/canonical/charm-tech-code@<40-char-sha>#subdirectory=<package>" <tool>
```

So a change reaches a repository only when that repository moves its pin to a commit that includes it. Merging here doesn't change anything that's already running, and a change to a tool's command-line interface needs the workflows that call it updated when their pin moves.

## Pull requests

Pull requests should have a short title that follows the [conventional commit style](https://www.conventionalcommits.org/en/) using one of these types:

- chore
- ci
- docs
- feat
- fix
- perf
- refactor
- revert
- test

We don't use scopes. Mark a breaking change, such as a change to a tool's command-line interface, with `!`, like `feat!: ...`.

The commits on the PR's branch don't need to follow the format, as they're squashed into a single commit on `main` using the PR title as the commit message.
