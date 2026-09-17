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


"""Reading a range of changes out of text, into categories.

`parse_git_log` is the way in. The conventional-commit convention governs
*commits*, so the commits are what a changelog should be read off, and a
squashed subject is what lands on the branch and what everything else in the
repository reads.

Three things that shapes:

* **Reverts resolve.** Working out whether a revert cancels something in the
  same range means reading the revert commit's *body*, which is in the log.
  See `_cancelled`.
* **Authors are a name and an email.** A handle is sometimes recoverable from
  the email and sometimes not, so a contributor is credited by whichever is
  there. See `_authors`.
* **There is no compare link.** A release body ends with one; a git log has
  no equivalent, so the caller supplies it (`--compare-url`) if it wants one.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import NamedTuple

from ._authors import credit_for
from ._constants import (
    BREAKING,
    CATEGORIES,
    COMMIT_SUBJECT_REGEX,
    GIT_LOG_FIELD_SEPARATOR,
    GIT_LOG_RECORD_SEPARATOR,
    IGNORED_TYPES,
    PR_SUFFIX_REGEX,
    REVERT,
    REVERT_OF_BREAKING_TYPES,
    REVERTS_REGEX,
    UNKNOWN,
)
from ._models import Change


def _empty_categories() -> dict[str, list[Change]]:
    """Every category, in render order, whether or not the range filled it.

    Callers index `categories['breaking']` and iterate the dict for the
    order, so the shape must not depend on what happened to be released.
    """
    return {category: [] for category in CATEGORIES}


def _capitalise(summary: str) -> str:
    """Sentence-case a summary without disturbing what it starts with.

    Conventional-commit summaries are lower case after the type and
    changelog bullets are sentence case, but a summary starting with a
    backtick or a quotation mark must come through untouched.
    """
    return summary[0].upper() + summary[1:] if summary else summary


class _Commit(NamedTuple):
    """One record of a `GIT_LOG_FORMAT` log, taken apart."""

    category: str
    breaking: bool
    description: str
    pr_number: int | None
    credit: str | None
    #: The pull request this commit reverts, where it says so in its body.
    reverts: int | None
    #: The conventional-commit type of the thing being reverted, read out of
    #: the quoted subject a revert carries: `revert: "feat: ..."` is `feat`.
    reverted_type: str | None
    reverted_type_is_breaking: bool


def _parse_reverts(body: str, repo: str | None) -> int | None:
    """Find the pull-request number a revert commit's body names, if any.

    A ``Reverts other/repo#5`` naming a different repository is not this
    range's #5, and cancelling against it would drop the wrong pair, so it
    is ignored when the caller has said which repository this log is from.
    With no `repo` to check against there is nothing to compare, and the
    reference is taken at face value.
    """
    match = REVERTS_REGEX.search(body)
    if not match:
        return None
    named_repo = match.group('repo')
    if repo is not None and named_repo is not None and named_repo.casefold() != repo.casefold():
        return None
    return int(match.group('number'))


def _parse_commit(record: str, team: Collection[str], repo: str | None) -> _Commit | None:
    """One `GIT_LOG_FORMAT` record, or `None` if the record is empty.

    A subject that is not a conventional-commit one -- a merge commit, or
    anything from before the convention was adopted -- is kept, under the
    `UNKNOWN` meta category and with its subject verbatim, rather than being
    dropped. There is no type to strip, and guessing one would be worse than
    showing the human what was actually written.
    """
    name, _, rest = record.partition(GIT_LOG_FIELD_SEPARATOR)
    email, _, rest = rest.partition(GIT_LOG_FIELD_SEPARATOR)
    subject, _, body = rest.partition(GIT_LOG_FIELD_SEPARATOR)

    subject = subject.strip()
    pr_number = None
    if suffix := PR_SUFFIX_REGEX.search(subject):
        pr_number = int(suffix.group(1))
        subject = subject[: suffix.start()]

    match = COMMIT_SUBJECT_REGEX.match(subject)
    if not match:
        if not subject:
            return None
        return _Commit(
            category=UNKNOWN,
            breaking=False,
            description=subject,
            pr_number=pr_number,
            credit=credit_for(name, email, team),
            reverts=_parse_reverts(body, repo),
            reverted_type=None,
            reverted_type_is_breaking=False,
        )

    summary = match.group('summary').strip()
    reverted_type = None
    reverted_type_is_breaking = False
    if match.group('category').casefold() == REVERT:
        # `revert: "feat: add the thing"` -- the quoted subject is the one
        # being undone, and its type says how much undoing it matters. The
        # quotes are what GitHub's Revert button writes, but a hand-written
        # revert often leaves them off, so both are read.
        inner = COMMIT_SUBJECT_REGEX.match(summary.strip('"').strip())
        if inner:
            reverted_type = inner.group('category').casefold()
            reverted_type_is_breaking = inner.group('breaking') == '!'

    return _Commit(
        category=match.group('category').casefold(),
        breaking=match.group('breaking') == '!',
        description=_capitalise(summary),
        pr_number=pr_number,
        credit=credit_for(name, email, team),
        reverts=_parse_reverts(body, repo),
        reverted_type=reverted_type,
        reverted_type_is_breaking=reverted_type_is_breaking,
    )


def _cancelled(commits: list[_Commit]) -> set[int]:
    """Find the commits that a revert in the same range takes back out of it.

    A change that landed and was undone before anything shipped did not
    happen as far as a reader is concerned, so neither half appears: not the
    change, and not the revert of it either. Listing both would be accurate
    and useless, and listing the revert alone would describe the removal of
    something the changelog never said had arrived.

    Identity is the pull-request number, because that is what a revert body
    names and, under squash merging, the only stable thing it could name.

    Returns:
        The indices into `commits` to leave out.

    """
    numbers = {commit.pr_number: index for index, commit in enumerate(commits) if commit.pr_number}
    cancelled: set[int] = set()
    for index, commit in enumerate(commits):
        if commit.reverts is not None and commit.reverts in numbers:
            cancelled.add(index)
            cancelled.add(numbers[commit.reverts])
    return cancelled


def parse_git_log(
    log_text: str, *, team: Collection[str] = (), repo: str | None = None
) -> dict[str, list[Change]]:
    """Parse a range of commits into categories.

    This is the input to prefer. The conventional-commit convention is about
    commit subjects, `canonical/operator` squash-merges so that every subject
    on a release branch is `type: summary (#N)`, and reading the subjects is
    therefore reading the thing the convention actually governs. It also
    needs nothing from GitHub: the pull-request number comes out of the
    `(#N)` suffix, and the link a release body wants is built back up from
    that number and `repo` when it is rendered.

    `log_text` is the output of ``git log`` with ``--format=GIT_LOG_FORMAT``,
    oldest first. Getting it is the caller's job: nothing here runs git, or
    anything else.

    Three things the subjects and bodies make possible:

    * **Reverts cancel.** A revert whose pull request is also in this range
      removes both itself and what it reverted. See `_cancelled`.
    * **A revert of something already released is called out.** It keeps its
      own `Reverted` heading rather than being filed under the type it
      undoes, so that it reads as a removal and not as a new fix. A revert of
      a released feature is a withdrawal of behaviour, so it goes further and
      is routed to `breaking`; see `REVERT_OF_BREAKING_TYPES`.
    * **A commit with no `(#N)` keeps a `None`**, rather than a placeholder,
      so that a change pushed straight to the branch is visible as one.

    Args:
        log_text: `git log` output in `GIT_LOG_FORMAT`.
        team: Authors not to credit, as emails and/or handles. The default
            credits everyone; see `_authors`.
        repo: The `owner/name` this log came from, used only to ignore a
            `Reverts` line that names a different repository.

    Returns:
        A dict of category to `Change` list, in the order they are rendered
        in, with every category present even when empty. There is no second
        return value: a git log carries no compare link for the formatter to
        pass through, and inventing one would mean knowing the tags at both
        ends, which is the caller's business.

    """
    records = log_text.split(GIT_LOG_RECORD_SEPARATOR)
    commits: list[_Commit] = []
    for record in records:
        if not record.strip():
            continue
        commit = _parse_commit(record, team, repo)
        if commit is not None:
            commits.append(commit)

    categories = _empty_categories()
    cancelled = _cancelled(commits)
    for index, commit in enumerate(commits):
        if index in cancelled or commit.category in IGNORED_TYPES:
            continue
        change = Change(commit.description, commit.pr_number, commit.credit)
        breaking = commit.breaking or (
            commit.category == REVERT
            and (
                commit.reverted_type in REVERT_OF_BREAKING_TYPES
                or commit.reverted_type_is_breaking
            )
        )
        if breaking:
            categories[BREAKING].append(
                change._replace(
                    description=f'{commit.category.capitalize()}: {change.description}'
                )
            )
        elif commit.category not in categories:
            # A real conventional-commit type that is not a category: keep
            # the type, since it is the thing the human has to act on.
            categories[UNKNOWN].append(
                change._replace(
                    description=f'{commit.category.capitalize()}: {change.description}'
                )
            )
        else:
            categories[commit.category].append(change)

    return categories
