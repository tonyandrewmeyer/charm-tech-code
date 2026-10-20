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

    The schema sets `additionalProperties: false`, so a single property the
    model invented would reject the whole envelope, and `_cli` would discard a
    usable title and body and leave the notifier's placeholder standing.

    An unknown property is by definition one `apply_entry` never reads, so
    removing it can't change what gets posted. Stripping it, rather than
    loosening the schema, keeps `additionalProperties: false` meaningful and
    puts the field name in the step summary.

    `allowed` is derived from the schema so that there is only one copy of the
    property list.
    """
    if not isinstance(entry, dict):
        return entry, []
    unknown = [k for k in entry if k not in allowed]
    if not unknown:
        return entry, []
    return {k: v for k, v in entry.items() if k in allowed}, unknown


def _normalise_entry(entry: Any, path: str, allowed: frozenset[str]) -> tuple[Any, list[str]]:
    """Strip unknown properties, then drop inapplicable ones."""
    entry, unknown = drop_unknown_fields(entry, allowed)
    notes = [f'{path}: {f} (not in the schema)' for f in unknown]
    entry, dropped = drop_inapplicable_fields(entry)
    notes += [f'{path}: {f}' for f in dropped]
    return entry, notes


def normalise_envelope(envelope: Any) -> tuple[Any, list[str]]:
    """Repair what the applier can read anyway, in the envelope and each `also` entry.

    Each repair turns an envelope that would otherwise be rejected, leaving the
    notifier's placeholder standing, into one the applier can act on. A
    property that doesn't apply to the chosen action goes; a property the
    schema doesn't declare at all goes.

    What is not repaired is anything that would amount to choosing on the
    model's behalf. Stripping an unknown `issue` does not invent the
    `target_issue` it was probably meant to be, and a missing body is not
    filled in, so either is still rejected rather than silently acted on.
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


# Derived from the schema above rather than written out a second time, so the
# lists `drop_unknown_fields` strips against can't drift from it.
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


def describe_envelope(envelope: dict[str, Any]) -> str:
    """Summarise a validated envelope's decision for the log, one line per entry.

    The body and title end up on the issue, so they are left out. What is not
    posted anywhere is why the model routed the failure where it did, and that
    is what this records.
    """
    lines: list[str] = []
    for label, entry in [('envelope', envelope)] + [
        (f'also[{i}]', e) for i, e in enumerate(envelope.get('also') or [])
    ]:
        where = f'#{entry["target_issue"]}' if entry['action'] == 'comment' else 'new issue'
        lines.append(
            f'{label}: {entry["action"]} on {where}, confidence {entry["confidence"]}. '
            f'Reason: {entry["dedup_reason"]}'
        )
    return '\n'.join(lines)
