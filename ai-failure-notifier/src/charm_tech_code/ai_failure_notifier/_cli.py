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


"""Entry point."""

from __future__ import annotations

import dataclasses
import datetime
import os
import sys
from typing import Any

from . import _github, _openrouter, _prompt, _summary
from ._apply import apply_entry, plain_fallback_body, render_body
from ._candidates import build_candidates_block
from ._constants import DEFAULT_MODEL, MARKER_PREFIX
from ._envelope import normalise_envelope, validate_envelope
from ._markers import render_enriched_marker, render_signature_stamp
from ._models import RunSignature
from ._signatures import build_job_signature, build_run_signature


@dataclasses.dataclass(frozen=True)
class _RunConfig:
    """The environment `main` runs with, read once up front."""

    repo: str
    run_id: str
    workflow_name: str
    run_url: str
    api_key: str
    model: str
    # What the notifier did. `NOTIFY_ISSUE` is required: this script upgrades
    # the artefact the notifier just made, and is not in the business of
    # going looking for it.
    notify_issue: int
    notify_origin: str | None


def _read_config() -> _RunConfig:
    """Read the workflow's environment into a `_RunConfig`."""
    return _RunConfig(
        repo=os.environ['REPO'],
        run_id=str(os.environ['RUN_ID']),
        workflow_name=os.environ['WORKFLOW_NAME'],
        run_url=os.environ['RUN_URL'],
        api_key=os.environ.get('OPENROUTER_API_KEY', ''),
        model=os.environ.get('OPENROUTER_MODEL') or DEFAULT_MODEL,
        notify_issue=int(os.environ['NOTIFY_ISSUE']),
        notify_origin=os.environ.get('NOTIFY_ORIGIN') or None,
    )


def _resolve_origin(config: _RunConfig) -> tuple[int | None, str | None, int]:
    """Locate the run's marker, degrading to "un-marked" if the lookup fails."""
    try:
        return _github.resolve_origin(
            config.repo, config.run_id, config.notify_issue, config.notify_origin
        )
    except Exception as exc:  # API rejection, rate limit, transient 5xx.
        # Nothing catches this above us: there is no workflow-level fallback
        # job any more, so an uncaught failure here loses the enrichment
        # outright rather than degrading through the paths below. We still
        # know the notifier's issue, so carry on with that.
        _summary.write_step_summary(
            f'Marker lookup failed ({exc}); treating this run as un-marked.'
        )
        return None, config.notify_origin, config.notify_issue


def _comment_on_rerun(config: _RunConfig, enriched_issue: int) -> None:
    """Rung zero: a re-run of the same failing jobs re-triggered us.

    Comment, don't skip and don't redo the full LLM pass. On a corpus of past
    scheduled failures this rung accounted for half the real duplicate pairs,
    making it the highest-value one.
    """
    _github.gh(
        'issue',
        'comment',
        str(enriched_issue),
        '--repo',
        config.repo,
        '--body',
        f'Re-run attempt still failing: {config.run_url}\n\n'
        f'<!-- {MARKER_PREFIX}:run={config.run_id} -->',
    )
    _summary.write_step_summary(
        f'Rung zero: run {config.run_id} already enriched on #{enriched_issue}; '
        'commented re-run note.'
    )


def _build_run_signature(config: _RunConfig) -> RunSignature:
    """Fetch the run's failed jobs and metadata, and reduce them to a signature."""
    failed_jobs = _github.fetch_failed_jobs(config.repo, config.run_id)
    jobs_sig = [
        build_job_signature(
            job.id,
            job.name,
            job.failed_step,
            _github.fetch_job_log(config.repo, config.run_id, job.id),
        )
        for job in failed_jobs
    ]
    meta = _github.fetch_run_meta(config.repo, config.run_id)
    return build_run_signature(
        config.run_id, config.workflow_name, config.run_url, meta.get('createdAt', ''), jobs_sig
    )


def _plain_fallback_entry(config: _RunConfig, origin_kind: str | None, origin_issue: int) -> Any:
    """Build the envelope-shaped entry `apply_entry` uses when there is no LLM output."""
    if origin_kind == 'comment':
        return {
            'action': 'comment',
            'body': plain_fallback_body(config.workflow_name),
            'target_issue': origin_issue,
        }
    return {
        'action': 'new',
        'body': plain_fallback_body(config.workflow_name),
        'title': f"Scheduled workflow '{config.workflow_name}' failed",
        'labels': [],
        'issue_type': None,
    }


def _apply_plain_fallback(
    config: _RunConfig, origin_kind: str | None, origin_issue: int, trailer: str
) -> None:
    """Apply the plain fallback entry against `origin_issue`."""
    apply_entry(
        config.repo,
        _plain_fallback_entry(config, origin_kind, origin_issue),
        trailer,
        config.workflow_name,
        config.run_url,
        default_target=origin_issue,
    )


def _search_candidates(config: _RunConfig, origin_kind: str | None, origin_issue: int) -> str:
    """Build the {{CANDIDATES_BLOCK}} for the prompt, degrading to "none" on search failure."""
    try:
        open_candidates, closed_candidates = _github.search_candidates(
            config.repo, config.workflow_name
        )
    except Exception as exc:  # as above: degrade to "no candidates", don't crash.
        _summary.write_step_summary(
            f'Candidate search failed ({exc}); proceeding with no candidates.'
        )
        open_candidates, closed_candidates = [], []
    if origin_kind == 'new':
        # The placeholder this run just created is not a candidate to dedupe
        # against. An issue the notifier *commented* on is a different matter:
        # it already existed, the coarse search matched it, and it is the most
        # likely duplicate -- dropping it left the model blind to the very
        # issue it should have been comparing against, so it answered "new"
        # and produced the duplicate this whole path exists to avoid.
        open_candidates = [c for c in open_candidates if c.number != origin_issue]
    return build_candidates_block(
        open_candidates, closed_candidates, datetime.datetime.now(datetime.timezone.utc)
    )


def _fetch_envelope(
    config: _RunConfig, origin_kind: str | None, origin_issue: int, signature: RunSignature
) -> Any:
    """Ask the LLM to triage the failure, returning `None` on any failure along the way."""
    candidates_block = _search_candidates(config, origin_kind, origin_issue)
    system_prompt, user_prompt = _prompt.build_prompt(
        config.workflow_name, config.run_url, signature, candidates_block
    )

    try:
        envelope = _openrouter.call_openrouter(
            system_prompt, user_prompt, config.model, config.api_key
        )
    except Exception as exc:  # network error, non-2xx, bad JSON, and so on.
        _summary.write_step_summary(
            f'OpenRouter call failed ({exc}); using the plain fallback body.'
        )
        return None

    envelope, dropped_fields = normalise_envelope(envelope)
    if dropped_fields:
        _summary.write_step_summary(
            'Ignored fields the applier does not act on: ' + ', '.join(dropped_fields) + '.'
        )

    errors = validate_envelope(envelope)
    if errors:
        _summary.write_step_summary(
            'LLM output failed schema validation:\n' + '\n'.join(f'- {e}' for e in errors)
        )
        return None

    return envelope


def _apply_envelope(
    config: _RunConfig,
    envelope: Any,
    origin_kind: str | None,
    origin_issue: int,
    enriched_marker: str,
    trailer: str,
) -> None:
    """Act on a validated LLM envelope: upgrade, comment, or open a new issue.

    `trailer` is the enriched marker plus this run's signature stamp, and goes
    on every artefact that is *about* this failure. The two pointer notes below
    get the bare `enriched_marker` instead: they are posted on an issue this
    failure was decided not to belong to, and stamping that issue with this
    signature would tell the next run's candidate block the opposite.
    """
    if envelope['action'] == 'new' and origin_kind == 'new':
        # Upgrade the placeholder in place rather than creating a duplicate.
        available = _github.existing_labels(config.repo)
        labels = _github.filter_labels(envelope.get('labels') or [], available)
        edit_args = [
            'issue',
            'edit',
            str(origin_issue),
            '--repo',
            config.repo,
            '--title',
            envelope['title'],
            '--body',
            render_body(envelope['body'], config.workflow_name, config.run_url, trailer),
        ]
        for label in labels:
            edit_args += ['--add-label', label]
        _github.gh(*edit_args)
    elif envelope['action'] == 'comment' and envelope.get('target_issue') == origin_issue:
        apply_entry(
            config.repo,
            envelope,
            trailer,
            config.workflow_name,
            config.run_url,
            default_target=origin_issue,
        )
    elif envelope['action'] == 'comment':
        # LLM picked a different candidate than the notifier's coarse match.
        apply_entry(config.repo, envelope, trailer, config.workflow_name, config.run_url)
        if origin_kind == 'comment':
            _github.gh(
                'issue',
                'comment',
                str(origin_issue),
                '--repo',
                config.repo,
                '--body',
                f'This looks like a distinct issue -- see #{envelope["target_issue"]}.\n\n'
                f'Run: {config.run_url}\n\n{enriched_marker}',
            )
    else:
        # action == "new" but origin_kind == "comment": the coarse title
        # match landed on an unrelated older issue; this is genuinely new.
        apply_entry(config.repo, envelope, trailer, config.workflow_name, config.run_url)
        _github.gh(
            'issue',
            'comment',
            str(origin_issue),
            '--repo',
            config.repo,
            '--body',
            f'This looks like a distinct issue from this one -- opened separately.\n\n'
            f'Run: {config.run_url}\n\n{enriched_marker}',
        )

    for also_entry in envelope.get('also') or []:
        apply_entry(config.repo, also_entry, trailer, config.workflow_name, config.run_url)


def main() -> int:
    """Entry point: locate the run's marker, enrich or fall back, apply, and exit."""
    config = _read_config()

    enriched_issue, origin_kind, origin_issue = _resolve_origin(config)

    if enriched_issue is not None:
        _comment_on_rerun(config, enriched_issue)
        return 0

    signature = _build_run_signature(config)
    enriched_marker = render_enriched_marker(config.run_id, signature)
    # The marker says "this run was enriched here"; the stamp says what the
    # failure was. Both are hidden, and both go on every artefact about this
    # failure -- the stamp so that when this artefact turns up in a later run's
    # candidate pool, the block can show its test ids and error classes instead
    # of the one line of placeholder body it would otherwise be reduced to.
    trailer = f'{enriched_marker}\n{render_signature_stamp(config.run_id, signature)}'

    if not config.api_key:
        _summary.write_step_summary(
            'No OPENROUTER_API_KEY configured -- using the plain fallback body.'
        )
        _apply_plain_fallback(config, origin_kind, origin_issue, trailer)
        return 0

    envelope = _fetch_envelope(config, origin_kind, origin_issue, signature)
    if envelope is None:
        _apply_plain_fallback(config, origin_kind, origin_issue, trailer)
        return 0

    _apply_envelope(config, envelope, origin_kind, origin_issue, enriched_marker, trailer)
    return 0


if __name__ == '__main__':
    sys.exit(main())
