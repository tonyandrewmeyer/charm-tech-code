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


"""What a parser produces and a formatter renders: one change.

There is one of these per bullet. `parse_git_log` makes them and everything
downstream -- the two formatters, the bump-size rule -- works on this rather
than on the shape of the text they were read out of.
"""

from __future__ import annotations

from typing import NamedTuple


class Change(NamedTuple):
    """One entry in a changelog.

    Attributes:
        description: The summary, with its conventional-commit type stripped
            and its first letter capitalised. A change routed into the
            `breaking` category keeps its real type as a prefix, so this may
            read `Feat: add the thing` or `Revert: "feat: add the thing"`.
        pr_number: The pull request this came from, or `None` for a commit
            that has none -- one pushed straight to the branch, or a release
            notes bullet whose link is not a pull request. `None` is carried
            rather than a placeholder because a change with no pull request
            is still a real change, and rendering it as `(#?)` hides it in
            plain sight.
        credit: How to credit the author, already rendered: `@handle` where
            one is known, the person's name where it is not, and `None` for
            an author the caller named as one of its own. See `_authors`.
    """

    description: str
    pr_number: int | None = None
    credit: str | None = None
