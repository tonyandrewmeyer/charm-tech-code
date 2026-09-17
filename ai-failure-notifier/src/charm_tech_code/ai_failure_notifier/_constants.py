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


"""Regexes, marker shapes and tuning constants."""

from __future__ import annotations

import re

MARKER_PREFIX = 'ai-failure-notifications'
DEFAULT_MODEL = 'deepseek/deepseek-chat'  # DeepSeek V3 on OpenRouter.
CLOSED_CANDIDATE_WINDOW_DAYS = 14
MAX_CANDIDATES = 3

# Colour escapes, which Actions logs are full of. Two alternatives, because
# the logs contain both the real thing and a mangled form where the ESC byte
# has already been stripped, leaving a bare "[32m".
ANSI = re.compile(
    r"""
    \x1b\[ [0-9;]* [A-Za-z]   # a full escape: ESC [ params letter
    |
    \[ \d+ (?:;\d+)* m        # ESC already stripped: [32m, [1;33m
    """,
    re.VERBOSE,
)

# The timestamp Actions prefixes to every log line, for example
# "2026-07-21T16:17:04.8204062Z ". Stripped before anything else is matched.
TS = re.compile(
    r"""
    ^\d{4}-\d{2}-\d{2}        # date: 2026-07-21
    T\d{2}:\d{2}:\d{2}        # time: T16:17:04
    \.\d+Z[ ]                 # fractional seconds, zone, one trailing space
    """,
    re.VERBOSE,
)

# Actions' own annotation for a failing step.
ERROR_MARKER = re.compile(r'##\[error\]')

# The runner opens every step with "##[group]Run <script>", echoes the whole
# `run:` block a line at a time, dumps the step's env, and closes with
# "##[endgroup]". None of that is output: it is the step's own source. A
# multi-line `run:` therefore puts its every branch into the log, including
# the ones that did not execute, and parsing it produces failures the run
# never had.
#
# Colour is not a usable signal here -- the runner marks echoed lines cyan-
# bold, but ANSI above strips that before anything is matched -- so the
# group boundary is what separates a step's script from its output.
GROUP_RUN_START = re.compile(r'^##\[group\]Run ')
GROUP_END = re.compile(r'^##\[endgroup\]')

# A line from pytest's short summary, for example
# "FAILED tests/integration/test_charm.py::test_deploy - TimeoutError: ...".
PYTEST_SUMMARY = re.compile(
    r"""
    ^(FAILED|ERROR)[ ]         # which of the two pytest reports
    (\S+?)[ ]-[ ]              # the test id, up to the " - " separator
    (.+)$                      # the error message, to end of line
    """,
    re.VERBOSE,
)

# The end of pytest's summary section, for example
# "======== 3 failed, 41 passed, 2 warnings in 512.44s ========".
PYTEST_SUMMARY_END = re.compile(
    r"""
    ={3,}                      # the run of = that brackets the line
    .*(failed|passed|error)    # and one of pytest's outcome words
    """,
    re.VERBOSE,
)

# A failing Go test, for example "--- FAIL: TestFoo (0.01s)".
GO_FAIL = re.compile(r'^--- FAIL: (\S+)')

# The last line of a Python traceback: the exception type and its message,
# for example "KeyError: 'loki/0'". Deliberately narrow -- it must look like an
# exception class name -- so that arbitrary "word: text" log lines don't match.
TRACEBACK_END = re.compile(
    r"""
    ^([A-Z]                          # exception types start with a capital
      [A-Za-z_.]*                    # dotted path allowed: ops.pebble.APIError
      (?:Error|Exception|Warning))   # and conventionally end one of three ways
    :[ ](.*)$                        # then ": " and the message
    """,
    re.VERBOSE,
)

# Matches markers stamped by either workflow:
#   notifier:  <!-- ai-failure-notifications:run=123:origin=new -->
#              <!-- ai-failure-notifications:run=123:origin=comment -->
#   enricher:  <!-- ai-failure-notifications:run=123:sig=abcdef0123456789 -->
MARKER_RE = re.compile(
    r'<!--\s*'
    + re.escape(MARKER_PREFIX)
    + r"""
    :run=(?P<run_id>\d+)
    (?::origin=(?P<origin>new|comment))?
    (?::sig=(?P<sig>[0-9a-f]+))?
    \s*-->
    """,
    re.VERBOSE,
)

# The signature stamp, a second hidden comment the enricher writes beside the
# marker above:
#   <!-- ai-failure-notifications:signature {"run":"123","tests":[...],...} -->
#
# The marker's :sig= is a sha1, which is one-way: it can say "the same run was
# already enriched here" and nothing else. This carries the fields themselves,
# so that the next run's candidate block can show a previous failure's test ids
# and error classes -- which is what the prompt's strong rung matches on, and
# what no candidate ever carried before. Deliberately a separate comment rather
# than a longer marker: MARKER_RE stays exactly as it was, and rung zero cannot
# be affected by anything that happens here.
#
# Non-greedy to the first "-->" so two stamps in one body parse as two.
SIGNATURE_STAMP_RE = re.compile(
    r'<!--\s*' + re.escape(MARKER_PREFIX) + r':signature\s+(?P<json>\{.*?\})\s*-->',
    re.DOTALL,
)

# An exception class as it appears in a pytest summary line or a traceback's
# last line: the leading dotted path is allowed, the trailing ": message" is
# not part of it. Narrower than TRACEBACK_END because it has to match mid-line.
ERROR_CLASS = re.compile(r'\b[A-Z][A-Za-z_.]*(?:Error|Exception|Warning)\b')

# How many of each field the stamp keeps. A run with fifty failing tests does
# not need fifty node ids in someone else's candidate block; the first few are
# what a match is made on, and the cap bounds both the issue body and the
# prompt.
MAX_STAMPED_ITEMS = 6

# Bounds on what one candidate contributes to the block. Three candidates times
# these is the whole cost of the change, in tokens.
MAX_EXCERPT_CHARS = 300
MAX_COMMENT_CHARS = 400
# How many of a candidate's trailing comments the block shows. See
# CandidateIssue.recent_comments for why it is not 1.
MAX_RECENT_COMMENTS = 2
MAX_SIGNATURE_CHARS = 600
