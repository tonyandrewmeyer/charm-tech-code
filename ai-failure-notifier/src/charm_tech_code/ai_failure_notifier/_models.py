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


"""The structured shapes a run's failure is reduced to."""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

from ._constants import MAX_COMMENT_CHARS, MAX_EXCERPT_CHARS, MAX_RECENT_COMMENTS

# The signature is built here, serialised into the prompt, and hashed for the
# marker, so its shape is worth pinning down rather than passing dicts around.
# `dataclasses.asdict` preserves field declaration order, which is what the
# prompt's JSON ends up in.


@dataclasses.dataclass(frozen=True)
class PytestFailure:
    """One line of pytest's short summary."""

    kind: str  # "FAILED" or "ERROR" -- pytest reports both here.
    test: str
    error: str


@dataclasses.dataclass(frozen=True)
class JobSignature:
    """What the deterministic parser could extract from one failed job's log."""

    job_id: int
    job_name: str
    failed_step: str | None
    pytest_failures: list[PytestFailure]
    go_failures: list[str]
    traceback_top_error: str | None
    tail_excerpt: list[str]


@dataclasses.dataclass(frozen=True)
class RunSignature:
    """Every failed job of one run, plus the run's own identifying fields."""

    run_id: str
    workflow_name: str
    html_url: str
    created_at: str
    jobs: list[JobSignature]

    def as_json(self) -> str:
        """Render for the prompt, in field declaration order."""
        return json.dumps(dataclasses.asdict(self), indent=2)


@dataclasses.dataclass(frozen=True)
class FailedJob:
    """A failed job as listed by `gh run view`, before its log is fetched."""

    id: int
    name: str
    failed_step: str | None


# Lines that are structure rather than content, and that a candidate excerpt
# should skip over rather than spend itself on. `## Summary` is the shipped
# prompt's first body line, so before this an enriched issue's whole excerpt
# was the word "Summary"; `Workflow:` and `Run:` are the applier's own footer.
_HEADING = re.compile(r'^\s*#{1,6}\s')
_FOOTER = re.compile(r'^\s*(?:Workflow|Run):\s')
_HTML_COMMENT = re.compile(r'<!--.*?-->', re.DOTALL)


@dataclasses.dataclass(frozen=True)
class CandidateIssue:
    """An existing issue that might already track this failure."""

    number: int
    title: str
    body: str | None
    closed_at: str | None
    # Comment bodies, oldest first. spike-step-4/prompt.md always specified the
    # candidate excerpt as "body opening OR most recent comment" and the
    # shipped renderer only ever did the first half; on a real tracker the
    # notifier-era issues keep their entire diagnosis in the thread, so that
    # was where the whole signal went. Defaulted so that a caller that has not
    # fetched comments still builds.
    comments: tuple[str, ...] = ()

    @classmethod
    def from_gh(cls, data: dict[str, Any]) -> CandidateIssue:
        """Build from one element of `gh issue list --json ...` output."""
        comments = data.get('comments') or []
        return cls(
            number=data['number'],
            title=data['title'],
            body=data.get('body'),
            closed_at=data.get('closedAt'),
            comments=tuple((c.get('body') or '').strip() for c in comments if c.get('body')),
        )

    def excerpt(self) -> str:
        """Return the body's first line of actual prose, bounded, for the candidate block."""
        text = _HTML_COMMENT.sub('', self.body or '')
        for line in text.splitlines():
            if line.strip() and not _HEADING.match(line) and not _FOOTER.match(line):
                return line.strip()[:MAX_EXCERPT_CHARS]
        return '(no body)'

    def recent_comments(self) -> list[str]:
        """Return the last few comments, oldest first, each flattened to one bounded line.

        spike-step-4/prompt.md said "most recent comment", and the most recent
        comment is often the wrong one: a thread's last word is a status note
        ("1 hour was still not enough"), while the diagnosis that names the test
        is a comment or two earlier. On #2633 -- the issue six of last night's
        seven false drops should have attached to -- "`test_deploy_cos` times
        out after 10 minutes" is the third comment of four. Taking the last
        MAX_RECENT_COMMENTS rather than the last one catches that without
        anything having to guess which comment is the useful one.
        """
        out: list[str] = []
        for raw in reversed(self.comments):
            text = ' '.join(_HTML_COMMENT.sub('', raw).split())
            if text:
                out.append(text[:MAX_COMMENT_CHARS])
            if len(out) >= MAX_RECENT_COMMENTS:
                break
        return list(reversed(out))
