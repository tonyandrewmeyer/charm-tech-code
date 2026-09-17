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


"""Size a release, and say what version it makes.

How big the commits in a range add up to, and what that makes the next
version.

Two functions, and the split between them is the interesting part. The
Charm Tech repositories share a changelog format and a set of
conventional-commit types, so "is this range a minor or a patch" has one
answer everywhere and lives here. What the *next version* of a particular
repository is does not: which version to count from, whether a `.dev0` gets
appended afterwards, what a pre-release looks like, and canonical/operator's
rule that the ops-scenario major is the ops major plus five, are all things
the adopting repository decides. `next_version` is the generic middle: it
applies a size to a plain `X.Y.Z` and refuses anything else, so a repository
with a version shape of its own has to say what it means rather than get a
silently wrong answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from ._constants import MINOR_BUMP_CATEGORIES, RELEASE_VERSION_REGEX
from ._models import Change

#: The two sizes a release can be inferred to be. A major bump is never
#: inferred -- see `MINOR_BUMP_CATEGORIES` -- so it is not one of these.
BumpSize = Literal['minor', 'patch']

MINOR: BumpSize = 'minor'
PATCH: BumpSize = 'patch'


def infer_bump_size(categories: Mapping[str, list[Change]]) -> BumpSize:
    """Work out whether a range of changes is a minor release or a patch one.

    `categories` is what `parse_git_log` returned.
    That matters rather more than it looks: the parse is where a `!` moves an
    entry out of its real type and into `breaking`, and where a revert of a
    released feature is routed there too, so the categories this reads have
    already had that routing applied. Passing a dict built some other way
    will get a different answer.

    A revert is a change like any other here, with one thing worth saying:
    a revert of something released earlier counts, at least as a patch,
    because taking a change back out is itself a change that shipped. A
    revert of something in this same range counts for nothing, because
    `parse_git_log` has already cancelled the pair and neither is in
    `categories` to be counted.

    A `minor` result means "there is a feature, or a breaking change, in this
    range". A caller that has branches on which neither may appear -- a
    maintenance branch carrying only cherry-picked fixes, say -- should treat
    `minor` as the error it is for that branch, rather than quietly bumping
    the patch instead. This function will not do that for you: which branches
    those are is repository policy, and nothing here knows what branch it is
    on.

    Returns:
        `'minor'` or `'patch'`. Never `'major'`: a major release is a
        deliberate act, not something to infer from a commit range.
    """
    if any(categories.get(category) for category in MINOR_BUMP_CATEGORIES):
        return MINOR
    return PATCH


def next_version(*, previous: str, size: BumpSize) -> str:
    """Apply a bump size to a released version.

    `previous` is the version this release follows, which for a workflow
    proposing a release is normally the last tag on the branch being released
    -- not whatever is currently in the repository's version file, which by
    then is usually a `.dev0` of a version that was only ever a guess.

    Only a plain `X.Y.Z` is accepted. A pre-release, a dev version or a local
    version means the caller is doing something this cannot infer -- cutting
    `3.9.0b1`, or resuming a pre-release series -- and the release workflow's
    explicit version input is the way to say so. Raising is the point: the
    alternative is quietly dropping a suffix and tagging the wrong thing.

    Raises:
        ValueError: if `previous` is not a plain `X.Y.Z`, or `size` is not
            one of the two sizes `infer_bump_size` returns.
    """
    match = RELEASE_VERSION_REGEX.fullmatch(previous.strip())
    if not match:
        raise ValueError(
            f'{previous!r} is not a plain X.Y.Z release version. Pre-releases, '
            f'dev versions and anything else are not inferred from: pass the '
            f'version you want explicitly.'
        )
    major, minor, patch = (int(part) for part in match.groups())
    if size == MINOR:
        return f'{major}.{minor + 1}.0'
    if size == PATCH:
        return f'{major}.{minor}.{patch + 1}'
    raise ValueError(f'{size!r} is not a bump size. Expected {MINOR!r} or {PATCH!r}.')
