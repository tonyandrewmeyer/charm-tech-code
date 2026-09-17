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


"""Validating the model's response envelope.

ENVELOPE_JSON_SCHEMA below is what OpenRouter is asked to conform to, and it
is also what the applier side re-checks the response against, via
`jsonschema`. One spec, checked in both directions: an earlier hand-rolled
validator mirrored the schema in about 130 lines of Python, and the two drifted.
"""

from __future__ import annotations

from typing import Any

import jsonschema

# Fields that only mean something for one of the two actions. The model is
# given a `strict` schema, so it tends to return every declared property and
# fill in the ones that do not apply to the action it chose. Those are dropped
# rather than treated as an error: we would not act on them either way, and
# rejecting the envelope threw away a usable body and fell back to the plain
# notice.
_ACTION_ONLY_FIELDS = {
    'comment': ('title', 'labels', 'issue_type'),
    'new': ('target_issue',),
}


def drop_inapplicable_fields(entry: Any) -> tuple[Any, list[str]]:
    """Strip fields that do not apply to `entry`'s action; report what went."""
    if not isinstance(entry, dict):
        return entry, []
    fields = _ACTION_ONLY_FIELDS.get(entry.get('action'))
    if not fields:
        return entry, []
    dropped = [f for f in fields if f in entry]
    if not dropped:
        return entry, []
    return {k: v for k, v in entry.items() if k not in dropped}, dropped


def drop_unknown_fields(entry: Any, allowed: frozenset[str]) -> tuple[Any, list[str]]:
    """Strip properties the schema does not declare; report what went.

    The third repair on this path, and it exists for the same reason as the
    other two: the schema sets `additionalProperties: false`, so one extra
    property the model invented rejects the whole envelope, and `_cli` then
    throws away a perfectly good title and body and leaves the notifier's
    placeholder standing. The 2026-09-16 harness saw it once in fifteen cells
    (an extra `html_url`), which is not a rate but is not zero.

    It is safe to drop rather than merely tolerate, and the reason is narrow:
    an unknown property is by definition one `apply_entry` never reads, so
    removing it cannot change what gets posted -- whereas rejecting the
    envelope demonstrably changes what gets posted, for the worse. Loosening
    the schema instead would hide the drift; stripping it keeps
    `additionalProperties: false` meaningful and puts the field name in the
    step summary.

    `allowed` is derived from the schema, not written out again here, because a
    hand-maintained second copy of the property list is exactly the drift this
    module's docstring was written about.
    """
    if not isinstance(entry, dict):
        return entry, []
    unknown = [k for k in entry if k not in allowed]
    if not unknown:
        return entry, []
    return {k: v for k, v in entry.items() if k in allowed}, unknown


def coerce_target_issue(entry: Any) -> Any:
    """Turn a `target_issue` the model wrote as text into the integer it means.

    Issues are written `#44` everywhere a person sees them, and the model
    returns that string often enough to matter: the schema wants an integer, so
    the whole envelope was rejected and a usable body was thrown away for a `#`.
    Anything that is not a plain issue reference is left exactly as it is, for
    the schema to reject on its own terms.
    """
    if not isinstance(entry, dict) or not isinstance(entry.get('target_issue'), str):
        return entry
    text = entry['target_issue'].strip().removeprefix('#')
    if not text.isdigit():
        return entry
    return {**entry, 'target_issue': int(text)}


# Keys the model writes the comment text under when it does not write `body`.
# Every one of these has been observed on a real response; none of them is a
# guess at what the model "probably meant". `comment` is the whole of cell B4
# of the 2026-09-17 harness round: the text was there, correct and complete,
# under a key one word different from the schema's.
_BODY_ALIASES = ('comment', 'comment_body', 'issue_body')


def recover_body(entry: Any) -> tuple[Any, str | None]:
    """Find the body text the model wrote somewhere other than `body`.

    Only ever a rename, never a synthesis: if none of the known aliases holds a
    non-empty string, this returns the entry untouched and `validate_envelope`
    rejects it as it should. `body` already present and non-empty always wins.
    """
    if not isinstance(entry, dict):
        return entry, None
    if isinstance(entry.get('body'), str) and entry['body'].strip():
        return entry, None
    for alias in _BODY_ALIASES:
        value = entry.get(alias)
        if isinstance(value, str) and value.strip():
            return {**entry, 'body': value}, alias
    return entry, None


def fall_back_to_dedup_reason(entry: Any) -> tuple[Any, bool]:
    """For a comment with no body at all, post the model's own reason instead.

    This is the one repair here that reuses a field for a purpose it was not
    written for, so the case for it is worth stating. On the 2026-09-17 round
    three cells chose `action: "comment"`, named a correct target, wrote a
    `dedup_reason` naming the matching tests -- and omitted `body`. The
    alternative to this repair is what the code did before it: discard all of
    that and post `Scheduled workflow 'X' failed.` on the *notifier's* issue,
    losing the diagnosis and the dedup decision together. Compare what the two
    produce on cell B6:

        dedup_reason: "Same workflow, same failing step, and same tests failing
        as in #2633: `test_deploy_cos` (timeout), `test_integrate_loki`
        (timeout), `test_loki_data` (KeyError), and
        `test_workload_version_is_set` (AssertionError)."

        plain fallback: "Scheduled workflow 'Example Charm Integration Tests'
        failed."

    Nothing is invented: the text is the model's own prose about this failure
    and this issue. Restricted to `action: "comment"`, where the prompt already
    asks for one short paragraph saying what matches -- which is what
    `dedup_reason` is. A missing body on `action: "new"` is NOT repaired this
    way: a new issue is a bigger artefact than one sentence, and it still has
    the notifier's placeholder to fall back to.
    """
    if not isinstance(entry, dict) or entry.get('action') != 'comment':
        return entry, False
    if isinstance(entry.get('body'), str) and entry['body'].strip():
        return entry, False
    reason = entry.get('dedup_reason')
    if not isinstance(reason, str) or not reason.strip():
        return entry, False
    return {**entry, 'body': reason.strip()}, True


def _normalise_entry(entry: Any, path: str, allowed: frozenset[str]) -> tuple[Any, list[str]]:
    """Coerce, recover the body, strip unknown properties, drop inapplicable ones."""
    notes: list[str] = []
    entry, alias = recover_body(coerce_target_issue(entry))
    if alias:
        notes.append(f'{path}: read the body from "{alias}"')
    entry, used_reason = fall_back_to_dedup_reason(entry)
    if used_reason:
        notes.append(f'{path}: no body supplied; commented with dedup_reason instead')
    entry, unknown = drop_unknown_fields(entry, allowed)
    notes += [f'{path}: {f} (not in the schema)' for f in unknown]
    entry, dropped = drop_inapplicable_fields(entry)
    notes += [f'{path}: {f}' for f in dropped]
    return entry, notes


def normalise_envelope(envelope: Any) -> tuple[Any, list[str]]:
    """Repair what the applier can read anyway, in the envelope and each `also` entry.

    Every repair here exists because, before it existed, a whole enrichment was
    discarded and the notifier's placeholder was left standing in its place.
    A `"#44"` becomes 44; a body written under `comment` is read from there; a
    comment with no body at all falls back to the model's own `dedup_reason`; a
    property that does not apply to the chosen action goes; a property the
    schema does not declare at all goes.

    This is all necessary because **`response_format`'s `strict` is not
    actually enforced** by the provider the default model routes to. Two things
    seen on real responses prove it rather than suggest it: an envelope
    carrying a property the schema does not declare (`html_url`, `comment`),
    which strict mode cannot emit, and an envelope missing a `required`
    property (`body`), which strict mode also cannot emit. The schema is
    therefore a strong hint to the model and a real check on the way back, and
    the way back has to be able to cope.

    What is not repaired is anything that would amount to choosing on the
    model's behalf. Stripping an unknown `issue` does not invent the
    `target_issue` it was probably meant to be, and a `new` action with no body
    is not given one, so either is still rejected rather than silently acted on.
    """
    if not isinstance(envelope, dict):
        return envelope, []
    cleaned, notes = _normalise_entry(envelope, 'envelope', _TOP_LEVEL_PROPERTIES)
    also = cleaned.get('also')
    if isinstance(also, list):
        entries: list[Any] = []
        for i, entry in enumerate(also):
            entry, entry_notes = _normalise_entry(entry, f'envelope.also[{i}]', _ENTRY_PROPERTIES)
            notes += entry_notes
            entries.append(entry)
        cleaned = {**cleaned, 'also': entries}
    return cleaned, notes


ENVELOPE_JSON_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    'title': 'ai-failure-notifications envelope',
    'type': 'object',
    'required': ['action', 'body', 'dedup_reason', 'confidence'],
    'properties': {
        'action': {'enum': ['comment', 'new']},
        'body': {'type': 'string', 'minLength': 1},
        'dedup_reason': {'type': 'string', 'minLength': 1},
        'confidence': {'enum': ['high', 'medium', 'low']},
        # Nullable, because the schema is sent to OpenRouter as `strict`: the
        # model returns every declared property and nulls the ones that do not
        # apply to the action it chose. A null reads as "not supplied"; only a
        # real value for the wrong action is an error.
        'title': {'type': ['string', 'null'], 'minLength': 1},
        'labels': {'type': ['array', 'null'], 'items': {'type': 'string'}},
        'issue_type': {'type': ['string', 'null']},
        'target_issue': {'type': ['integer', 'null'], 'minimum': 1},
        'also': {'type': 'array', 'maxItems': 2, 'items': {'$ref': '#/$defs/envelopeEntry'}},
    },
    'additionalProperties': False,
    'allOf': [{'$ref': '#/$defs/actionConditionals'}],
    '$defs': {
        'actionConditionals': {
            'allOf': [
                {
                    'if': {'properties': {'action': {'const': 'new'}}},
                    'then': {
                        'required': ['title', 'labels', 'issue_type'],
                        'properties': {'target_issue': {'type': 'null'}},
                    },
                },
                {
                    'if': {'properties': {'action': {'const': 'comment'}}},
                    'then': {
                        'required': ['target_issue'],
                        'properties': {
                            'target_issue': {'type': 'integer', 'minimum': 1},
                            'title': {'type': 'null'},
                            'labels': {'type': 'null'},
                            'issue_type': {'type': 'null'},
                        },
                    },
                },
            ]
        },
        'envelopeEntry': {
            'type': 'object',
            'required': ['action', 'body', 'dedup_reason', 'confidence'],
            'properties': {
                'action': {'enum': ['comment', 'new']},
                'body': {'type': 'string', 'minLength': 1},
                'dedup_reason': {'type': 'string', 'minLength': 1},
                'confidence': {'enum': ['high', 'medium', 'low']},
                'title': {'type': ['string', 'null'], 'minLength': 1},
                'labels': {'type': ['array', 'null'], 'items': {'type': 'string'}},
                'issue_type': {'type': ['string', 'null']},
                'target_issue': {'type': ['integer', 'null'], 'minimum': 1},
            },
            'additionalProperties': False,
            'allOf': [{'$ref': '#/$defs/actionConditionals'}],
        },
    },
}


# Derived from the schema above, never written out a second time: this is the
# list `drop_unknown_fields` strips against, and a hand-maintained copy of it
# would be the same drift this module exists to have stopped.
_TOP_LEVEL_PROPERTIES = frozenset(ENVELOPE_JSON_SCHEMA['properties'])
_ENTRY_PROPERTIES = frozenset(ENVELOPE_JSON_SCHEMA['$defs']['envelopeEntry']['properties'])

_VALIDATOR = jsonschema.Draft202012Validator(ENVELOPE_JSON_SCHEMA)


def _error_path(error: jsonschema.ValidationError) -> str:
    """Render an error's location the way the old validator did: `envelope.also[0].body`."""
    path = 'envelope'
    for part in error.absolute_path:
        path += f'[{part}]' if isinstance(part, int) else f'.{part}'
    return path


def validate_envelope(envelope: Any) -> list[str]:
    """Validate a top-level envelope (may carry `also`).

    Returns a list of human-readable errors; empty list means valid.
    """
    return [
        f'{_error_path(error)}: {error.message}'
        for error in sorted(_VALIDATOR.iter_errors(envelope), key=jsonschema.exceptions.relevance)
    ]
