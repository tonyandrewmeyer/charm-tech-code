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

"""Unit tests for scripts/ai_failure_notifier.py.

No network calls and no `gh` calls happen in this file -- OpenRouter and gh
I/O are mocked. FIXTURE below is the extracted signature, candidate issue and
LLM envelope from a real failing scheduled run (28141163589, "Broad Charm
Compatibility Tests", 2026-06-25); the `tail_excerpt` arrays are trimmed for
size; `pytest_failures` and `traceback_top_error` are verbatim, since those are
what the dedup and schema logic actually exercise.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import email.message
import io
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from typing import Any
from unittest import mock

from charm_tech_code.ai_failure_notifier import (
    _apply,
    _candidates,
    _cli,
    _constants,
    _envelope,
    _github,
    _markers,
    _models,
    _openrouter,
    _prompt,
    _signatures,
    _summary,
)

# The model a faked OpenRouter call says answered: the fallback, so that a test
# reading it back can tell it from the configured model.
ANSWERED_BY = 'deepseek/deepseek-v3.2'

# The signature, candidate issue and envelope from a real failing scheduled run
# (28141163589, "Broad Charm Compatibility Tests", 2026-06-25). The tail_excerpt
# lists are trimmed for size; pytest_failures and traceback_top_error are
# verbatim, since those are what the dedup and schema logic exercise.
FIXTURE_SIGNATURE = _models.RunSignature(
    run_id='28141163589',
    workflow_name='Broad Charm Compatibility Tests',
    html_url='https://github.com/example/repo/actions/runs/28141163589',
    created_at='2026-06-25T01:40:15Z',
    jobs=[
        _models.JobSignature(
            job_id=83338922280,
            job_name='charm-tests (canonical/charm-ubuntu, .)',
            failed_step="Run the charm's unit tests",
            pytest_failures=[
                _models.PytestFailure(
                    kind='ERROR',
                    test='tests/unit/test_charm.py::TestCharm::test_charm_ready',
                    error='PendingDeprecat...',
                ),
                _models.PytestFailure(
                    kind='ERROR',
                    test='tests/unit/test_charm.py::TestCharm::test_hostname',
                    error='PendingDeprecation...',
                ),
                _models.PytestFailure(
                    kind='ERROR',
                    test='tests/unit/test_charm.py::TestCharm::test_version',
                    error='PendingDeprecationW...',
                ),
            ],
            go_failures=[],
            traceback_top_error=(
                'ResourceWarning: Implicitly cleaning up <TemporaryDirectory '
                "'/tmp/ops-harness-ruod78qd'>"
            ),
            tail_excerpt=[
                'pytest.PytestUnraisableExceptionWarning: Exception ignored in: <finalize object '
                'at 0x7f2f582dea60; dead>',
                'unit: FAIL code 1 (2.04=setup[1.23]+cmd[0.81] seconds)',
                'evaluation failed :( (2.07 seconds)',
            ],
        ),
        _models.JobSignature(
            job_id=83338922315,
            job_name='charm-tests (canonical/k8s-operator, charms/worker/k8s)',
            failed_step="Run the charm's static tests",
            pytest_failures=[],
            go_failures=[],
            traceback_top_error=None,
            tail_excerpt=[
                'Total issues (by severity):',
                'static: FAIL code 1 (16.01=setup[0.42]+cmd[15.59] seconds)',
                'evaluation failed :( (16.06 seconds)',
            ],
        ),
        _models.JobSignature(
            job_id=83338923301,
            job_name='charm-tests (canonical/seldon-core-operator, .)',
            failed_step="Run the charm's unit tests",
            pytest_failures=[
                _models.PytestFailure(
                    kind='FAILED',
                    test='tests/unit/test_operator.py::TestCharm::test_prometheus_data_set',
                    error=(
                        "AttributeError: module 'ops.testing' has no attribute "
                        "'_TestingModelBackend'"
                    ),
                ),
            ],
            go_failures=[],
            traceback_top_error=(
                "AttributeError: module 'ops.testing' has no attribute '_TestingModelBackend'"
            ),
            tail_excerpt=[
                'FAILED tests/unit/test_operator.py::TestCharm::test_prometheus_data_set - '
                "AttributeError: module 'ops.testing' has no attribute '_TestingModelBackend'",
                'unit: FAIL code 1 (8.95=setup[0.60]+cmd[8.35] seconds)',
            ],
        ),
        _models.JobSignature(
            job_id=83338923304,
            job_name='charm-tests (canonical/self-signed-certificates-operator, .)',
            failed_step="Run the charm's unit tests",
            pytest_failures=[],
            go_failures=[],
            traceback_top_error=None,
            tail_excerpt=[
                'ERROR tests/unit/test_charm_collect_status.py',
                '!!!!!!!!!!!!!!!!!!! Interrupted: 5 errors during collection !!!!!!!!!!!!!!!!!!!!',
                'unit: FAIL code 2 (3.43=setup[1.18]+cmd[0.06,0.02,2.16] seconds)',
            ],
        ),
        _models.JobSignature(
            job_id=83338923318,
            job_name='charm-tests (canonical/traefik-k8s-operator, .)',
            failed_step="Run the charm's unit tests",
            pytest_failures=[],
            go_failures=[],
            traceback_top_error=(
                "ImportError: cannot import name 'Event' from 'scenario' (/home/runner/work/operat"
                'or/operator/charm-repo/.tox/unit/lib/python3.12/site-packages/scenario/__init__.p'
                'y)'
            ),
            tail_excerpt=[
                (
                    "ImportError: cannot import name 'Event' from 'scenario' "
                    '(/home/runner/work/operator/operator/charm-repo/.tox/unit/lib/python3.12/site'
                    '-packages/scenario/__init__.py)'
                ),
                'unit: FAIL code 1 (6.43=setup[2.14]+cmd[0.06,0.02,4.21] seconds)',
            ],
        ),
    ],
)

FIXTURE_CANDIDATES = [
    _models.CandidateIssue(
        number=9010,
        title='Broad Charm Compatibility Tests: 4 downstream charms failing, independent causes',
        body=(
            'canonical/k8s-operator: bandit exit 1 (Medium: 24, High: 591). '
            "canonical/seldon-core-operator: AttributeError: module 'ops.testing' has no attribute"
            " '_TestingModelBackend'. canonical/self-signed-certificates-operator: 5 collection "
            "errors. canonical/traefik-k8s-operator: ImportError: cannot import name 'Event' from "
            "'scenario'."
        ),
        closed_at=None,
    ),
]

FIXTURE_ENVELOPE: dict[str, Any] = {
    'action': 'comment',
    'target_issue': 9010,
    'body': (
        'Another occurrence: '
        'https://github.com/example/repo/actions/runs/28141163589\n'
        '\n'
        '4 of the 5 failing charms match #9010 unchanged. New this run: '
        'canonical/charm-ubuntu is now also failing its unit tests.'
    ),
    'dedup_reason': (
        "4 of 5 failing charms match #9010's signature; charm-ubuntu is a "
        'new failure not present in #9010'
    ),
    'confidence': 'medium',
}


class SignatureExtractionTests(unittest.TestCase):
    def test_strip_line_removes_timestamp_and_ansi(self):
        raw = '2026-06-25T01:40:15.8141713Z \x1b[36mhello\x1b[0m'
        self.assertEqual(
            _signatures.strip_line(raw),
            '\x1b[36mhello\x1b[0m'.replace('\x1b[36m', '').replace('\x1b[0m', ''),
        )

    def test_parse_job_log_pytest_failures_and_summary_bound(self):
        ts = '2026-06-25T01:40:15.0000000Z '
        log = '\n'.join([
            f'{ts}============ short test summary info ============',
            f'{ts}FAILED tests/unit/test_x.py::test_a - AssertionError: x',
            f'{ts}ERROR tests/unit/test_x.py::test_b - PendingDeprecat...',
            f'{ts}============ 2 failed in 1.23s ============',
            f'{ts}some trailing noise, not part of the summary',
        ])
        pytest_failures, go_failures, _tb, _tail = _signatures.parse_job_log(log)
        self.assertEqual(
            pytest_failures,
            [
                _models.PytestFailure(
                    'FAILED', 'tests/unit/test_x.py::test_a', 'AssertionError: x'
                ),
                _models.PytestFailure(
                    'ERROR', 'tests/unit/test_x.py::test_b', 'PendingDeprecat...'
                ),
            ],
        )
        self.assertEqual(go_failures, [])

    def test_parse_job_log_go_failures(self):
        log = '--- FAIL: TestFoo (0.03s)\n--- FAIL: TestBar (0.01s)\n'
        _pytest_failures, go_failures, _tb, _tail = _signatures.parse_job_log(log)
        self.assertEqual(go_failures, ['TestFoo', 'TestBar'])

    def test_parse_job_log_traceback_top_error_prefers_last_match(self):
        log = '\n'.join([
            'ValueError: first, ignored',
            'some other output',
            'AttributeError: the real one',
        ])
        _, _, tb, _ = _signatures.parse_job_log(log)
        self.assertEqual(tb, 'AttributeError: the real one')

    def test_parse_job_log_tail_excerpt_stops_before_first_error_marker(self):
        log = '\n'.join([
            'line before 1',
            'line before 2',
            '##[error]something broke',
            'line after (should not appear in tail)',
        ])
        _, _, _, tail = _signatures.parse_job_log(log)
        self.assertEqual(tail, ['line before 1', 'line before 2'])

    def test_parse_job_log_ignores_the_step_script_the_runner_echoes(self):
        # A multi-line `run:` puts every branch into the log, executed or not,
        # so an unexecuted branch must not contribute to the signature.
        log = '\n'.join([
            '##[group]Run case "$SHAPE" in',
            'case "$SHAPE" in',
            '  traceback-only)',
            "KeyError: 'loki/0'",
            '    ;;',
            'esac',
            '##[endgroup]',
            'short test summary info',
            "FAILED test/test_model.py::TestModel::test_thing - KeyError: 'host'",
        ])
        pytest_failures, _go, tb, _tail = _signatures.parse_job_log(log)
        self.assertIsNone(tb)
        self.assertEqual([f.error for f in pytest_failures], ["KeyError: 'host'"])

    def test_parse_job_log_counts_a_failure_once_not_twice(self):
        # The same summary line appears in the echoed script and again in the
        # output; only the output one is a failure that happened.
        log = '\n'.join([
            '##[group]Run tox -e unit',
            'FAILED test/test_model.py::TestModel::test_thing - KeyError',
            '##[endgroup]',
            'short test summary info',
            'FAILED test/test_model.py::TestModel::test_thing - KeyError',
        ])
        pytest_failures, _go, _tb, _tail = _signatures.parse_job_log(log)
        self.assertEqual(len(pytest_failures), 1)

    def test_parse_job_log_keeps_groups_the_step_itself_opened(self):
        # `::group::` from inside a step is output, not the runner's header;
        # only "##[group]Run " starts an echoed script.
        log = '\n'.join([
            '##[group]my own section',
            'AttributeError: this is real output',
            '##[endgroup]',
        ])
        _, _, tb, _ = _signatures.parse_job_log(log)
        self.assertEqual(tb, 'AttributeError: this is real output')

    def test_strip_log_drops_the_env_dump_with_the_script(self):
        log = '\n'.join([
            '##[group]Run uv run scripts/ai_failure_notifier.py',
            'env:',
            '  OPENROUTER_API_KEY: ***',
            '##[endgroup]',
            'real output',
        ])
        self.assertEqual(_signatures.strip_log(log), ['real output'])

    # The shape of jubilant's wait timeout in pytest's summary, trimmed from a
    # real scheduled "ops Integration Tests" failure (39219345374, 2026-10-19):
    # the summary line only says the wait timed out, and the reason is further
    # down, in the `Status(...)` that is the rest of the message.
    WAIT_TIMEOUT_SUMMARY = (
        '=========================== short test summary info ============================',
        'ERROR test/integration/test_tracing.py::test_direct_connection'
        ' - TimeoutError: wait timed out after 900s',
        'Status(',
        '  model=ModelStatus(',
        "    model_status=StatusInfo(current='available', since='19 Oct 2026 08:17:13Z'),",
        '  ),',
        '  apps={',
        "    'minio': AppStatus(",
        "      app_status=StatusInfo(current='active', since='19 Oct 2026 08:16:17Z'),",
        '      units={',
        "        'minio/0': UnitStatus(",
        "          workload_status=StatusInfo(current='active', since='19 Oct 2026 08:16:17Z'),",
        "          juju_status=StatusInfo(current='idle', since='19 Oct 2026 08:02:15Z'),",
        '        ),',
        '      },',
        '    ),',
        "    'tempo': AppStatus(",
        "      app_status=StatusInfo(current='error', message='hook failed: \"start\"',"
        " since='19 Oct 2026 08:01:36Z'),",
        '      units={',
        "        'tempo/0': UnitStatus(",
        "          workload_status=StatusInfo(current='error', message='hook failed: \"start\"',"
        " since='19 Oct 2026 08:01:36Z'),",
        "          juju_status=StatusInfo(current='idle', since='19 Oct 2026 08:01:49Z'),",
        '        ),',
        '      },',
        '    ),',
        "    'tempo-worker': AppStatus(",
        "      app_status=StatusInfo(current='waiting', message='Waiting for coordinator',"
        " since='19 Oct 2026 08:16:15Z'),",
        '    ),',
        '  },',
        '  storage=CombinedStorage(',
        "    filesystems={'0': FilesystemInfo(size=1024)},",
        '  ),',
        ')',
        '======================== 1 error in 1248.60s (0:20:48) =========================',
    )

    def test_unhealthy_statuses_from_a_jubilant_wait_timeout(self):
        statuses = _signatures.parse_unhealthy_statuses('\n'.join(self.WAIT_TIMEOUT_SUMMARY))
        self.assertEqual(
            statuses,
            ['tempo: error (hook failed: "start")', 'tempo/0: error (hook failed: "start")'],
        )

    def test_unhealthy_statuses_reach_the_job_signature(self):
        job = _signatures.build_job_signature(
            1, 'integration', 'Run tox', '\n'.join(self.WAIT_TIMEOUT_SUMMARY)
        )
        self.assertEqual(job.unhealthy_statuses[0], 'tempo: error (hook failed: "start")')
        self.assertIn(
            'unhealthy_statuses',
            json.loads(_signatures.build_run_signature('1', 'w', 'url', '', [job]).as_json())[
                'jobs'
            ][0],
        )

    def test_unhealthy_statuses_ignore_the_traceback_copy_of_the_status(self):
        # With --tb native the same Status(...) is printed in the traceback
        # first; only the summary's copy counts, so nothing is listed twice and
        # a status that only the traceback has is not listed at all.
        log = '\n'.join([
            'Traceback (most recent call last):',
            "    'other': AppStatus(",
            "      app_status=StatusInfo(current='error', message='only in the traceback'),",
            *self.WAIT_TIMEOUT_SUMMARY,
        ])
        self.assertEqual(
            _signatures.parse_unhealthy_statuses(log),
            ['tempo: error (hook failed: "start")', 'tempo/0: error (hook failed: "start")'],
        )

    def test_unhealthy_statuses_blocked_without_a_message_and_double_quoted(self):
        log = '\n'.join([
            'short test summary info',
            'FAILED test/integration/test_charm.py::test_deploy - TimeoutError: wait timed out',
            "    'db': AppStatus(",
            "      app_status=StatusInfo(current='blocked'),",
            "    'app': AppStatus(",
            "      app_status=StatusInfo(current='error', message=\"can't connect\"),",
            '======== 1 failed in 3.00s ========',
        ])
        self.assertEqual(
            _signatures.parse_unhealthy_statuses(log),
            ['db: blocked', "app: error (can't connect)"],
        )

    def test_unhealthy_statuses_are_capped(self):
        lines = ['short test summary info', 'FAILED t.py::t - TimeoutError: wait timed out']
        for i in range(_constants.MAX_UNHEALTHY_STATUSES + 5):
            lines += [f"    'app{i}': AppStatus(", "      app_status=StatusInfo(current='error'),"]
        statuses = _signatures.parse_unhealthy_statuses('\n'.join(lines))
        self.assertEqual(len(statuses), _constants.MAX_UNHEALTHY_STATUSES)

    def test_no_unhealthy_statuses_without_a_status_dump(self):
        log = '\n'.join([
            'short test summary info',
            'FAILED tests/unit/test_x.py::test_a - AssertionError: x',
            '============ 1 failed in 1.23s ============',
        ])
        self.assertEqual(_signatures.parse_unhealthy_statuses(log), [])

    def test_build_run_signature_matches_fixture_shape(self):
        jobs = [
            _signatures.build_job_signature(j.job_id, j.job_name, j.failed_step, '')
            for j in FIXTURE_SIGNATURE.jobs
        ]
        sig = _signatures.build_run_signature(
            '28141163589', 'Broad Charm Compatibility Tests', 'url', '2026-06-25T01:40:15Z', jobs
        )
        self.assertEqual(sig.run_id, '28141163589')
        self.assertEqual(len(sig.jobs), 5)
        # as_json is what reaches the prompt; field order is declaration order.
        self.assertEqual(
            list(json.loads(sig.as_json())),
            ['run_id', 'workflow_name', 'html_url', 'created_at', 'jobs'],
        )


class MarkerTests(unittest.TestCase):
    def test_render_and_parse_notifier_marker(self):
        marker = _markers.render_notifier_marker('123', 'new')
        enriched, origin_kind, origin_issue = _markers.find_run_markers([(42, marker)], '123')
        self.assertIsNone(enriched)
        self.assertEqual(origin_kind, 'new')
        self.assertEqual(origin_issue, 42)

    def test_render_and_parse_enriched_marker_is_rung_zero(self):
        marker = _markers.render_enriched_marker('28141163589', FIXTURE_SIGNATURE)
        run_id = '28141163589'
        enriched, origin_kind, _origin_issue = _markers.find_run_markers([(9010, marker)], run_id)
        self.assertEqual(enriched, 9010)
        self.assertIsNone(origin_kind)

    def test_marker_for_different_run_id_does_not_match(self):
        marker = _markers.render_notifier_marker('999', 'comment')
        enriched, origin_kind, origin_issue = _markers.find_run_markers([(1, marker)], '123')
        self.assertIsNone(enriched)
        self.assertIsNone(origin_kind)
        self.assertIsNone(origin_issue)

    def test_signature_hash_is_deterministic_and_order_independent_of_call(self):
        h1 = _markers.signature_hash(FIXTURE_SIGNATURE)
        h2 = _markers.signature_hash(FIXTURE_SIGNATURE)  # independently constructed
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)

    def test_signature_fields_are_the_four_things_the_rungs_match_on(self):
        fields = _markers.signature_fields(FIXTURE_SIGNATURE)
        self.assertIn('tests/unit/test_charm.py::TestCharm::test_charm_ready', fields['tests'])
        self.assertIn('AttributeError', fields['errors'])
        self.assertIn('ImportError', fields['errors'])
        self.assertIn("Run the charm's unit tests", fields['steps'])
        self.assertIn('charm-tests (canonical/traefik-k8s-operator, .)', fields['jobs'])
        # `tail_excerpt` is dozens of log lines and has no business in another
        # issue's candidate entry.
        self.assertEqual(set(fields), {'tests', 'errors', 'steps', 'jobs'})

    def test_signature_fields_are_deduplicated_and_capped(self):
        job = _models.JobSignature(
            job_id=1,
            job_name='j',
            failed_step='s',
            pytest_failures=[
                _models.PytestFailure(
                    kind='FAILED', test=f'tests/t.py::test_{i}', error='KeyError: x'
                )
                for i in range(20)
            ],
            go_failures=[],
            traceback_top_error='KeyError: x',
            tail_excerpt=[],
        )
        fields = _markers.signature_fields(dataclasses.replace(FIXTURE_SIGNATURE, jobs=[job]))
        self.assertEqual(len(fields['tests']), _constants.MAX_STAMPED_ITEMS)
        self.assertEqual(fields['errors'], ['KeyError'])
        self.assertEqual(fields['jobs'], ['j'])

    def test_signature_stamp_round_trips(self):
        stamp = _markers.render_signature_stamp('28141163589', FIXTURE_SIGNATURE)
        self.assertTrue(stamp.startswith('<!-- ai-failure-notifications:signature {'))
        parsed = _markers.parse_signature_stamp(f'prose\n\n{stamp}\n\nmore prose')
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed['run'], '28141163589')
        self.assertEqual(parsed['tests'], _markers.signature_fields(FIXTURE_SIGNATURE)['tests'])

    def test_a_signature_stamp_is_not_a_run_marker(self):
        """Rung zero must not see the stamp. MARKER_RE is unchanged and this pins it."""
        stamp = _markers.render_signature_stamp('28141163589', FIXTURE_SIGNATURE)
        self.assertEqual(
            _markers.find_run_markers([(1, stamp)], '28141163589'), (None, None, None)
        )

    # An enriched comment exactly as the current release (bcbd3cc) writes it:
    # the marker and the signature stamp, and no model stamp.
    OLD_ENRICHED_TRAILER = (
        '<!-- ai-failure-notifications:run=123:sig=abcdef0123456789 -->\n'
        '<!-- ai-failure-notifications:signature '
        '{"run":"123","tests":["tests/t.py::test_x"],"errors":["KeyError"],'
        '"steps":["s"],"jobs":["j"]} -->'
    )

    def _new_enriched_trailer(self, model: str = 'deepseek/deepseek-v3.2') -> str:
        marker, stamp = self.OLD_ENRICHED_TRAILER.split('\n')
        return f'{marker}\n{_markers.render_model_stamp(model)}\n{stamp}'

    def test_old_and_new_enriched_trailers_both_parse(self):
        for name, trailer in (
            ('old', self.OLD_ENRICHED_TRAILER),
            ('new', self._new_enriched_trailer()),
        ):
            with self.subTest(name):
                body = f'## Summary\n\nprose\n\nWorkflow: w\nRun: u\n\n{trailer}'
                self.assertEqual(_markers.find_run_markers([(7, body)], '123'), (7, None, None))
                stamp = _markers.parse_signature_stamp(body)
                assert stamp is not None
                self.assertEqual(stamp['tests'], ['tests/t.py::test_x'])
                # Rung zero on the issue the notifier hands over.
                with mock.patch.object(_github, 'fetch_issue_texts', return_value=['', body]):
                    self.assertEqual(
                        _github.resolve_origin('o/r', '123', 7, 'comment'), (7, 'comment', 7)
                    )
                # And the candidate block still shows the signature, and not
                # the model.
                issue = _models.CandidateIssue(
                    number=7, title='t', body='b', closed_at=None, comments=(body,)
                )
                line = _candidates.render_signature_line(issue)
                assert line is not None
                self.assertIn('tests/t.py::test_x', line)
                self.assertNotIn('deepseek', ' '.join(issue.recent_comments()))
        self.assertNotIn(':model', self.OLD_ENRICHED_TRAILER)
        self.assertIn(
            _markers.render_model_stamp('deepseek/deepseek-v3.2'), self._new_enriched_trailer()
        )

    def test_a_model_stamp_is_not_a_run_marker_or_a_signature_stamp(self):
        stamp = _markers.render_model_stamp('deepseek/deepseek-v3.2')
        self.assertEqual(stamp, '<!-- ai-failure-notifications:model deepseek/deepseek-v3.2 -->')
        self.assertEqual(_markers.find_run_markers([(1, stamp)], '123'), (None, None, None))
        self.assertIsNone(_markers.parse_signature_stamp(stamp))

    def test_a_notifier_marker_beside_a_model_stamp_is_still_found(self):
        marker = _markers.render_notifier_marker('123', 'comment')
        body = f'{marker}\n{_markers.render_model_stamp("x/y")}'
        self.assertEqual(_markers.find_run_markers([(5, body)], '123'), (None, 'comment', 5))

    def test_a_model_name_cannot_close_the_comment_early(self):
        stamp = _markers.render_model_stamp('evil --> <b>hi</b>')
        self.assertEqual(stamp.count('-->'), 1)
        self.assertTrue(stamp.endswith(' -->'))
        self.assertEqual(stamp, '<!-- ai-failure-notifications:model evil_--___b_hi_/b_ -->')

    def test_no_marker_present_returns_all_none(self):
        enriched, origin_kind, origin_issue = _markers.find_run_markers(
            [(1, 'just a normal comment, no marker')], '123'
        )
        self.assertIsNone(enriched)
        self.assertIsNone(origin_kind)
        self.assertIsNone(origin_issue)


class CandidateBlockTests(unittest.TestCase):
    """What the model is shown about each candidate, pinned exactly.

    Pinned rather than spot-checked with assertIn, because a block that reduces
    every candidate to a run URL, a heading or `(no body)` leaves the prompt's
    strong rung nothing to match on, and an assertIn suite stays green through
    that. If this test doesn't change when the rendering changes, the rendering
    isn't being reviewed.
    """

    def test_open_candidate_rendered(self):
        block = _candidates.build_candidates_block(
            FIXTURE_CANDIDATES, [], datetime.datetime.now(datetime.timezone.utc)
        )
        self.assertEqual(
            block,
            '- **#9010 — Broad Charm Compatibility Tests: 4 downstream charms failing, '
            'independent causes** (open)\n'
            '  > canonical/k8s-operator: bandit exit 1 (Medium: 24, High: 591). '
            "canonical/seldon-core-operator: AttributeError: module 'ops.testing' has no "
            "attribute '_TestingModelBackend'. "
            'canonical/self-signed-certificates-operator: 5 collection errors. '
            # Cut at MAX_EXCERPT_CHARS, mid-word: the bound is on characters,
            # and a candidate excerpt is not prose anyone reads to the end.
            'canonical/traefik-k8s-operator: ImportError: cannot import na',
        )

    def test_a_notifier_placeholder_candidate_shows_its_comment(self):
        """One placeholder line of body, and the whole diagnosis in the thread."""
        issue = _models.CandidateIssue.from_gh({
            'number': 2633,
            'title': "Scheduled workflow 'Example Charm Integration Tests' failed",
            'body': (
                "Scheduled workflow 'Example Charm Integration Tests' failed: "
                'https://github.com/canonical/operator/actions/runs/28882829315'
            ),
            'closedAt': None,
            'comments': [
                {'body': 'Timeouts and a store failure. Trying a re-run.'},
                {'body': '`test_deploy_cos` times out after 10 minutes.'},
            ],
        })
        block = _candidates.build_candidates_block(
            [issue], [], datetime.datetime.now(datetime.timezone.utc)
        )
        self.assertEqual(
            block,
            "- **#2633 — Scheduled workflow 'Example Charm Integration Tests' failed** (open)\n"
            "  > Scheduled workflow 'Example Charm Integration Tests' failed: "
            'https://github.com/canonical/operator/actions/runs/28882829315\n'
            '  > earlier comment: Timeouts and a store failure. Trying a re-run.\n'
            '  > most recent comment: `test_deploy_cos` times out after 10 minutes.',
        )

    def test_an_issue_this_tool_enriched_shows_its_failure_signature(self):
        stamp = _markers.render_signature_stamp('28882829315', FIXTURE_SIGNATURE)
        issue = _models.CandidateIssue.from_gh({
            'number': 31,
            'title': 'Broad Charm Compatibility Tests: downstream charms failing',
            'body': f'## Summary\n\nFour downstream charms failed.\n\n{stamp}',
            'closedAt': None,
        })
        block = _candidates.build_candidates_block(
            [issue], [], datetime.datetime.now(datetime.timezone.utc)
        )
        # The body excerpt skips the heading the shipped prompt always emits,
        # so it is the summary sentence rather than the word "Summary".
        self.assertIn('  > Four downstream charms failed.', block)
        self.assertIn('  > failure signature (run 28882829315): tests ', block)
        self.assertIn('tests/unit/test_charm.py::TestCharm::test_charm_ready', block)
        self.assertIn('AttributeError', block)
        self.assertIn("steps Run the charm's unit tests", block)
        self.assertNotIn('<!--', block)

    def test_an_issue_nobody_has_enriched_gets_no_signature_line(self):
        """The common case on a tracker this tool has not run against yet.

        Stated as its own test because a fix that only works on issues this
        tool has already touched would be no fix at all for a fresh repo, and
        this is the assertion that says which half of the change is carrying
        the weight there.
        """
        issue = _models.CandidateIssue.from_gh({
            'number': 2641,
            'title': 'Integration tests fail against Juju 4.1/edge',
            'body': "Scheduled workflow 'ops Integration Tests' failed: https://x/runs/1",
            'closedAt': None,
        })
        block = _candidates.build_candidates_block(
            [issue], [], datetime.datetime.now(datetime.timezone.utc)
        )
        self.assertNotIn('failure signature', block)
        self.assertNotIn('most recent comment', block)
        self.assertEqual(
            block,
            '- **#2641 — Integration tests fail against Juju 4.1/edge** (open)\n'
            "  > Scheduled workflow 'ops Integration Tests' failed: https://x/runs/1",
        )

    def test_a_stamp_in_a_comment_is_read_and_the_latest_one_wins(self):
        """An issue the enricher has commented on carries its stamp there, not in the body."""
        first = _markers.render_signature_stamp('111', FIXTURE_SIGNATURE)
        later = _markers.render_signature_stamp(
            '222',
            dataclasses.replace(
                FIXTURE_SIGNATURE,
                jobs=[
                    _models.JobSignature(
                        job_id=1,
                        job_name='k8s-5-observe',
                        failed_step='Run integration tests',
                        pytest_failures=[
                            _models.PytestFailure(
                                kind='FAILED',
                                test='tests/integration/test_charm.py::test_deploy_cos',
                                error='TimeoutError: timed out',
                            )
                        ],
                        go_failures=[],
                        traceback_top_error='TimeoutError: timed out',
                        tail_excerpt=[],
                    )
                ],
            ),
        )
        issue = _models.CandidateIssue.from_gh({
            'number': 31,
            'title': 'COS deploy times out',
            'body': f'Some prose.\n\n{first}',
            'closedAt': None,
            'comments': [{'body': f'Another occurrence.\n\n{later}'}],
        })
        line = _candidates.render_signature_line(issue)
        self.assertIsNotNone(line)
        assert line is not None
        self.assertTrue(line.startswith('failure signature (run 222): '))
        self.assertIn('test_deploy_cos', line)
        self.assertIn('errors TimeoutError', line)
        self.assertNotIn('test_charm_ready', line)

    def test_an_unparseable_stamp_is_treated_as_absent(self):
        issue = _models.CandidateIssue(
            number=5,
            title='t',
            body='<!-- ai-failure-notifications:signature {not json} -->',
            closed_at=None,
        )
        self.assertIsNone(_candidates.render_signature_line(issue))

    def test_empty_candidates_block(self):
        block = _candidates.build_candidates_block(
            [], [], datetime.datetime.now(datetime.timezone.utc)
        )
        self.assertEqual(block, '(no open issues found for this workflow)')

    def test_recently_closed_candidate_is_labelled_and_capped_at_medium(self):
        now = datetime.datetime(2026, 6, 25, tzinfo=datetime.timezone.utc)
        closed = [
            _models.CandidateIssue.from_gh({
                'number': 42,
                'title': 'old thing',
                'body': 'x',
                'closedAt': '2026-06-20T00:00:00Z',
            })
        ]
        block = _candidates.build_candidates_block([], closed, now)
        self.assertIn('#42', block)
        self.assertIn('closed', block)
        self.assertIn('medium-confidence', block)

    def test_closed_candidate_outside_window_is_dropped(self):
        now = datetime.datetime(2026, 6, 25, tzinfo=datetime.timezone.utc)
        closed = [
            _models.CandidateIssue.from_gh({
                'number': 42,
                'title': 'ancient',
                'body': 'x',
                'closedAt': '2026-01-01T00:00:00Z',
            })
        ]
        block = _candidates.build_candidates_block([], closed, now)
        self.assertEqual(block, '(no open issues found for this workflow)')

    def test_candidates_capped_at_three(self):
        opens = [
            _models.CandidateIssue.from_gh({
                'number': n,
                'title': f'issue {n}',
                'body': 'x',
                'closedAt': None,
            })
            for n in range(5)
        ]
        block = _candidates.build_candidates_block(
            opens, [], datetime.datetime.now(datetime.timezone.utc)
        )
        self.assertEqual(block.count('- **#'), 3)


class SchemaValidationTests(unittest.TestCase):
    def test_valid_comment_envelope_from_fixture(self):
        errors = _envelope.validate_envelope(FIXTURE_ENVELOPE)
        self.assertEqual(errors, [])

    def test_valid_new_envelope(self):
        envelope: dict[str, Any] = {
            'action': 'new',
            'title': 'Something failed',
            'body': 'details',
            'labels': ['tests'],
            'issue_type': None,
            'dedup_reason': 'no match',
            'confidence': 'low',
        }
        self.assertEqual(_envelope.validate_envelope(envelope), [])

    def test_describe_envelope_records_the_routing_and_why(self):
        envelope: dict[str, Any] = {
            'action': 'new',
            'target_issue': None,
            'title': 'Something failed',
            'body': 'details',
            'labels': [],
            'issue_type': None,
            'dedup_reason': 'no candidate names this test',
            'confidence': 'medium',
            'also': [
                {
                    'action': 'comment',
                    'target_issue': 44,
                    'title': None,
                    'labels': None,
                    'issue_type': None,
                    'body': 'again',
                    'dedup_reason': '#44 names the same lint error',
                    'confidence': 'high',
                }
            ],
        }
        self.assertEqual(
            _envelope.describe_envelope(envelope),
            'envelope: new on new issue, confidence medium. '
            'Reason: no candidate names this test\n'
            'also[0]: comment on #44, confidence high. Reason: #44 names the same lint error',
        )

    def test_new_envelope_with_no_labels_is_valid(self):
        # No label is mandatory: the repo's label set is centrally managed, and
        # an empty list is the right answer when none of it applies.
        envelope: dict[str, Any] = {
            'action': 'new',
            'title': 'Something failed',
            'body': 'details',
            'labels': [],
            'issue_type': None,
            'dedup_reason': 'no match',
            'confidence': 'low',
        }
        self.assertEqual(_envelope.validate_envelope(envelope), [])

    def test_new_envelope_with_non_string_labels_is_invalid(self):
        envelope: dict[str, Any] = {
            'action': 'new',
            'title': 'Something failed',
            'body': 'details',
            'labels': ['tests', 7],
            'issue_type': None,
            'dedup_reason': 'no match',
            'confidence': 'low',
        }
        self.assertTrue(any('labels' in e for e in _envelope.validate_envelope(envelope)))

    def test_new_envelope_with_target_issue_is_invalid(self):
        envelope = {
            'action': 'new',
            'title': 't',
            'body': 'b',
            'labels': ['tests'],
            'issue_type': None,
            'dedup_reason': 'd',
            'confidence': 'low',
            'target_issue': 5,
        }
        errors = _envelope.validate_envelope(envelope)
        self.assertTrue(any('target_issue' in e for e in errors))

    def test_comment_envelope_with_title_is_invalid(self):
        envelope = {
            'action': 'comment',
            'target_issue': 5,
            'title': 'should not be here',
            'body': 'b',
            'dedup_reason': 'd',
            'confidence': 'high',
        }
        errors = _envelope.validate_envelope(envelope)
        self.assertTrue(any('title' in e for e in errors))

    def test_bad_action_value_is_invalid(self):
        envelope = {'action': 'delete', 'body': 'b', 'dedup_reason': 'd', 'confidence': 'high'}
        errors = _envelope.validate_envelope(envelope)
        self.assertTrue(any('action' in e for e in errors))

    def test_envelope_with_also_is_valid(self):
        # Regression: `also` is legal on the top-level envelope, and the model
        # emits it routinely because the schema it is given declares it. The
        # top-level unknown-field check used to reject every envelope carrying
        # it, so the LLM path always fell back to the plain body. The three
        # tests below did not catch it: they assert invalidity and match on the
        # substring "also", which the spurious error also contained.
        base = dict(FIXTURE_ENVELOPE)
        base['also'] = [dict(FIXTURE_ENVELOPE)]
        self.assertEqual(_envelope.validate_envelope(base), [])

    def test_envelope_with_empty_also_is_valid(self):
        base = dict(FIXTURE_ENVELOPE)
        base['also'] = []
        self.assertEqual(_envelope.validate_envelope(base), [])

    def test_genuinely_unknown_top_level_field_is_still_invalid(self):
        base = dict(FIXTURE_ENVELOPE)
        base['nonsense'] = 1
        self.assertTrue(any('nonsense' in e for e in _envelope.validate_envelope(base)))

    def test_new_envelope_with_null_target_issue_is_valid(self):
        # The schema sent to OpenRouter is `strict`, so models return every
        # declared property and null the inapplicable ones. Rejecting on mere
        # presence discarded good output; only a real value is a conflict.
        envelope: dict[str, Any] = {
            'action': 'new',
            'title': 't',
            'body': 'b',
            'labels': ['tests'],
            'issue_type': None,
            'target_issue': None,
            'dedup_reason': 'd',
            'confidence': 'low',
        }
        self.assertEqual(_envelope.validate_envelope(envelope), [])

    def test_new_envelope_with_a_real_target_issue_is_still_invalid(self):
        envelope: dict[str, Any] = {
            'action': 'new',
            'title': 't',
            'body': 'b',
            'labels': [],
            'issue_type': None,
            'target_issue': 7,
            'dedup_reason': 'd',
            'confidence': 'low',
        }
        self.assertTrue(
            any('target_issue' in e for e in _envelope.validate_envelope(envelope)),
        )

    def test_comment_envelope_with_null_new_only_fields_is_valid(self):
        envelope: dict[str, Any] = {
            'action': 'comment',
            'target_issue': 7,
            'body': 'b',
            'title': None,
            'labels': None,
            'issue_type': None,
            'dedup_reason': 'd',
            'confidence': 'high',
        }
        self.assertEqual(_envelope.validate_envelope(envelope), [])

    def test_comment_envelope_with_a_real_title_is_still_invalid(self):
        envelope: dict[str, Any] = {
            'action': 'comment',
            'target_issue': 7,
            'body': 'b',
            'title': 'nope',
            'dedup_reason': 'd',
            'confidence': 'high',
        }
        self.assertTrue(any('title' in e for e in _envelope.validate_envelope(envelope)))

    def test_also_capped_at_two_entries(self):
        base = dict(FIXTURE_ENVELOPE)
        base['also'] = [dict(FIXTURE_ENVELOPE) for _ in range(3)]
        errors = _envelope.validate_envelope(base)
        self.assertTrue(
            any(e.startswith('envelope.also:') and 'too long' in e for e in errors), errors
        )

    def test_nested_also_is_invalid(self):
        base = dict(FIXTURE_ENVELOPE)
        inner = dict(FIXTURE_ENVELOPE)
        inner['also'] = [dict(FIXTURE_ENVELOPE)]
        base['also'] = [inner]
        errors = _envelope.validate_envelope(base)
        self.assertTrue(any("'also' was unexpected" in e for e in errors), errors)

    def test_also_entries_individually_validated(self):
        base = dict(FIXTURE_ENVELOPE)
        broken = {'action': 'comment'}  # missing body/dedup_reason/confidence/target_issue
        base['also'] = [broken]
        errors = _envelope.validate_envelope(base)
        self.assertTrue(any('also[0]' in e for e in errors))


class MainFlowTests(unittest.TestCase):
    """Exercises main()'s branching with gh and OpenRouter mocked out --
    No live gh or OpenRouter calls happen in this test.
    """

    def setUp(self):
        self.env = {
            'REPO': 'example/repo',
            'RUN_ID': '28141163589',
            'WORKFLOW_NAME': 'Broad Charm Compatibility Tests',
            'RUN_URL': 'https://github.com/example/repo/actions/runs/28141163589',
            'OPENROUTER_API_KEY': 'test-key',
            'NOTIFY_ISSUE': '9010',
        }

    def _patch_common(
        self,
        *,
        locate_return: tuple[int | None, str | None, int | None],
        gh_calls: mock.Mock,
    ) -> list[Any]:
        patches = [
            mock.patch.object(_github, 'resolve_origin', return_value=locate_return),
            mock.patch.object(_github, 'fetch_failed_jobs', return_value=[]),
            mock.patch.object(
                _github, 'fetch_run_meta', return_value={'createdAt': '2026-06-25T01:40:15Z'}
            ),
            mock.patch.object(_github, 'search_candidates', return_value=(FIXTURE_CANDIDATES, [])),
            mock.patch.object(_github, 'existing_labels', return_value={'tests', 'docs'}),
            mock.patch.object(_github, 'gh', side_effect=gh_calls),
            mock.patch.object(_summary, 'write_step_summary'),
        ]
        return patches

    def test_rung_zero_comments_and_skips_llm(self):
        gh_calls = mock.Mock(return_value=mock.Mock(returncode=0, stdout='', stderr=''))
        patches = self._patch_common(locate_return=(9010, None, None), gh_calls=gh_calls)
        with (
            mock.patch.dict('os.environ', self.env, clear=True),
            mock.patch.object(_openrouter, 'call_openrouter') as call_openrouter,
            contextlib.ExitStack() as stack,
        ):
            for p in patches:
                stack.enter_context(p)
            rc = _cli.main()
        self.assertEqual(rc, 0)
        call_openrouter.assert_not_called()
        gh_calls.assert_called_once()
        self.assertEqual(gh_calls.call_args.args[:3], ('issue', 'comment', '9010'))

    def test_valid_llm_response_upgrades_placeholder_in_place(self):
        gh_calls = mock.Mock(return_value=mock.Mock(returncode=0, stdout='', stderr=''))
        patches = self._patch_common(locate_return=(None, 'new', 4242), gh_calls=gh_calls)
        envelope = {
            'action': 'new',
            'title': 'x',
            'body': 'y',
            'labels': ['tests'],
            'issue_type': None,
            'dedup_reason': 'd',
            'confidence': 'medium',
        }
        with (
            mock.patch.dict('os.environ', self.env, clear=True),
            mock.patch.object(
                _openrouter, 'call_openrouter', return_value=(envelope, ANSWERED_BY)
            ),
            contextlib.ExitStack() as stack,
        ):
            for p in patches:
                stack.enter_context(p)
            rc = _cli.main()
        self.assertEqual(rc, 0)
        edit_calls = [c for c in gh_calls.call_args_list if c.args[:2] == ('issue', 'edit')]
        self.assertEqual(len(edit_calls), 1)
        self.assertEqual(edit_calls[0].args[2], '4242')
        # The placeholder this edits over contained only the run link, so the
        # enriched body has to carry it.
        args = edit_calls[0].args
        body = args[args.index('--body') + 1]
        self.assertIn(f'Run: {self.env["RUN_URL"]}', body)
        self.assertIn('<!-- ai-failure-notifications:run=28141163589:sig=', body)
        self.assertIn('<!-- ai-failure-notifications:signature {', body)
        # The model that answered is recorded beside the marker, and the
        # marker it sits beside still reads as this run's enrichment.
        self.assertIn(_markers.render_model_stamp(ANSWERED_BY), body)
        self.assertEqual(_markers.find_run_markers([(4242, body)], '28141163589')[0], 4242)

    def test_the_pointer_note_links_the_run_but_carries_no_signature_stamp(self):
        """A "this belongs elsewhere" note must not stamp this failure on that issue.

        The stamp is what a later run's candidate block reads to decide what an
        issue is about, so stamping the issue this failure was decided NOT to
        belong to would teach the next run the opposite of the decision this
        one made. The run link still belongs there, so a reader of that issue
        has something to follow.
        """
        gh_calls = mock.Mock(
            return_value=mock.Mock(returncode=0, stdout='https://x/issues/9', stderr='')
        )
        patches = self._patch_common(locate_return=(None, 'comment', 4242), gh_calls=gh_calls)
        envelope = {
            'action': 'new',
            'title': 'x',
            'body': 'y',
            'labels': [],
            'issue_type': None,
            'dedup_reason': 'd',
            'confidence': 'medium',
        }
        with (
            mock.patch.dict('os.environ', self.env, clear=True),
            mock.patch.object(
                _openrouter, 'call_openrouter', return_value=(envelope, ANSWERED_BY)
            ),
            mock.patch.object(_github, 'existing_issue_types', return_value=set()),
            contextlib.ExitStack() as stack,
        ):
            for p in patches:
                stack.enter_context(p)
            rc = _cli.main()
        self.assertEqual(rc, 0)
        pointer = [
            c
            for c in gh_calls.call_args_list
            if c.args[:2] == ('issue', 'comment') and c.args[2] == '4242'
        ]
        self.assertEqual(len(pointer), 1)
        args = pointer[0].args
        body = args[args.index('--body') + 1]
        self.assertIn('opened separately', body)
        self.assertIn(f'Run: {self.env["RUN_URL"]}', body)
        self.assertNotIn('ai-failure-notifications:signature', body)
        self.assertNotIn('ai-failure-notifications:model', body)
        # The new issue itself is about this failure, so it does get the stamps.
        created = [c for c in gh_calls.call_args_list if c.args[:2] == ('issue', 'create')]
        self.assertEqual(len(created), 1)
        created_args = created[0].args
        created_body = created_args[created_args.index('--body') + 1]
        self.assertIn('ai-failure-notifications:signature', created_body)
        self.assertIn(_markers.render_model_stamp(ANSWERED_BY), created_body)

    def test_invalid_llm_response_falls_back_to_plain_comment(self):
        gh_calls = mock.Mock(return_value=mock.Mock(returncode=0, stdout='', stderr=''))
        patches = self._patch_common(locate_return=(None, 'comment', 4242), gh_calls=gh_calls)
        with (
            mock.patch.dict('os.environ', self.env, clear=True),
            mock.patch.object(
                _openrouter,
                'call_openrouter',
                return_value=({'action': 'not-a-real-action'}, ANSWERED_BY),
            ),
            contextlib.ExitStack() as stack,
        ):
            for p in patches:
                stack.enter_context(p)
            rc = _cli.main()
        self.assertEqual(rc, 0)
        comment_calls = [c for c in gh_calls.call_args_list if c.args[:2] == ('issue', 'comment')]
        self.assertEqual(len(comment_calls), 1)
        self.assertEqual(comment_calls[0].args[2], '4242')
        # The plain fallback had no model behind it, so it names none.
        args = comment_calls[0].args
        self.assertNotIn('ai-failure-notifications:model', args[args.index('--body') + 1])

    def test_no_api_key_uses_plain_fallback_without_calling_llm(self):
        gh_calls = mock.Mock(return_value=mock.Mock(returncode=0, stdout='', stderr=''))
        patches = self._patch_common(locate_return=(None, 'new', 4242), gh_calls=gh_calls)
        env = dict(self.env)
        env.pop('OPENROUTER_API_KEY')
        with (
            mock.patch.dict('os.environ', env, clear=True),
            mock.patch.object(_openrouter, 'call_openrouter') as call_openrouter,
            contextlib.ExitStack() as stack,
        ):
            for p in patches:
                stack.enter_context(p)
            rc = _cli.main()
        self.assertEqual(rc, 0)
        call_openrouter.assert_not_called()


def _gh_router(notifier_comment_ids: str = '') -> mock.Mock:
    """A `gh` mock that answers the comment lookup and the issue create by argv.

    `notifier_comment_ids` is what the comments endpoint query prints: one REST
    id per line for each comment carrying this run's `origin=comment` marker.
    """

    def respond(*args: str, **kwargs: Any) -> mock.Mock:
        if args[:2] == ('api', '--paginate'):
            stdout = notifier_comment_ids
        elif args[:2] == ('issue', 'create'):
            stdout = 'https://github.com/example/repo/issues/9'
        else:
            stdout = ''
        return mock.Mock(returncode=0, stdout=stdout, stderr='')

    return mock.Mock(side_effect=respond)


def _calls(gh_calls: mock.Mock, *prefix: str) -> list[Any]:
    return [c for c in gh_calls.call_args_list if c.args[: len(prefix)] == prefix]


def _patch_body(call: Any) -> str:
    args = call.args
    field = args[args.index('-f') + 1]
    assert field.startswith('body=')
    return field.removeprefix('body=')


class OneArtefactPerFailureTests(unittest.TestCase):
    """The enricher upgrades what the notifier made instead of adding to it.

    When the notifier comments on an issue that already existed, the enricher
    used to post a second comment under it, so every such failure left two
    comments; and when the model gave nothing back for a fresh placeholder, the
    fallback opened a second issue beside it.
    """

    def setUp(self):
        self.env = {
            'REPO': 'example/repo',
            'RUN_ID': '28141163589',
            'WORKFLOW_NAME': 'Broad Charm Compatibility Tests',
            'RUN_URL': 'https://github.com/example/repo/actions/runs/28141163589',
            'OPENROUTER_API_KEY': 'test-key',
            'NOTIFY_ISSUE': '4242',
        }

    def _run(
        self,
        gh_calls: mock.Mock,
        locate_return: tuple[int | None, str | None, int],
        *,
        envelope: Any = None,
        api_key: bool = True,
    ) -> mock.Mock:
        env = dict(self.env)
        if not api_key:
            env.pop('OPENROUTER_API_KEY')
        with (
            mock.patch.dict('os.environ', env, clear=True),
            mock.patch.object(_github, 'resolve_origin', return_value=locate_return),
            mock.patch.object(_github, 'fetch_failed_jobs', return_value=[]),
            mock.patch.object(_github, 'fetch_run_meta', return_value={'createdAt': ''}),
            mock.patch.object(_github, 'search_candidates', return_value=(FIXTURE_CANDIDATES, [])),
            mock.patch.object(_github, 'existing_labels', return_value=set()),
            mock.patch.object(_github, 'existing_issue_types', return_value=set()),
            mock.patch.object(_github, 'gh', side_effect=gh_calls),
            mock.patch.object(
                _openrouter, 'call_openrouter', return_value=(envelope, ANSWERED_BY)
            ),
            mock.patch.object(_summary, 'write_step_summary') as summary,
        ):
            self.assertEqual(_cli.main(), 0)
        return summary

    def test_a_comment_on_the_same_issue_replaces_the_notifiers_comment(self):
        gh_calls = _gh_router('555\n')
        envelope = {
            'action': 'comment',
            'target_issue': 4242,
            'body': 'Same failure as before.',
            'dedup_reason': 'd',
            'confidence': 'high',
        }
        self._run(gh_calls, (None, 'comment', 4242), envelope=envelope)
        self.assertEqual(_calls(gh_calls, 'issue', 'comment'), [])
        patches = _calls(gh_calls, 'api', '--method', 'PATCH')
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].args[3], 'repos/example/repo/issues/comments/555')
        body = _patch_body(patches[0])
        self.assertIn('Same failure as before.', body)
        self.assertIn(f'Run: {self.env["RUN_URL"]}', body)
        self.assertIn('<!-- ai-failure-notifications:run=28141163589:sig=', body)

    def test_the_fallback_on_a_commented_issue_replaces_the_notifiers_comment(self):
        gh_calls = _gh_router('555\n')
        self._run(gh_calls, (None, 'comment', 4242), api_key=False)
        self.assertEqual(_calls(gh_calls, 'issue', 'comment'), [])
        patches = _calls(gh_calls, 'api', '--method', 'PATCH')
        self.assertEqual(len(patches), 1)
        self.assertIn('<!-- ai-failure-notifications:signature {', _patch_body(patches[0]))

    def test_the_fallback_on_a_new_placeholder_edits_it_rather_than_opening_another(self):
        gh_calls = _gh_router()
        self._run(gh_calls, (None, 'new', 4242), api_key=False)
        self.assertEqual(_calls(gh_calls, 'issue', 'create'), [])
        edits = _calls(gh_calls, 'issue', 'edit', '4242')
        self.assertEqual(len(edits), 1)
        args = edits[0].args
        body = args[args.index('--body') + 1]
        self.assertIn('Workflow: Broad Charm Compatibility Tests', body)
        self.assertIn('<!-- ai-failure-notifications:run=28141163589:sig=', body)

    def test_the_pointer_note_replaces_the_notifiers_comment(self):
        gh_calls = _gh_router('555\n')
        envelope = {
            'action': 'new',
            'title': 'x',
            'body': 'y',
            'labels': [],
            'issue_type': None,
            'dedup_reason': 'd',
            'confidence': 'medium',
        }
        self._run(gh_calls, (None, 'comment', 4242), envelope=envelope)
        self.assertEqual(len(_calls(gh_calls, 'issue', 'create')), 1)
        self.assertEqual(_calls(gh_calls, 'issue', 'comment'), [])
        patches = _calls(gh_calls, 'api', '--method', 'PATCH')
        self.assertEqual(len(patches), 1)
        self.assertIn('opened separately', _patch_body(patches[0]))

    def test_a_rerun_note_replaces_the_notifiers_comment_on_the_same_issue(self):
        gh_calls = _gh_router('555\n')
        self._run(gh_calls, (4242, 'comment', 4242))
        self.assertEqual(_calls(gh_calls, 'issue', 'comment'), [])
        patches = _calls(gh_calls, 'api', '--method', 'PATCH')
        self.assertEqual(len(patches), 1)
        self.assertIn('Re-run attempt still failing', _patch_body(patches[0]))

    def test_a_rerun_note_on_another_issue_is_a_new_comment(self):
        gh_calls = _gh_router('555\n')
        self._run(gh_calls, (9010, 'comment', 4242))
        self.assertEqual(_calls(gh_calls, 'api', '--method', 'PATCH'), [])
        self.assertEqual(len(_calls(gh_calls, 'issue', 'comment', '9010')), 1)

    def test_no_notifier_comment_to_edit_adds_a_comment_and_says_so(self):
        gh_calls = _gh_router('')
        summary = self._run(gh_calls, (None, 'comment', 4242), api_key=False)
        self.assertEqual(_calls(gh_calls, 'api', '--method', 'PATCH'), [])
        self.assertEqual(len(_calls(gh_calls, 'issue', 'comment', '4242')), 1)
        self.assertTrue(
            any(
                "Couldn't edit the notifier's comment" in c.args[0] for c in summary.call_args_list
            )
        )

    def test_a_failed_edit_adds_a_comment_instead(self):
        def respond(*args: str, **kwargs: Any) -> mock.Mock:
            if args[:2] == ('api', '--paginate'):
                return mock.Mock(returncode=0, stdout='555\n', stderr='')
            if args[:3] == ('api', '--method', 'PATCH'):
                raise subprocess.CalledProcessError(1, ['gh', *args])
            return mock.Mock(returncode=0, stdout='', stderr='')

        gh_calls = mock.Mock(side_effect=respond)
        self._run(gh_calls, (None, 'comment', 4242), api_key=False)
        self.assertEqual(len(_calls(gh_calls, 'issue', 'comment', '4242')), 1)


class GhCallShapeTests(unittest.TestCase):
    """Pins the argv of each read-only gh call.

    These mock only the `gh` subprocess boundary, not the functions under
    test, so a wrong flag or a mis-quoted positional is visible here. The
    MainFlowTests above patch out `resolve_origin` and `search_candidates`
    wholesale, which is why both shipped with argv bugs that 29 green tests
    did not catch -- see the 2026-07-25 dev-box run against canonical/operator.
    """

    def _capture(self, stdout: str = '[]') -> mock.Mock:
        return mock.Mock(return_value=mock.Mock(returncode=0, stdout=stdout, stderr=''))

    def test_search_candidates_passes_state_and_search_flags(self):
        gh_calls = self._capture('[]')
        with mock.patch.object(_github, 'gh', side_effect=gh_calls):
            _github.search_candidates('example/repo', 'Example Charm Tests')
        states: list[str] = []
        for call in gh_calls.call_args_list:
            args = call.args
            self.assertEqual(args[:2], ('issue', 'list'))
            self.assertEqual(args[args.index('--repo') + 1], 'example/repo')
            self.assertEqual(args[args.index('--search') + 1], '"Example Charm Tests"')
            states.append(args[args.index('--state') + 1])
        self.assertEqual(states, ['open', 'closed'])

    def test_search_candidates_asks_for_the_comments_field(self):
        """Without `comments` the candidate block has nothing in it to match on.

        An automatically-opened issue's body is one line reading "Scheduled
        workflow 'X' failed: <url>"; everything anybody knows about it is in the
        thread. The field list is pinned here rather than left to an assertIn
        somewhere downstream.
        """
        gh_calls = self._capture('[]')
        with mock.patch.object(_github, 'gh', side_effect=gh_calls):
            _github.search_candidates('example/repo', 'Example Charm Tests')
        for call in gh_calls.call_args_list:
            args = call.args
            self.assertEqual(
                args[args.index('--json') + 1], 'number,title,body,createdAt,closedAt,comments'
            )

    def test_fetch_job_log_uses_the_rest_logs_endpoint(self):
        gh_calls = self._capture('2026-07-21T16:17:04Z some log line\n')
        with mock.patch.object(_github, 'gh', side_effect=gh_calls):
            log = _github.fetch_job_log('example/repo', '29847889218', 88693036489)
        self.assertIn('some log line', log)
        # Without --allow-escape-sequences, gh 2.9x+ writes nothing at all for
        # a log with terminal escapes in it, which is every Actions log.
        self.assertEqual(
            gh_calls.call_args.args,
            (
                'api',
                'repos/example/repo/actions/jobs/88693036489/logs',
                '--allow-escape-sequences',
            ),
        )

    def test_fetch_job_log_reports_an_empty_log_instead_of_swallowing_it(self):
        gh_calls = self._capture('')
        with (
            mock.patch.object(_github, 'gh', side_effect=gh_calls),
            mock.patch.object(_summary, 'write_step_summary') as summary,
        ):
            log = _github.fetch_job_log('example/repo', '29847889218', 88693036489)
        self.assertEqual(log, '')
        summary.assert_called_once()
        self.assertIn('no log text', summary.call_args.args[0])

    def test_fetch_job_log_reports_the_status_gh_put_on_stderr(self):
        gh_calls = mock.Mock(
            return_value=mock.Mock(returncode=1, stdout='', stderr='gh: Not Found (HTTP 404)\n')
        )
        with (
            mock.patch.object(_github, 'gh', side_effect=gh_calls),
            mock.patch.object(_summary, 'write_step_summary') as summary,
        ):
            _github.fetch_job_log('example/repo', '29847889218', 88693036489)
        # A 404 (log not ready) and a 403 (no `actions: read`) are both exit 1,
        # so the status has to reach the summary for either to be diagnosable.
        self.assertIn('HTTP 404', summary.call_args.args[0])

    def test_fetch_job_log_says_so_when_gh_was_silent(self):
        gh_calls = self._capture('')
        with (
            mock.patch.object(_github, 'gh', side_effect=gh_calls),
            mock.patch.object(_summary, 'write_step_summary') as summary,
        ):
            _github.fetch_job_log('example/repo', '29847889218', 88693036489)
        self.assertIn('no stderr', summary.call_args.args[0])

    def test_fetch_failed_jobs_requests_the_jobs_field(self):
        gh_calls = self._capture(
            '{"jobs": [{"databaseId": 1, "name": "j", "conclusion": "failure",'
            ' "steps": [{"name": "s", "conclusion": "failure"}]}]}'
        )
        with mock.patch.object(_github, 'gh', side_effect=gh_calls):
            jobs = _github.fetch_failed_jobs('example/repo', '29847889218')
        self.assertEqual(jobs, [_models.FailedJob(id=1, name='j', failed_step='s')])
        args = gh_calls.call_args.args
        self.assertEqual(args[:3], ('run', 'view', '29847889218'))
        self.assertEqual(args[args.index('--json') + 1], 'jobs')

    def test_existing_labels_requests_the_name_field(self):
        gh_calls = self._capture('[{"name": "tests"}, {"name": "docs"}]')
        with mock.patch.object(_github, 'gh', side_effect=gh_calls):
            labels = _github.existing_labels('example/repo')
        self.assertEqual(labels, {'tests', 'docs'})
        args = gh_calls.call_args.args
        self.assertEqual(args[:2], ('label', 'list'))
        self.assertEqual(args[args.index('--json') + 1], 'name')

    def test_existing_issue_types_returns_the_enabled_ones(self):
        gh_calls = self._capture(
            '{"data": {"repository": {"issueTypes": {"nodes": ['
            '{"name": "Bug", "isEnabled": true}, '
            '{"name": "Task", "isEnabled": true}, '
            '{"name": "Epic", "isEnabled": false}]}}}}'
        )
        with mock.patch.object(_github, 'gh', side_effect=gh_calls):
            types = _github.existing_issue_types('example/repo')
        self.assertEqual(types, {'Bug', 'Task'})
        args = gh_calls.call_args.args
        self.assertEqual(args[:2], ('api', 'graphql'))

    def test_existing_issue_types_tolerates_a_repo_with_none(self):
        """A personal fork, or any repo whose org has not enabled types."""
        gh_calls = self._capture('{"data": {"repository": {"issueTypes": null}}}')
        with mock.patch.object(_github, 'gh', side_effect=gh_calls):
            self.assertEqual(_github.existing_issue_types('example/repo'), set())

    def test_match_issue_type_ignores_case_and_uses_the_repo_spelling(self):
        self.assertEqual(_github.match_issue_type('bug', {'Bug', 'Task'}), 'Bug')
        self.assertIsNone(_github.match_issue_type('bug', set()))
        self.assertIsNone(_github.match_issue_type('chore', {'Bug', 'Task'}))
        self.assertIsNone(_github.match_issue_type(None, {'Bug'}))

    def test_find_notifier_comment_reads_the_rest_comments_endpoint(self):
        gh_calls = self._capture('111\n222\n')
        with mock.patch.object(_github, 'gh', side_effect=gh_calls):
            comment_id = _github.find_notifier_comment('example/repo', 4242, '123')
        args = gh_calls.call_args.args
        self.assertEqual(
            args[:3], ('api', '--paginate', 'repos/example/repo/issues/4242/comments')
        )
        self.assertIn(
            'contains("ai-failure-notifications:run=123:origin=comment")',
            args[args.index('--jq') + 1],
        )
        # A re-run's notifier comment comes after the first attempt's.
        self.assertEqual(comment_id, 222)

    def test_find_notifier_comment_returns_none_when_there_is_none(self):
        gh_calls = self._capture('')
        with mock.patch.object(_github, 'gh', side_effect=gh_calls):
            self.assertIsNone(_github.find_notifier_comment('example/repo', 4242, '123'))

    def test_edit_comment_patches_the_rest_comment(self):
        gh_calls = self._capture('')
        with mock.patch.object(_github, 'gh', side_effect=gh_calls):
            _github.edit_comment('example/repo', 555, 'New @body')
        self.assertEqual(
            gh_calls.call_args.args,
            (
                'api',
                '--method',
                'PATCH',
                'repos/example/repo/issues/comments/555',
                '-f',
                'body=New @body',
            ),
        )


class NormalisationTests(unittest.TestCase):
    """Fields that do not apply to the chosen action are dropped, not fatal.

    Found by dogfooding: the model is given a `strict` schema, so it returns
    every declared property and fills the ones irrelevant to the action it
    chose. Treating those as validation errors discarded a perfectly good
    comment body and fell back to the plain notice.
    """

    def test_comment_loses_new_only_fields(self):
        envelope: dict[str, Any] = {
            'action': 'comment',
            'target_issue': 9010,
            'body': 'Another occurrence.',
            'title': 'a title it should not have',
            'labels': ['tests'],
            'issue_type': 'bug',
            'dedup_reason': 'd',
            'confidence': 'high',
        }
        cleaned, dropped = _envelope.normalise_envelope(envelope)
        self.assertEqual(
            sorted(dropped), ['envelope: issue_type', 'envelope: labels', 'envelope: title']
        )
        self.assertNotIn('title', cleaned)
        self.assertEqual(cleaned['body'], 'Another occurrence.')
        self.assertEqual(_envelope.validate_envelope(cleaned), [])

    def test_new_loses_target_issue(self):
        envelope: dict[str, Any] = {
            'action': 'new',
            'title': 't',
            'body': 'b',
            'labels': [],
            'issue_type': None,
            'target_issue': 7,
            'dedup_reason': 'd',
            'confidence': 'low',
        }
        cleaned, dropped = _envelope.normalise_envelope(envelope)
        self.assertEqual(dropped, ['envelope: target_issue'])
        self.assertEqual(_envelope.validate_envelope(cleaned), [])

    def test_also_entries_are_normalised_too(self):
        inner: dict[str, Any] = {
            'action': 'comment',
            'target_issue': 1,
            'body': 'b',
            'title': 'nope',
            'dedup_reason': 'd',
            'confidence': 'low',
        }
        envelope: dict[str, Any] = {
            'action': 'new',
            'title': 't',
            'body': 'b',
            'labels': [],
            'issue_type': None,
            'dedup_reason': 'd',
            'confidence': 'low',
            'also': [inner],
        }
        cleaned, dropped = _envelope.normalise_envelope(envelope)
        self.assertEqual(dropped, ['envelope.also[0]: title'])
        self.assertEqual(_envelope.validate_envelope(cleaned), [])

    def test_an_unknown_property_is_stripped_rather_than_discarding_the_envelope(self):
        """An extra property must not cost a usable body and dedup decision.

        An unknown property is one `apply_entry` never reads, so removing it
        can't change what gets posted, and rejecting the envelope would.
        """
        envelope = {
            'action': 'comment',
            'target_issue': 9010,
            'body': 'Another occurrence.',
            'dedup_reason': 'same tests',
            'confidence': 'high',
            'html_url': 'https://github.com/example/repo/issues/9010',
        }
        cleaned, notes = _envelope.normalise_envelope(envelope)
        self.assertNotIn('html_url', cleaned)
        self.assertIn('envelope: html_url (not in the schema)', notes)
        self.assertEqual(_envelope.validate_envelope(cleaned), [])
        self.assertEqual(cleaned['body'], 'Another occurrence.')

    def test_an_unknown_property_in_an_also_entry_is_stripped_too(self):
        envelope = {
            'action': 'new',
            'title': 't',
            'labels': [],
            'issue_type': None,
            'body': 'b',
            'dedup_reason': 'r',
            'confidence': 'low',
            'also': [
                {
                    'action': 'comment',
                    'target_issue': 7,
                    'body': 'b',
                    'dedup_reason': 'r',
                    'confidence': 'medium',
                    'url': 'https://example.invalid/7',
                }
            ],
        }
        cleaned, notes = _envelope.normalise_envelope(envelope)
        self.assertNotIn('url', cleaned['also'][0])
        self.assertIn('envelope.also[0]: url (not in the schema)', notes)
        self.assertEqual(_envelope.validate_envelope(cleaned), [])

    def test_stripping_an_unknown_property_does_not_invent_a_target(self):
        """The repair is a strip, not a guess.

        A model that put its target in `issue` rather than `target_issue` has
        not told us what to comment on in a way we are entitled to act on, so
        the envelope must still be rejected rather than quietly retargeted.
        """
        envelope = {
            'action': 'comment',
            'issue': 9010,
            'body': 'b',
            'dedup_reason': 'r',
            'confidence': 'high',
        }
        cleaned, notes = _envelope.normalise_envelope(envelope)
        self.assertNotIn('issue', cleaned)
        self.assertNotIn('target_issue', cleaned)
        self.assertIn('envelope: issue (not in the schema)', notes)
        self.assertNotEqual(_envelope.validate_envelope(cleaned), [])

    def test_a_comment_with_no_body_is_rejected(self):
        envelope = {
            'action': 'comment',
            'target_issue': 7,
            'dedup_reason': 'r',
            'confidence': 'high',
        }
        cleaned, _ = _envelope.normalise_envelope(envelope)
        self.assertNotIn('body', cleaned)
        self.assertNotEqual(_envelope.validate_envelope(cleaned), [])

    def test_nothing_dropped_leaves_the_envelope_alone(self):
        cleaned, dropped = _envelope.normalise_envelope(FIXTURE_ENVELOPE)
        self.assertEqual(dropped, [])
        self.assertIs(cleaned, FIXTURE_ENVELOPE)


class CandidatePoolTests(unittest.TestCase):
    """The issue the notifier commented on stays in the candidate pool.

    Found by dogfooding. The origin issue was excluded unconditionally. When
    the notifier had commented on a pre-existing issue -- the case for every
    recurrence after the first -- that issue is the most likely duplicate, and
    removing it left the model with an empty candidate list. It answered "new",
    and a duplicate issue was opened: the exact outcome this path exists to
    prevent.
    """

    def _run_main(self, *, origin_kind: str, candidates: list[_models.CandidateIssue]) -> str:
        captured: dict[str, str] = {}

        def fake_build_prompt(
            workflow_name: str, run_url: str, signature: Any, candidates_block: str
        ) -> tuple[str, str]:
            captured['block'] = candidates_block
            return 'sys', 'user'

        env = {
            'REPO': 'example/repo',
            'RUN_ID': '28141163589',
            'WORKFLOW_NAME': 'Broad Charm Compatibility Tests',
            'RUN_URL': 'https://example.invalid/run',
            'OPENROUTER_API_KEY': 'test-key',
            'NOTIFY_ISSUE': '9010',
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(_github, 'resolve_origin', return_value=(None, origin_kind, 9010)),
            mock.patch.object(_github, 'fetch_failed_jobs', return_value=[]),
            mock.patch.object(_github, 'fetch_run_meta', return_value={'createdAt': ''}),
            mock.patch.object(_github, 'search_candidates', return_value=(candidates, [])),
            mock.patch.object(_github, 'existing_labels', return_value=set()),
            mock.patch.object(_prompt, 'build_prompt', side_effect=fake_build_prompt),
            mock.patch.object(
                _openrouter, 'call_openrouter', return_value=({'action': 'bogus'}, ANSWERED_BY)
            ),
            mock.patch.object(_github, 'gh'),
            mock.patch.object(_summary, 'write_step_summary'),
        ):
            _cli.main()
        return captured['block']

    def test_commented_origin_issue_is_offered_as_a_candidate(self):
        candidate = _models.CandidateIssue(
            number=9010, title='the tracked one', body='x', closed_at=None
        )
        block = self._run_main(origin_kind='comment', candidates=[candidate])
        self.assertIn('#9010', block)

    def test_freshly_created_placeholder_is_not_offered_as_a_candidate(self):
        candidate = _models.CandidateIssue(
            number=9010, title='the placeholder', body='x', closed_at=None
        )
        block = self._run_main(origin_kind='new', candidates=[candidate])
        self.assertNotIn('#9010', block)


class BodyFooterTests(unittest.TestCase):
    """Every body carries the footer the notifier's coarse search matches on.

    Found by dogfooding: the footer was described in the design and in the
    notifier's own comment, but only ever existed in the prompt template, so
    enriched issues went out without it. The coarse search then depended on
    the model happening to leave the workflow name in the title.
    """

    def test_render_body_has_footer_run_link_and_marker(self):
        body = _apply.render_body(
            'Some detail.', 'Example Charm Tests', 'https://x/actions/runs/7', '<!-- m -->'
        )
        self.assertEqual(
            body,
            'Some detail.\n\nWorkflow: Example Charm Tests\n'
            'Run: https://x/actions/runs/7\n\n<!-- m -->',
        )

    def test_applied_comment_body_has_the_footer_and_the_run_link(self):
        gh_calls = mock.Mock(return_value=mock.Mock(returncode=0, stdout='', stderr=''))
        entry: dict[str, Any] = {'action': 'comment', 'body': 'Another occurrence.'}
        with mock.patch.object(_github, 'gh', side_effect=gh_calls):
            _apply.apply_entry(
                'example/repo',
                entry,
                '<!-- m -->',
                'ops Smoke Tests',
                'https://x/actions/runs/7',
                default_target=7,
            )
        args = gh_calls.call_args.args
        self.assertEqual(args[:3], ('issue', 'comment', '7'))
        body = args[args.index('--body') + 1]
        self.assertIn('Workflow: ops Smoke Tests', body)
        self.assertIn('Run: https://x/actions/runs/7', body)

    def test_a_missing_issue_type_creates_one_issue_and_not_two(self):
        """The type is resolved before the create, so there is nothing to retry.

        `gh issue create --type` creates the issue and only then fails on the
        type, so retrying without it opened a second, identical issue.
        """
        gh_calls = mock.Mock(
            return_value=mock.Mock(returncode=0, stdout='https://x/issues/9', stderr='')
        )
        entry: dict[str, Any] = {
            'action': 'new',
            'title': 't',
            'body': 'Detail.',
            'labels': [],
            'issue_type': 'bug',
        }
        with (
            mock.patch.object(_github, 'gh', side_effect=gh_calls),
            mock.patch.object(_github, 'existing_labels', return_value=set()),
            mock.patch.object(_github, 'existing_issue_types', return_value=set()),
            mock.patch.object(_summary, 'write_step_summary') as summary,
        ):
            _apply.apply_entry(
                'example/repo', entry, '<!-- m -->', 'ops Smoke Tests', 'https://x/runs/7'
            )
        gh_calls.assert_called_once()
        self.assertNotIn('--type', gh_calls.call_args.args)
        self.assertIn('bug', summary.call_args.args[0])

    def test_a_known_issue_type_is_passed_in_the_repo_spelling(self):
        gh_calls = mock.Mock(
            return_value=mock.Mock(returncode=0, stdout='https://x/issues/9', stderr='')
        )
        entry: dict[str, Any] = {
            'action': 'new',
            'title': 't',
            'body': 'Detail.',
            'labels': [],
            'issue_type': 'bug',
        }
        with (
            mock.patch.object(_github, 'gh', side_effect=gh_calls),
            mock.patch.object(_github, 'existing_labels', return_value=set()),
            mock.patch.object(_github, 'existing_issue_types', return_value={'Bug', 'Task'}),
        ):
            _apply.apply_entry(
                'example/repo', entry, '<!-- m -->', 'ops Smoke Tests', 'https://x/runs/7'
            )
        gh_calls.assert_called_once()
        args = gh_calls.call_args.args
        self.assertEqual(args[args.index('--type') + 1], 'Bug')

    def test_no_issue_type_asked_for_costs_no_lookup(self):
        gh_calls = mock.Mock(
            return_value=mock.Mock(returncode=0, stdout='https://x/issues/9', stderr='')
        )
        entry: dict[str, Any] = {
            'action': 'new',
            'title': 't',
            'body': 'Detail.',
            'labels': [],
            'issue_type': None,
        }
        with (
            mock.patch.object(_github, 'gh', side_effect=gh_calls),
            mock.patch.object(_github, 'existing_labels', return_value=set()),
            mock.patch.object(_github, 'existing_issue_types') as types,
        ):
            _apply.apply_entry(
                'example/repo', entry, '<!-- m -->', 'ops Smoke Tests', 'https://x/runs/7'
            )
        types.assert_not_called()

    def test_applied_new_issue_body_has_the_footer(self):
        gh_calls = mock.Mock(
            return_value=mock.Mock(returncode=0, stdout='https://x/issues/9', stderr='')
        )
        entry: dict[str, Any] = {
            'action': 'new',
            'title': 't',
            'body': 'Detail.',
            'labels': [],
            'issue_type': None,
        }
        with (
            mock.patch.object(_github, 'gh', side_effect=gh_calls),
            mock.patch.object(_github, 'existing_labels', return_value=set()),
        ):
            _apply.apply_entry(
                'example/repo', entry, '<!-- m -->', 'ops Smoke Tests', 'https://x/runs/7'
            )
        args = gh_calls.call_args.args
        self.assertIn('Workflow: ops Smoke Tests', args[args.index('--body') + 1])


class StepSummaryTests(unittest.TestCase):
    """Fallback reporting reaches the job log, not only the step summary."""

    def test_message_goes_to_stderr_even_with_a_summary_file(self):
        with tempfile.NamedTemporaryFile('w+', suffix='.md', delete=False) as handle:
            summary_path = handle.name
        self.addCleanup(os.unlink, summary_path)
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': summary_path}, clear=True),
            contextlib.redirect_stderr(stderr),
        ):
            _summary.write_step_summary('OpenRouter call failed (boom)')
        self.assertIn('OpenRouter call failed (boom)', stderr.getvalue())
        with open(summary_path, encoding='utf-8') as handle:
            self.assertIn('OpenRouter call failed (boom)', handle.read())


class MainDegradationTests(unittest.TestCase):
    """main() degrades through its own fallbacks when a gh search fails.

    Without these, a search failure raises out of main(), kills the enrich
    job, and hands every run to the workflow-level plain-fallback -- so
    enrichment silently never happens and the job still looks healthy.
    """

    def setUp(self):
        self.env = {
            'REPO': 'example/repo',
            'RUN_ID': '28141163589',
            'WORKFLOW_NAME': 'Broad Charm Compatibility Tests',
            'RUN_URL': 'https://github.com/example/repo/actions/runs/28141163589',
            'NOTIFY_ISSUE': '4242',
        }

    def test_marker_lookup_failure_does_not_crash_main(self):
        gh_calls = mock.Mock(
            return_value=mock.Mock(
                returncode=0, stdout='https://github.com/example/repo/issues/9999', stderr=''
            )
        )
        with (
            mock.patch.dict('os.environ', self.env, clear=True),
            mock.patch.object(_github, 'resolve_origin', side_effect=RuntimeError('boom')),
            mock.patch.object(_github, 'fetch_failed_jobs', return_value=[]),
            mock.patch.object(_github, 'fetch_run_meta', return_value={'createdAt': ''}),
            mock.patch.object(_github, 'existing_labels', return_value=set()),
            mock.patch.object(_github, 'gh', side_effect=gh_calls),
            mock.patch.object(_summary, 'write_step_summary') as summary,
        ):
            rc = _cli.main()
        self.assertEqual(rc, 0)
        self.assertTrue(
            any('Marker lookup failed' in c.args[0] for c in summary.call_args_list),
            'the failure should be reported in the step summary',
        )

    def test_candidate_search_failure_proceeds_with_no_candidates(self):
        gh_calls = mock.Mock(return_value=mock.Mock(returncode=0, stdout='', stderr=''))
        env = dict(self.env, OPENROUTER_API_KEY='test-key')
        with (
            mock.patch.dict('os.environ', env, clear=True),
            mock.patch.object(_github, 'resolve_origin', return_value=(None, 'new', 4242)),
            mock.patch.object(_github, 'fetch_failed_jobs', return_value=[]),
            mock.patch.object(_github, 'fetch_run_meta', return_value={'createdAt': ''}),
            mock.patch.object(_github, 'search_candidates', side_effect=RuntimeError('boom')),
            mock.patch.object(_github, 'existing_labels', return_value=set()),
            mock.patch.object(
                _openrouter,
                'call_openrouter',
                return_value=({'action': 'not-a-real-action'}, ANSWERED_BY),
            ),
            mock.patch.object(_github, 'gh', side_effect=gh_calls),
            mock.patch.object(_summary, 'write_step_summary') as summary,
        ):
            rc = _cli.main()
        self.assertEqual(rc, 0)
        self.assertTrue(
            any('Candidate search failed' in c.args[0] for c in summary.call_args_list),
            'the failure should be reported in the step summary',
        )


class OpenRouterCallTests(unittest.TestCase):
    """The OpenRouter call is built with urllib, so it has no third-party deps.

    Previously this function was only ever mocked, so nothing checked the
    request it actually builds.
    """

    def _response(self, content: str, model: str | None = None) -> mock.MagicMock:
        reply: dict[str, Any] = {'choices': [{'message': {'content': content}}]}
        if model is not None:
            reply['model'] = model
        response = mock.MagicMock()
        response.read.return_value = json.dumps(reply).encode()
        response.__enter__.return_value = response
        return response

    def _call(self, *outcomes: Any, model: str | None = 'm') -> tuple[Any, mock.Mock, mock.Mock]:
        """Call with urlopen giving `outcomes` in turn; return (result, urlopen, sleep)."""
        sleep = mock.Mock()
        with (
            mock.patch.object(
                _openrouter.urllib.request, 'urlopen', side_effect=list(outcomes)
            ) as urlopen,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = _openrouter.call_openrouter('sys', 'user', model, 'k', sleep=sleep)
        return result, urlopen, sleep

    def _call_raises(self, *outcomes: Any) -> tuple[str, mock.Mock, mock.Mock]:
        """As `_call`, for a call that is expected to give up; return (message, urlopen, sleep)."""
        sleep = mock.Mock()
        with (
            mock.patch.object(
                _openrouter.urllib.request, 'urlopen', side_effect=list(outcomes)
            ) as urlopen,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(RuntimeError) as raised,
        ):
            _openrouter.call_openrouter('sys', 'user', 'm', 'k', sleep=sleep)
        return str(raised.exception), urlopen, sleep

    def _ok(self, model: str | None = None) -> mock.MagicMock:
        return self._response(json.dumps({'action': 'new', 'body': 'b'}), model)

    def test_posts_json_with_auth_and_schema(self):
        envelope = {'action': 'new', 'body': 'b'}
        with mock.patch.object(
            _openrouter.urllib.request,
            'urlopen',
            return_value=self._response(json.dumps(envelope), 'some/model'),
        ) as urlopen:
            result, answered_by = _openrouter.call_openrouter(
                'sys', 'user', 'some/model', 'secret-key'
            )

        self.assertEqual(result, envelope)
        self.assertEqual(answered_by, 'some/model')
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, 'POST')
        self.assertEqual(request.full_url, 'https://openrouter.ai/api/v1/chat/completions')
        # urllib title-cases header keys, so compare case-insensitively.
        headers = {k.lower(): v for k, v in request.headers.items()}
        self.assertEqual(headers['Authorization'.lower()], 'Bearer secret-key')
        self.assertEqual(headers['Content-type'.lower()], 'application/json')
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 60)

        sent = json.loads(request.data.decode())
        self.assertEqual([m['role'] for m in sent['messages']], ['system', 'user'])
        self.assertEqual(sent['messages'][0]['content'], 'sys')
        self.assertEqual(sent['messages'][1]['content'], 'user')
        self.assertEqual(sent['response_format']['type'], 'json_schema')
        self.assertEqual(
            sent['response_format']['json_schema']['schema'], _envelope.ENVELOPE_JSON_SCHEMA
        )
        self.assertTrue(sent['response_format']['json_schema']['strict'])
        self.assertEqual(sent['provider'], {'require_parameters': True})

    def test_sends_the_configured_model_first_then_the_defaults(self):
        """OpenRouter's documented fallback shape: a `models` list, in priority order."""
        _, urlopen, _ = self._call(self._ok(), model='some/model')
        sent = json.loads(urlopen.call_args.args[0].data.decode())
        self.assertEqual(sent['models'], ['some/model', *_constants.DEFAULT_MODELS])
        # `models` alone: the documented examples do not send `model` with it.
        self.assertNotIn('model', sent)

    def test_without_a_configured_model_the_defaults_are_sent(self):
        _, urlopen, _ = self._call(self._ok(), model=None)
        sent = json.loads(urlopen.call_args.args[0].data.decode())
        self.assertEqual(sent['models'], ['deepseek/deepseek-chat', 'deepseek/deepseek-v3.2'])

    def test_a_configured_model_that_is_also_a_default_is_listed_once(self):
        _, urlopen, _ = self._call(self._ok(), model='deepseek/deepseek-v3.2')
        sent = json.loads(urlopen.call_args.args[0].data.decode())
        self.assertEqual(sent['models'], ['deepseek/deepseek-v3.2', 'deepseek/deepseek-chat'])

    def test_an_openrouter_model_override_reaches_the_request(self):
        env = {
            'REPO': 'o/r',
            'RUN_ID': '1',
            'WORKFLOW_NAME': 'w',
            'RUN_URL': 'u',
            'NOTIFY_ISSUE': '2',
            'OPENROUTER_MODEL': 'other/model',
        }
        with mock.patch.dict('os.environ', env, clear=True):
            config = _cli._read_config()
        self.assertEqual(config.model, 'other/model')
        _, urlopen, _ = self._call(self._ok(), model=config.model)
        sent = json.loads(urlopen.call_args.args[0].data.decode())
        self.assertEqual(
            sent['models'], ['other/model', 'deepseek/deepseek-chat', 'deepseek/deepseek-v3.2']
        )

    def test_no_openrouter_model_means_no_override(self):
        env = {'REPO': 'o/r', 'RUN_ID': '1', 'WORKFLOW_NAME': 'w', 'RUN_URL': 'u'}
        for value in (None, ''):
            with self.subTest(value=value):
                extra = {} if value is None else {'OPENROUTER_MODEL': value}
                with mock.patch.dict(
                    'os.environ', {**env, 'NOTIFY_ISSUE': '2', **extra}, clear=True
                ):
                    self.assertIsNone(_cli._read_config().model)

    def test_the_answering_model_is_returned_and_logged(self):
        sleep = mock.Mock()
        log = io.StringIO()
        with (
            mock.patch.object(
                _openrouter.urllib.request,
                'urlopen',
                return_value=self._ok('deepseek/deepseek-v3.2'),
            ),
            contextlib.redirect_stderr(log),
        ):
            _, answered_by = _openrouter.call_openrouter(
                'sys', 'user', 'deepseek/deepseek-chat', 'k', sleep=sleep
            )
        self.assertEqual(answered_by, 'deepseek/deepseek-v3.2')
        self.assertIn('answered with model deepseek/deepseek-v3.2', log.getvalue())

    def test_a_reply_without_a_model_is_put_down_to_the_first_one_asked_for(self):
        for model, expected in (('some/model', 'some/model'), (None, 'deepseek/deepseek-chat')):
            with self.subTest(model=model):
                (_, answered_by), _, _ = self._call(self._ok(), model=model)
                self.assertEqual(answered_by, expected)

    def _http_error(
        self, status: int, body: bytes = b'', headers: dict[str, str] | None = None
    ) -> urllib.error.HTTPError:
        # HTTPError holds a file object and warns on implicit cleanup, which
        # the unit env's -W error turns into a failure. Give it a real `fp`
        # (it fabricates a tempfile when passed None) and close it explicitly.
        message = email.message.Message()
        for key, value in (headers or {}).items():
            message[key] = value
        error = urllib.error.HTTPError(
            'https://openrouter.ai/api/v1/chat/completions',
            status,
            'boom',
            message,
            io.BytesIO(body),
        )
        self.addCleanup(error.close)
        return error

    def test_http_error_raises_so_main_can_fall_back(self):
        self._call_raises(self._http_error(500), self._http_error(500), self._http_error(500))

    def test_a_429_then_success_returns_the_second_response(self):
        (result, _), urlopen, sleep = self._call(self._http_error(429), self._ok())
        self.assertEqual(result, {'action': 'new', 'body': 'b'})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(_constants.RETRY_DELAYS[0])

    def test_two_503s_then_success(self):
        (result, _), urlopen, sleep = self._call(
            self._http_error(503), self._http_error(503), self._ok()
        )
        self.assertEqual(result, {'action': 'new', 'body': 'b'})
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [5.0, 15.0])

    def test_other_4xx_are_not_retried(self):
        """A 400 is the request and a 402 is the key's credit: neither is a wait-and-see."""
        for status in (400, 401, 402, 403, 404):
            with self.subTest(status=status):
                message, urlopen, sleep = self._call_raises(self._http_error(status), self._ok())
                self.assertEqual(urlopen.call_count, 1)
                sleep.assert_not_called()
                self.assertIn(f'HTTP Error {status}', message)
                self.assertIn('after 1 attempt)', message)

    def test_three_429s_give_up_with_the_count_and_the_last_detail(self):
        message, urlopen, sleep = self._call_raises(
            self._http_error(429, b'{"error": {"message": "first"}}'),
            self._http_error(429, b'{"error": {"message": "second"}}'),
            self._http_error(429, b'{"error": {"message": "Provider returned error"}}'),
            self._ok(),
        )
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertIn('HTTP Error 429', message)
        self.assertIn('Provider returned error', message)
        self.assertNotIn('first', message)
        self.assertIn('after 3 attempts', message)

    def test_retry_after_is_honoured(self):
        _, _, sleep = self._call(self._http_error(429, headers={'Retry-After': '2'}), self._ok())
        sleep.assert_called_once_with(2.0)

    def test_retry_after_is_capped(self):
        _, _, sleep = self._call(self._http_error(429, headers={'Retry-After': '600'}), self._ok())
        sleep.assert_called_once_with(_constants.MAX_RETRY_WAIT)

    def test_a_retry_after_date_is_honoured_and_capped(self):
        _, _, sleep = self._call(
            self._http_error(503, headers={'Retry-After': 'Fri, 31 Dec 2100 23:59:59 GMT'}),
            self._ok(),
        )
        sleep.assert_called_once_with(_constants.MAX_RETRY_WAIT)

    def test_an_unreadable_retry_after_falls_back_to_the_default_delay(self):
        _, _, sleep = self._call(
            self._http_error(429, headers={'Retry-After': 'soon'}), self._ok()
        )
        sleep.assert_called_once_with(_constants.RETRY_DELAYS[0])

    def test_the_total_wait_is_capped(self):
        with (
            mock.patch.object(_openrouter, 'RETRY_DELAYS', (5.0, 15.0)),
            mock.patch.object(_openrouter, 'MAX_RETRY_WAIT', 30.0),
            mock.patch.object(_openrouter, 'MAX_TOTAL_RETRY_WAIT', 40.0),
        ):
            _, _, sleep = self._call(
                self._http_error(429, headers={'Retry-After': '30'}),
                self._http_error(429, headers={'Retry-After': '30'}),
                self._ok(),
            )
        # 30 and then only the 10 left of the 40, not another 30.
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [30.0, 10.0])

    def test_the_real_caps_hold_against_a_long_retry_after(self):
        """However long OpenRouter asks for, the waits add up to at most a minute."""
        long_wait = {'Retry-After': '3600'}
        message, urlopen, sleep = self._call_raises(
            self._http_error(429, headers=long_wait),
            self._http_error(429, headers=long_wait),
            self._http_error(429, headers=long_wait),
        )
        self.assertEqual(urlopen.call_count, 3)
        waits = [c.args[0] for c in sleep.call_args_list]
        self.assertTrue(all(w <= _constants.MAX_RETRY_WAIT for w in waits), waits)
        self.assertLessEqual(sum(waits), _constants.MAX_TOTAL_RETRY_WAIT)
        self.assertIn('after 3 attempts', message)

    def test_a_timeout_is_retried(self):
        # `socket.timeout` is an alias of `TimeoutError` from Python 3.10, so
        # the first entry covers it too.
        for error in (
            TimeoutError('timed out'),
            urllib.error.URLError('connection refused'),
            ConnectionResetError('reset by peer'),
        ):
            with self.subTest(error=type(error).__name__):
                (result, _), urlopen, sleep = self._call(error, self._ok())
                self.assertEqual(result, {'action': 'new', 'body': 'b'})
                self.assertEqual(urlopen.call_count, 2)
                sleep.assert_called_once()

    def test_each_retry_is_one_log_line_without_the_key(self):
        log = io.StringIO()
        sleep = mock.Mock()
        with (
            mock.patch.object(
                _openrouter.urllib.request,
                'urlopen',
                side_effect=[self._http_error(429), TimeoutError('timed out'), self._ok()],
            ),
            contextlib.redirect_stderr(log),
        ):
            _openrouter.call_openrouter('sys', 'user', 'm', 'secret-key', sleep=sleep)
        lines = [line for line in log.getvalue().splitlines() if 'retrying' in line]
        self.assertEqual(len(lines), 2)
        self.assertIn('attempt 1 of 3', lines[0])
        self.assertIn('HTTP Error 429', lines[0])
        self.assertIn('retrying in 5s', lines[0])
        self.assertIn('attempt 2 of 3', lines[1])
        self.assertIn('timed out', lines[1])
        self.assertIn('retrying in 15s', lines[1])
        self.assertNotIn('secret-key', log.getvalue())

    def test_the_error_carries_openrouters_own_explanation(self):
        """A 400 says only "Bad Request"; which of the model, key or schema is in the body."""
        body = json.dumps({
            'error': {'code': 400, 'message': "Invalid schema: 'required' is missing 'also'"}
        }).encode()
        message, _, _ = self._call_raises(self._http_error(400, body))
        self.assertIn('HTTP Error 400', message)
        self.assertIn("'required' is missing 'also'", message)

    def test_a_reply_in_a_code_fence_is_unwrapped(self):
        envelope = {'action': 'new', 'body': 'b'}
        for content in (
            f'```json\n{json.dumps(envelope)}\n```',
            f'```\n{json.dumps(envelope, indent=2)}\n```',
            f'\n  ```json  \n{json.dumps(envelope)}\n  ```\n',
        ):
            with self.subTest(content=content):
                with (
                    mock.patch.object(
                        _openrouter.urllib.request,
                        'urlopen',
                        return_value=self._response(content, 'deepseek/deepseek-v3.2'),
                    ),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    result, _ = _openrouter.call_openrouter('sys', 'user', 'm', 'k')
                self.assertEqual(result, envelope)

    def test_only_a_fence_around_the_whole_reply_is_unwrapped(self):
        envelope = json.dumps({'action': 'new', 'body': 'b'})
        for content in (f'Here you go:\n```json\n{envelope}\n```', f'```json\n{envelope}'):
            with (
                self.subTest(content=content),
                mock.patch.object(
                    _openrouter.urllib.request,
                    'urlopen',
                    return_value=self._response(content, 'm'),
                ),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(json.JSONDecodeError),
            ):
                _openrouter.call_openrouter('sys', 'user', 'm', 'k')

    def test_a_body_that_is_not_json_is_reported_as_it_came(self):
        error = b'<html>upstream is unwell</html>'
        message, _, _ = self._call_raises(
            self._http_error(502, error),
            self._http_error(502, error),
            self._http_error(502, error),
        )
        self.assertIn('upstream is unwell', message)

    def test_an_unreadable_body_still_leaves_the_status(self):
        errors = [self._http_error(429) for _ in range(3)]
        for error in errors:
            error.read = mock.Mock(side_effect=OSError('connection reset'))
        message, _, _ = self._call_raises(*errors)
        self.assertIn('HTTP Error 429', message)


class ResolveOriginTests(unittest.TestCase):
    """`notify` hands us the issue it touched, and that is the only lookup."""

    def test_passed_issue_is_used_without_any_search(self):
        with mock.patch.object(
            _github, 'fetch_issue_texts', return_value=['no markers here']
        ) as fetch:
            enriched, kind, origin = _github.resolve_origin('o/r', '123', 4242, 'comment')
        self.assertEqual((enriched, kind, origin), (None, 'comment', 4242))
        # The read-your-writes hazard is gone because the only issue we read
        # is the one we were handed -- nothing is searched for.
        fetch.assert_called_once_with('o/r', 4242)

    def test_passed_issue_wins_over_a_missing_marker(self):
        """A marker we cannot find does not make the issue the wrong issue."""
        with mock.patch.object(_github, 'fetch_issue_texts', return_value=['']):
            _enriched, kind, origin = _github.resolve_origin('o/r', '123', 77, 'new')
        self.assertEqual((kind, origin), ('new', 77))

    def test_rung_zero_still_detected_on_the_passed_issue(self):
        """The notifier cannot tell us this: it is a fact about an earlier
        run of *this* script, so the narrowed lookup still has to find it."""
        body = f'<!-- {_constants.MARKER_PREFIX}:run=123:sig=abcdef0123456789 -->'
        with mock.patch.object(_github, 'fetch_issue_texts', return_value=[body]):
            enriched, _kind, origin = _github.resolve_origin('o/r', '123', 4242, 'new')
        self.assertEqual((enriched, origin), (4242, 4242))

    def test_rung_zero_ignores_a_marker_for_a_different_run(self):
        body = f'<!-- {_constants.MARKER_PREFIX}:run=999:sig=abcdef0123456789 -->'
        with mock.patch.object(_github, 'fetch_issue_texts', return_value=[body]):
            enriched, _kind, _origin = _github.resolve_origin('o/r', '123', 4242, 'new')
        self.assertIsNone(enriched)
