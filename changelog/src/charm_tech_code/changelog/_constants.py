# Copyright 2026 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""The changelog format, as constants.

None of this is injectable, and that is the point. The format is the same
across the Charm Tech repositories, and the set of conventional-commit types
is enforced by a shared CI check, so there is no second format for an
adopting repository to supply. A hook or a template system here would exist
for a caller that does not exist.
"""

from __future__ import annotations

import re

#: How a pull-request link is rebuilt from a number. A change carries its
#: *number*, because that is all the git log carries and all a ``CHANGES.md``
#: entry renders, so the URL a release body wants is built back up from the
#: number and the repository the caller names. That is a string operation,
#: which is what keeps the package free of I/O.
PULL_REQUEST_URL_TEMPLATE = 'https://github.com/{repo}/pull/{number}'

#: The prefix of the compare line a release body ends with. The link itself
#: is the caller's (`--compare-url`); this is the shape GitHub uses, so that
#: notes rendered here read the same as notes rendered there.
FULL_CHANGELOG_PREFIX = '**Full Changelog**'

#: The ``git log --format=`` string `parse_git_log` expects, and the two
#: control characters it is built out of.
#:
#: The fields are the *author* name and email, the subject, and the body.
#: Author rather than committer: a squash merge records the contributor as
#: the author and GitHub itself as the committer, so the committer is never
#: the person to credit. There is no SHA, deliberately -- a squashed commit's
#: SHA on the default branch has no relation to anything a contributor would
#: cite, and the pull-request number in the subject is the key that does.
#:
#: The separators are ASCII 0x1e (record) and 0x1f (unit), which is what they
#: are for. A commit message may contain anything else, newlines and blank
#: lines very much included, so a line-oriented or blank-line-delimited format
#: would be guessing at where one commit stops and the next starts::
#:
#:     git log --reverse --no-merges --format="$FORMAT" 3.8.1..3.8.2
#:
#: ``--reverse`` because a changelog reads oldest first, which is also the
#: order GitHub's generated notes come in. ``--no-merges`` because a merge
#: commit's subject is not a conventional-commit one; such a subject is
#: dropped anyway, so this is tidiness rather than correctness.
GIT_LOG_RECORD_SEPARATOR = '\x1e'
GIT_LOG_FIELD_SEPARATOR = '\x1f'
GIT_LOG_FORMAT = '%x1e%an%x1f%ae%x1f%s%x1f%b'

#: A conventional-commit subject, as the shared `check-conventional-pr-title`
#: script defines it: a type, an optional scope, an optional ``!``, then the
#: summary.
#:
#: The scope is captured and dropped. `canonical/pebble` uses scopes heavily
#: -- 60 of its last 298 conventional subjects carry one, from
#: ``chore(deps)`` to ``fix(cmdstate,wsutil)`` -- so this is a choice about
#: what a changelog entry should read like, not an observation that nothing
#: uses them. A reader of a release's notes wants what changed; which package
#: it changed in is in the diff, and prefixing every bullet with it would
#: mean a `chore(deps)`-heavy range rendering sixty near-identical prefixes.
#: If a repository ever wants them rendered, `Change` is where the scope
#: would have to be carried, and this is the only place it is currently
#: thrown away.
COMMIT_SUBJECT_REGEX = re.compile(
    r'^(?P<category>[A-Za-z]+)'
    r'(?:\((?P<scope>[^()]+)\))?'
    r'(?P<breaking>!?)'
    r': (?P<summary>.+)$'
)

#: The ``(#123)`` that a squash merge appends to the subject, and the only
#: place the pull-request number comes from on the git-log path. Over
#: `canonical/operator`'s last 300 commits every subject carries one; the
#: commits that do not are older, from before the squash-merge policy, and a
#: commit pushed straight to the default branch would not have one either.
#: Such a change is real and belongs in the changelog, so it is carried with
#: no number rather than with a placeholder that hides it.
PR_SUFFIX_REGEX = re.compile(r'\s*\(#(\d+)\)$')

#: What GitHub's "Revert" button writes into the body of the revert pull
#: request: ``Reverts canonical/operator#2538``. It names the *pull request*
#: rather than a commit, which is the robust key under squash merging, since
#: the reverted commit's SHA on the default branch is not something anyone
#: cites. The owner/repo part is optional because a body written by hand
#: often leaves it off.
REVERTS_REGEX = re.compile(
    r'^[ \t]*Reverts[ \t]+(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<number>\d+)[ \t]*$',
    flags=re.MULTILINE | re.IGNORECASE,
)

#: A GitHub no-reply address, which is the one author email a handle can be
#: recovered from: ``46688206+ducky-debugger@users.noreply.github.com`` is
#: ``@ducky-debugger``. It is the default for a GitHub account with a private email,
#: so it is the common form for exactly the drive-by external contributor
#: this is here to credit. The older suffix-free form is accepted too, and so
#: is the ``[bot]`` a GitHub App's address carries.
NOREPLY_EMAIL_REGEX = re.compile(
    r'^(?:\d+\+)?(?P<handle>[A-Za-z\d](?:[A-Za-z\d]|-(?=[A-Za-z\d]))*(?:\[bot\])?)'
    r'@users\.noreply\.github\.com$',
    flags=re.IGNORECASE,
)

#: The categories a changelog has, in the order they are rendered.
#:
#: Two of these are meta categories that nothing parses into directly.
#:
#: `breaking` is filled by a `!` after the real type, which moves the entry
#: out of its own category and into this one, keeping its real type as a
#: prefix (`Feat: ...`). It renders first.
#:
#: `unknown` is filled by anything this package cannot place: a
#: conventional-commit type that is neither a category nor one of
#: `IGNORED_TYPES`, and a subject that is not conventional at all. It renders
#: last, under `UNKNOWN_PREAMBLE`, so that the human reading the draft
#: release sees what needs categorising by hand. Silently dropping these is
#: what the format used to do, and it makes a typo in a commit type
#: indistinguishable from a deliberate omission.
#:
#: Deliberate omissions are `IGNORED_TYPES`, which is where `chore` lives.
CATEGORIES: tuple[str, ...] = (
    'breaking',
    'feat',
    'fix',
    'docs',
    'test',
    'refactor',
    'perf',
    'ci',
    'revert',
    'unknown',
)

#: The meta category breaking changes are collected into.
BREAKING = 'breaking'

#: The meta category anything unplaceable is collected into.
UNKNOWN = 'unknown'

#: Real conventional-commit types that are deliberately not rendered, as
#: distinct from the unrecognised ones that go to `UNKNOWN`.
#:
#: `chore` is the whole list. It is accepted by the PR-title check, but
#: dependency bumps, charm-pin updates and the release's own version-bump
#: commit are all `chore`, and none of them is something a reader of a
#: changelog is looking for. In a typical operator release that is a third to
#: a half of the commits in the range. Dropping them is the intended
#: behaviour, not an oversight in the type list, so please do not "fix" it by
#: adding a `chore` category.
IGNORED_TYPES: tuple[str, ...] = ('chore',)

#: The one real conventional-commit type the bump-size rule cares about.
FEATURE = 'feat'

#: The type of a commit that undoes another one.
REVERT = 'revert'

#: Reverting one of these is a removal of behaviour people may already be
#: relying on, so the revert is routed to `BREAKING` rather than left under
#: `REVERT`. Only a revert of something *already released* gets this far: a
#: revert of a change in the same range cancels with it and neither appears.
#:
#: `feat` is the case that matters, and it is the reason this exists at all:
#: a released feature that is taken away again is a breaking change by any
#: reading, and filing it under `Reverted` would put it below the fold of a
#: changelog that the affected reader needs to see the top of. An inner `!`
#: is treated the same way, for the same reason and more obviously.
REVERT_OF_BREAKING_TYPES: tuple[str, ...] = (FEATURE,)

#: The categories whose presence in a range makes the release a minor one.
#: Everything else -- and an empty range -- is a patch.
#:
#: `feat` is the rule as it is usually stated. `breaking` is here because a
#: `!` moves an entry *out* of its real type and into the meta category, so
#: a range whose only feature is a `feat!` has an empty `feat` list, and a
#: rule that only looked at `feat` would call that release a patch. It is
#: also the right answer in its own right: a `!` does not infer a major bump
#: (see `BREAKING_PREAMBLE`), but a breaking change riding in a *patch*
#: release is a worse bend of the rules than one riding in a minor, which is
#: the bend we have actually decided to allow. operator's own 3.8.0 shipped
#: a `refactor!` in a minor release.
#:
#: A major bump is never inferred, from this or anything else. That is what
#: the release workflow's explicit version input is for.
MINOR_BUMP_CATEGORIES: tuple[str, ...] = (BREAKING, FEATURE)

#: A plain `X.Y.Z` release version, which is the only shape the bump
#: arithmetic will touch. Pre-releases, dev versions and anything else are
#: the caller's own policy: see `next_version`.
RELEASE_VERSION_REGEX = re.compile(r'(\d+)\.(\d+)\.(\d+)')

#: Commit type to the heading it is rendered under. A type with no entry
#: here is capitalised instead, which is what makes an unrecognised type
#: degrade to something readable rather than to a KeyError.
CATEGORY_HEADINGS = {
    'feat': 'Features',
    'fix': 'Fixes',
    'docs': 'Documentation',
    'test': 'Tests',
    'ci': 'CI',
    'perf': 'Performance',
    'refactor': 'Refactoring',
    'revert': 'Reverted',
    'breaking': 'Breaking Changes',
    'unknown': 'Uncategorised',
}

#: The sentence under the release notes' `### Breaking Changes` heading. A
#: `!` deliberately does not infer a major version bump -- a breaking change
#: sometimes rides in a minor release -- so this calling-out is what the bent
#: rule relies on.
BREAKING_PREAMBLE = 'There are breaking changes in this release. Please review them carefully:'

#: The sentence under the `Uncategorised` heading. These entries are the ones
#: the package could not place, and the draft release is where a human is
#: already reading the notes, so this is the cheapest place to ask them to.
UNKNOWN_PREAMBLE = (
    'These changes have a commit type this changelog does not recognise, or '
    'no conventional-commit type at all, and need categorising by hand:'
)
