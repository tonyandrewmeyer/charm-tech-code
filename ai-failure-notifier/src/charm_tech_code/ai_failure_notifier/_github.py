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


"""Everything that shells out to `gh`."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from . import _summary
from ._markers import find_run_markers
from ._models import CandidateIssue, FailedJob


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a `gh` subcommand, returning the completed process."""
    # S607: `gh` is deliberately called by name, resolved from the runner's PATH.
    return subprocess.run(['gh', *args], text=True, capture_output=True, check=check)  # noqa: S607


def gh_json(*args: str) -> Any:
    """Run a `gh ... --json ...` subcommand and parse its stdout as JSON."""
    result = gh(*args)
    return json.loads(result.stdout) if result.stdout.strip() else None


def fetch_failed_jobs(repo: str, run_id: str) -> list[FailedJob]:
    """List the failed jobs of a run, each with its id, name, and failed step."""
    data = gh_json('run', 'view', str(run_id), '--repo', repo, '--json', 'jobs') or {}
    failed: list[FailedJob] = []
    for job in data.get('jobs', []):
        if job.get('conclusion') != 'failure':
            continue
        failed_step = None
        for step in job.get('steps') or []:
            if step.get('conclusion') == 'failure':
                failed_step = step.get('name')
                break
        failed.append(FailedJob(id=job['databaseId'], name=job['name'], failed_step=failed_step))
    return failed


def fetch_job_log(repo: str, run_id: str, job_id: int) -> str:
    """Fetch one job's full log text.

    Uses the REST logs endpoint rather than `gh run view --log`: the latter
    exits 0 with empty stdout on some `gh` builds (reproduced on 2.45.0), which
    silently degrades the extracted signature to nothing. An empty log here is
    reported rather than swallowed.

    `--allow-escape-sequences` is not optional. From gh 2.9x, `gh api` refuses
    to write a response containing terminal escapes -- "the response contains
    terminal escape sequences; pass --allow-escape-sequences to output it
    anyway" -- and returns nothing at all. Actions logs are full of them; ANSI
    above exists to strip them. Runners carry a gh new enough to refuse (2.97.0
    when this was measured, in fork run 32673538357), so without the flag every
    fetch comes back empty and the signature degrades to the job name.

    Older builds have no such check and no such flag, and reject it as unknown
    rather than ignoring it, so those retry without.
    """
    endpoint = f'repos/{repo}/actions/jobs/{job_id}/logs'
    result = gh('api', endpoint, '--allow-escape-sequences', check=False)
    if 'unknown flag' in (result.stderr or ''):
        result = gh('api', endpoint, check=False)
    if not result.stdout.strip():
        # `gh` puts the status on stderr ("gh: Not Found (HTTP 404)"), and the
        # exit code alone is 1 for all of them. Without the status there is no
        # telling a log that is not ready yet from a token that has lost
        # `actions: read`, and the two want opposite fixes.
        detail = ' '.join((result.stderr or '').split())[:200] or 'no stderr'
        _summary.write_step_summary(
            f'Warning: no log text for job {job_id} of run {run_id} '
            f'(gh exit {result.returncode}: {detail}); '
            f'signature will be based on the job name alone.'
        )
    return result.stdout


def fetch_run_meta(repo: str, run_id: str) -> dict[str, str]:
    """Fetch a run's display metadata (title, workflow name, url, createdAt)."""
    return (
        gh_json(
            'run',
            'view',
            str(run_id),
            '--repo',
            repo,
            '--json',
            'displayTitle,workflowName,url,createdAt',
        )
        or {}
    )


def fetch_issue_texts(repo: str, number: int) -> list[str]:
    """Fetch an issue's body plus all comment bodies, for marker scanning."""
    data = gh_json('issue', 'view', str(number), '--repo', repo, '--json', 'body,comments') or {}
    texts = [data.get('body') or '']
    for c in data.get('comments') or []:
        texts.append(c.get('body') or '')
    return texts


def resolve_origin(
    repo: str, run_id: str, notify_issue: int, notify_origin: str | None
) -> tuple[int | None, str | None, int]:
    """Work out which artefact this run should upgrade.

    Returns the same triple as `find_run_markers`.

    The notifier tells us the issue it created or commented on, and that is
    authoritative for `origin_issue`/`origin_kind`: we never search for it.
    That is what keeps the read-your-writes hazard out of this script. The
    notifier stamps its marker moments before this job runs, and GitHub's
    issue *search* index lags, so a marker that has not been indexed yet
    reads as "no notifier marker found" and main() opens a second issue for a
    run that already has one. A number passed directly through the workflow
    cannot be stale.

    Rung zero still needs a lookup, and the notifier's output cannot supply
    it: "this run id was already fully enriched" is a fact about an *earlier
    run of this script*, not about what the notifier just did. Knowing the
    issue keeps that lookup to reading the one issue we were handed.
    """
    texts = [(notify_issue, text) for text in fetch_issue_texts(repo, notify_issue)]
    enriched_issue, origin_kind, origin_issue = find_run_markers(texts, run_id)
    # The passed-in values win: a marker we failed to find on the issue does
    # not make the issue the wrong one.
    return enriched_issue, notify_origin or origin_kind, notify_issue


def search_candidates(
    repo: str, workflow_name: str
) -> tuple[list[CandidateIssue], list[CandidateIssue]]:
    """Coarse candidate search: open and closed issues matching the workflow name."""
    fields = 'number,title,body,createdAt,closedAt'
    open_issues = (
        gh_json(
            'issue',
            'list',
            '--repo',
            repo,
            '--state',
            'open',
            '--search',
            f'"{workflow_name}"',
            '--json',
            fields,
            '--limit',
            '20',
        )
        or []
    )
    closed_issues = (
        gh_json(
            'issue',
            'list',
            '--repo',
            repo,
            '--state',
            'closed',
            '--search',
            f'"{workflow_name}"',
            '--json',
            fields,
            '--limit',
            '20',
        )
        or []
    )
    return (
        [CandidateIssue.from_gh(i) for i in open_issues],
        [CandidateIssue.from_gh(i) for i in closed_issues],
    )


def existing_labels(repo: str) -> set[str]:
    """Return the set of label names that already exist in `repo`."""
    data = gh_json('label', 'list', '--repo', repo, '--json', 'name', '--limit', '100') or []
    return {item['name'] for item in data}


def filter_labels(labels: list[str], available: set[str]) -> list[str]:
    """Drop labels that don't already exist in the repo (never auto-create)."""
    return [label for label in labels if label in available]


def existing_issue_types(repo: str) -> set[str]:
    """Return the issue type names enabled for `repo`, which may be none.

    Issue types come from the owning organisation, so a repo can have none at
    all: a personal fork returns `null` here, and so does any repo whose org
    has not enabled them.
    """
    owner, _, name = repo.partition('/')
    query = (
        'query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) '
        '{ issueTypes(first: 50) { nodes { name isEnabled } } } }'
    )
    data = (
        gh_json(
            'api', 'graphql', '-f', f'query={query}', '-F', f'owner={owner}', '-F', f'name={name}'
        )
        or {}
    )
    repository = (data.get('data') or {}).get('repository') or {}
    types = repository.get('issueTypes') or {}
    return {node['name'] for node in types.get('nodes') or [] if node.get('isEnabled')}


def match_issue_type(issue_type: str | None, available: set[str]) -> str | None:
    """Resolve `issue_type` to the repo's own spelling, or `None` if it has no such type.

    The model is asked for a lowercase name, and GitHub's are capitalised, so
    the match ignores case and the repo's spelling is what gets passed on.
    """
    if not issue_type:
        return None
    folded = issue_type.casefold()
    return next((name for name in sorted(available) if name.casefold() == folded), None)
