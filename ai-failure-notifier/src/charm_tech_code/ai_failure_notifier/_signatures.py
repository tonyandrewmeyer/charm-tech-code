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


class _StatusCollector:
    """Pick the apps and units in error, failed or blocked out of a `Status(...)` dump.

    Fed one line at a time from pytest's summary section. `found()` returns
    entries like 'tempo/0: error (hook failed: "start")', in the order they
    appear, without repeats.
    """

    def __init__(self) -> None:
        self._found: list[tuple[str, str]] = []  # (current, entry)
        self._entity: str | None = None
        # The field lines of a StatusInfo that jubilant split over several lines.
        self._split_fields: list[str] | None = None

    def end_summary(self) -> None:
        self._entity = None
        self._split_fields = None

    def feed(self, line: str) -> None:
        if self._split_fields is not None:
            if not STATUS_INFO_END.match(line):
                self._split_fields.append(line.strip())
                return
            fields = ' '.join(self._split_fields)
            self._split_fields = None
        else:
            m = STATUS_ENTITY.match(line)
            if m:
                self._entity = m.group('name')
                return
            m = STATUS_INFO.match(line)
            if not m or self._entity is None:
                return
            fields = m.group('fields')
            if not fields:
                self._split_fields = []
                return
        m = STATUS_FIELDS.match(fields)
        if not m:
            return
        current = m.group('current')
        entry = f'{self._entity}: {current}'
        if m.group('message'):
            entry += f' ({m.group("message")})'
        if (current, entry) not in self._found:
            self._found.append((current, entry))

    def found(self) -> list[str]:
        # Past the cap, drop blocked entries first: error and failed are the
        # stronger evidence, and one app with many units shouldn't hide them.
        kept = [i for i, (current, _) in enumerate(self._found) if current != 'blocked']
        kept += [i for i, (current, _) in enumerate(self._found) if current == 'blocked']
        return [self._found[i][1] for i in sorted(kept[:MAX_UNHEALTHY_STATUSES])]


def parse_job_log(
    text: str,
) -> tuple[list[PytestFailure], list[str], str | None, list[str], list[str]]:
    """Parse one job's raw log text.

    Returns (pytest_failures, go_failures, traceback_top_error, tail_excerpt,
    unhealthy_statuses).

    The unhealthy statuses are only read from pytest's summary section: the
    same `Status(...)` also appears in the traceback above it, and reading
    both would list everything twice.
    """
    lines = strip_log(text)

    pytest_failures: list[PytestFailure] = []
    go_failures: list[str] = []
    statuses = _StatusCollector()
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
                statuses.end_summary()
            else:
                statuses.feed(line)
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

    return pytest_failures, go_failures, traceback_top_error, tail, statuses.found()


def parse_unhealthy_statuses(text: str) -> list[str]:
    """Find the apps and units in error, failed or blocked in pytest's summary section."""
    return parse_job_log(text)[4]


def build_job_signature(
    job_id: int, job_name: str, failed_step: str | None, log_text: str
) -> JobSignature:
    """Parse one job's log into its signature."""
    pytest_failures, go_failures, traceback_top_error, tail, statuses = parse_job_log(log_text)
    return JobSignature(
        job_id=job_id,
        job_name=job_name,
        failed_step=failed_step,
        pytest_failures=pytest_failures,
        go_failures=go_failures,
        traceback_top_error=traceback_top_error,
        tail_excerpt=tail,
        unhealthy_statuses=statuses,
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
