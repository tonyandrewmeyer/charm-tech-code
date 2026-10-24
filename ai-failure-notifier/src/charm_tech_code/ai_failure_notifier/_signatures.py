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


"""Turning raw job logs into a deterministic failure signature."""

from __future__ import annotations

from ._constants import (
    ANSI,
    ERROR_MARKER,
    GO_FAIL,
    GROUP_END,
    GROUP_RUN_START,
    MAX_UNHEALTHY_STATUSES,
    PYTEST_SUMMARY,
    PYTEST_SUMMARY_END,
    STATUS_ENTITY,
    STATUS_FIELDS,
    STATUS_INFO,
    STATUS_INFO_END,
    TRACEBACK_END,
    TS,
)
from ._models import JobSignature, PytestFailure, RunSignature


def strip_line(line: str) -> str:
    """Remove GHA timestamp and ANSI colours."""
    line = TS.sub('', line, count=1)
    line = ANSI.sub('', line)
    return line.rstrip('\r\n')


def strip_log(text: str) -> list[str]:
    """Split a raw job log into output lines, dropping each step's own script.

    Everything between "##[group]Run ..." and "##[endgroup]" is the runner
    echoing the step's `run:` block and env, not anything the step printed.
    See GROUP_RUN_START above for why that matters.
    """
    lines: list[str] = []
    in_step_header = False
    for raw in text.splitlines():
        line = strip_line(raw)
        if GROUP_RUN_START.match(line):
            in_step_header = True
            continue
        if in_step_header:
            in_step_header = not GROUP_END.match(line)
            continue
        lines.append(line)
    return lines


def parse_job_log(
    text: str,
) -> tuple[list[PytestFailure], list[str], str | None, list[str]]:
    """Parse one job's raw log text.

    Returns (pytest_failures, go_failures, traceback_top_error, tail_excerpt).
    """
    lines = strip_log(text)

    pytest_failures: list[PytestFailure] = []
    go_failures: list[str] = []
    in_summary = False

    for line in lines:
        if 'short test summary info' in line:
            in_summary = True
            continue
        if in_summary:
            m = PYTEST_SUMMARY.match(line)
            if m:
                kind, test, err = m.groups()
                pytest_failures.append(PytestFailure(kind, test, err.strip()))
                continue
            if PYTEST_SUMMARY_END.match(line):
                in_summary = False
        m = GO_FAIL.match(line)
        if m:
            go_failures.append(m.group(1))

    traceback_top_error: str | None = None
    for line in reversed(lines):
        m = TRACEBACK_END.match(line)
        if m:
            traceback_top_error = f'{m.group(1)}: {m.group(2).strip()}'
            break

    error_idx = None
    for i, line in enumerate(lines):
        if ERROR_MARKER.search(line):
            error_idx = i
            break
    tail: list[str] = []
    if error_idx is not None:
        for line in reversed(lines[:error_idx]):
            if not line.strip():
                continue
            if line.startswith('##[group]') or line.startswith('##[endgroup]'):
                continue
            tail.append(line)
            if len(tail) >= 40:
                break
        tail.reverse()

    return pytest_failures, go_failures, traceback_top_error, tail


def parse_unhealthy_statuses(text: str) -> list[str]:
    """Find the apps and units in error or blocked in pytest's summary section.

    Only the summary is read: the same `Status(...)` also appears in the
    traceback above it, and reading both would list everything twice. Returns
    entries like 'tempo/0: error (hook failed: "start")', in the order they
    appear, without repeats.
    """
    found: list[str] = []
    in_summary = False
    entity: str | None = None
    # The field lines of a StatusInfo that jubilant split over several lines.
    split_fields: list[str] | None = None
    for line in strip_log(text):
        if 'short test summary info' in line:
            in_summary = True
            continue
        if not in_summary:
            continue
        if PYTEST_SUMMARY_END.match(line):
            in_summary = False
            entity = None
            split_fields = None
            continue
        if split_fields is not None:
            if not STATUS_INFO_END.match(line):
                split_fields.append(line.strip())
                continue
            fields = ' '.join(split_fields)
            split_fields = None
        else:
            m = STATUS_ENTITY.match(line)
            if m:
                entity = m.group('name')
                continue
            m = STATUS_INFO.match(line)
            if not m or entity is None:
                continue
            fields = m.group('fields')
            if not fields:
                split_fields = []
                continue
        m = STATUS_FIELDS.match(fields)
        if m:
            entry = f'{entity}: {m.group("current")}'
            if m.group('message'):
                entry += f' ({m.group("message")})'
            if entry not in found:
                found.append(entry)
            if len(found) >= MAX_UNHEALTHY_STATUSES:
                break
    return found


def build_job_signature(
    job_id: int, job_name: str, failed_step: str | None, log_text: str
) -> JobSignature:
    """Parse one job's log into its signature."""
    pytest_failures, go_failures, traceback_top_error, tail = parse_job_log(log_text)
    return JobSignature(
        job_id=job_id,
        job_name=job_name,
        failed_step=failed_step,
        pytest_failures=pytest_failures,
        go_failures=go_failures,
        traceback_top_error=traceback_top_error,
        tail_excerpt=tail,
        unhealthy_statuses=parse_unhealthy_statuses(log_text),
    )


def build_run_signature(
    run_id: str, workflow_name: str, html_url: str, created_at: str, jobs: list[JobSignature]
) -> RunSignature:
    """Combine per-job signatures into the full run signature."""
    return RunSignature(
        run_id=str(run_id),
        workflow_name=workflow_name,
        html_url=html_url,
        created_at=created_at,
        jobs=jobs,
    )
