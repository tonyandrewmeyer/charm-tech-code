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


"""Rendering parsed categories as release notes and as a changelog entry."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Mapping

from ._constants import (
    BREAKING,
    BREAKING_PREAMBLE,
    CATEGORY_HEADINGS,
    FULL_CHANGELOG_PREFIX,
    PULL_REQUEST_URL_TEMPLATE,
    UNKNOWN,
    UNKNOWN_PREAMBLE,
)
from ._models import Change

logger = logging.getLogger(__name__)


def commit_type_to_category(commit_type: str) -> str:
    """Map a commit type to a human-readable category heading.

    If the commit type is not recognised, it returns the capitalised commit type.
    """
    return CATEGORY_HEADINGS.get(commit_type, commit_type.capitalize())


def _bullet(change: Change, reference: str | None) -> str:
    """One `* ...` line: the change, who to thank, and where it came from.

    The three pieces are each optional after the first, and a missing one
    takes its separator with it rather than leaving `by  (#)` behind. The
    credit sits before the reference because that is where
    `canonical/operator`'s hand-written entries have always put it:
    `* Fix typos in code snippets by @ducky-debugger (#1750)`.
    """
    parts = [f'* {change.description}']
    if change.credit:
        parts.append(f'by {change.credit}')
    if reference:
        parts.append(reference)
    return ' '.join(parts)


def format_release_notes(categories: Mapping[str, list[Change]], compare_url: str | None) -> str:
    """Format for release notes.

    Results in a Markdown formatted string with sections for each commit type.

    Breaking changes are rendered first, under their own heading and a
    sentence asking the reader to review them. `categories` is expected to be
    what `parse_git_log` returned: every category present, in the order they
    are rendered in.

    Args:
        categories: The parsed changes.
        compare_url: A link comparing the two ends of the range, rendered as
            the closing line. A git log does not carry one, and the tags at
            either end are the caller's to know. `None` for no closing line.
    """
    lines = ["## What's Changed", '']
    if categories[BREAKING]:
        lines.append(f'### {commit_type_to_category(BREAKING)}')
        lines.append(f'{BREAKING_PREAMBLE}\n')
        lines.extend(_bullet(change, _reference(change)) for change in categories[BREAKING])
        lines.append('')
        logger.info(
            'Breaking changes detected in the release notes. '
            'Please ensure there are sufficient instructions for users to handle them.'
        )
    for commit_type, items in categories.items():
        if commit_type == BREAKING:
            continue
        if items:
            lines.append(f'### {commit_type_to_category(commit_type)}')
            if commit_type == UNKNOWN:
                lines.append(f'{UNKNOWN_PREAMBLE}\n')
            lines.extend(_bullet(change, _reference(change)) for change in items)
            lines.append('')
    if compare_url:
        lines.append(f'{FULL_CHANGELOG_PREFIX}: {compare_url}')
    return '\n'.join(lines)


def _reference(change: Change) -> str | None:
    """Render the `in #N` half of a release-notes bullet, or nothing.

    The short form rather than the full URL: GitHub renders `#N` in a
    release body as a link to the pull request, with its title on hover, so
    spelling the URL out would only make the line longer.
    """
    if change.pr_number is None:
        return None
    return f'in #{change.pr_number}'


def format_changes(
    categories: Mapping[str, list[Change]], tag: str, date: datetime.date, *, repo: str
) -> str:
    """Format for CHANGES.md.

    The header is formatted as a top-level heading with the tag and date.
    The content is a Markdown formatted string with sections for each commit type.
    Each item is formatted as a bullet point with the description and a link to
    the pull request in parentheses. A Markdown link rather than a bare
    `#N`: a `CHANGES.md` is read in an editor, on PyPI and in the docs as
    often as it is read on GitHub, and only GitHub turns `#N` into a link.
    The link text stays `#N` so the line still reads the way the
    hand-written entries always have.

    `repo` is the `owner/name` the links point into. It is needed because a
    `Change` carries a number and not a URL -- the number is all a git log
    has -- so the link is built here rather than carried around.

    A change with no pull request behind it -- a commit pushed straight to
    the branch -- gets no link rather than a placeholder standing in for one.
    The change is real and belongs in the list; what it does not have is a
    pull request to point anyone at, and saying so plainly beats a
    placeholder that reads like a parsing accident.

    `date` is passed in rather than read from the clock. This module does no
    I/O of any kind, and "what day is it" is I/O: the caller knows whether it
    means the runner's today, the date on the tag, or a date under test.
    """
    day = date.strftime('%d %B %Y')
    lines = [f'# {tag} - {day}\n']
    for commit_type, items in categories.items():
        if items:
            lines.append(f'## {commit_type_to_category(commit_type)}\n')
            for change in items:
                reference = None
                if change.pr_number is not None:
                    url = PULL_REQUEST_URL_TEMPLATE.format(repo=repo, number=change.pr_number)
                    reference = f'([#{change.pr_number}]({url}))'
                lines.append(_bullet(change, reference))
            lines.append('')
    return '\n'.join(lines) + '\n'
