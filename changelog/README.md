# changelog

Turns a range of commits into our changelog format.

The Charm Tech repositories have different release processes but aim for a consistent changelog style. The formatting and the version arithmetic are centralised here; the file rewriting and the GitHub calls stay in each repository.

## What it produces

Four commits in a range, the first of them from a contributor outside the team:

```
fix: stop the framework mistaking two notices for twins (#2684)
docs: stop dressing cross-references up as quotations (#2666)
ci: crawl back to the upstream concierge presets (#2699)
chore: bump cryptography from 48.0.1 to 50.0.0 (#2682)
```

become one `CHANGES.md` entry:

```markdown
# 3.8.2 - 31 August 2026

## Fixes

* Stop the framework mistaking two notices for twins by @ducky-debugger ([#2684](https://github.com/canonical/operator/pull/2684))

## Documentation

* Stop dressing cross-references up as quotations ([#2666](https://github.com/canonical/operator/pull/2666))

## CI

* Crawl back to the upstream concierge presets ([#2699](https://github.com/canonical/operator/pull/2699))
```

Release notes are the same content with `###` headings and `in #2684` in place of the parenthesised link. The short form is GitHub's own: a release body renders `#2684` as a link to the pull request, where a `CHANGES.md` is read in an editor, on PyPI and in the docs as well, and only GitHub would make a link of it.

Most of the rules are visible there:

* **A contributor from outside the maintaining team is credited; a maintainer is not.** Pass the team to `--team`, comma-separated, as emails and/or handles; an empty team credits everyone, which is the safe way round, since over-crediting is visible in the draft release and crediting nobody is not.
* **A handle is only sometimes recoverable.** `46688206+ducky-debugger@users.noreply.github.com` gives `@ducky-debugger`, GitHub's default for an account with a private email; where the log has no handle, the person is credited by name.
* **`chore` is dropped on purpose.** Dependency bumps, charm pins and the release's own version bump are not what a reader came for, and `git log` still has them.
* **The headings, their order and the commit-type map are fixed.** The format is common across our repositories, so there is nothing for an adopting repository to supply.

And the rules it doesn't show:

* **A `!` means breaking.** The entry moves to a `Breaking Changes` section that renders first, keeping its real type as a prefix and carrying a sentence asking the reader to review carefully; it does not infer a *major* bump.
* **A revert of something in the same range cancels with it**, and neither appears; a revert of something already released goes under `Reverted`, or under `Breaking Changes` where it undoes a released `feat` or anything carrying a `!`.
* **Anything unplaceable is surfaced rather than dropped.** An unrecognised commit type, or a subject that is not conventional at all, lands under `Uncategorised` with the type kept, for a human to fix while reading the draft.
* **A commit with no `(#N)` in its subject** - one pushed straight to the branch - renders with no reference rather than a placeholder standing in for one.

## Using it

Everything goes through the `changelog` console script. It reads a git log on stdin and prints one answer on stdout:

```shell
git log --reverse --no-merges --format="$(changelog git-log-format)" "$LAST_TAG..$BRANCH" > log.txt
SIZE=$(changelog bump-size --team "$TEAM" < log.txt)
VERSION=$(changelog next-version --previous "$LAST_TAG" --team "$TEAM" < log.txt)
changelog release-notes --repo "$REPO" --team "$TEAM" \
    --compare-url "https://github.com/$REPO/compare/$LAST_TAG...$VERSION" < log.txt > release-notes.md
changelog changes-entry --repo "$REPO" --tag "$VERSION" --team "$TEAM" < log.txt > changes-entry.md
```

The two commands that print one word are for `$GITHUB_OUTPUT`; the two that print Markdown are for redirecting into a file, because a `$GITHUB_OUTPUT` line only takes a multi-line document through a heredoc delimiter the document must not itself contain. `git-log-format` prints the `--format` string the others expect, so the separators live in one place rather than in every workflow - copy them and it works until someone drops one, at which point the log stops parsing and the release goes out with an empty changelog rather than an error.

`--compare-url` is optional: a git log carries no compare link and the tags at either end are the workflow's to know. `--date` defaults to today in UTC, and the console script is the only part of the package that reads the clock. `changelog --help` has the rest.

From a workflow, pinned to a commit:

```shell
uvx --from "git+https://github.com/canonical/charm-tech-code@<40-char-sha>#subdirectory=changelog" changelog bump-size < log.txt
```

## Versions

`bump-size` says whether a range is a minor or a patch - a `feat` or a breaking change means minor, anything else means patch - and `next-version` applies that to a plain `X.Y.Z`. A major bump is never inferred, nor is a pre-release: `next-version` raises rather than guess at anything that isn't `X.Y.Z`, `3.9.0.dev0` included, since that is the guess the last post-release bump left in the version file rather than a version anyone shipped.

The repository decides what to count from, whether a `.dev0` goes on afterwards, and what any of it means for the other packages it ships. A `minor` on a branch where a feature has no business appearing, such as operator's `2.23-maintenance`, is an error rather than a patch - but which branches those are is the repository's to know, so that check belongs there.

## Developing

```shell
uv sync --group unit
uv run pytest
```
