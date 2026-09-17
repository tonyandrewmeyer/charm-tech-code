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


def normalise_envelope(envelope: Any) -> tuple[Any, list[str]]:
    """Drop inapplicable fields from the envelope and each `also` entry."""
    if not isinstance(envelope, dict):
        return envelope, []
    cleaned, dropped = drop_inapplicable_fields(coerce_target_issue(envelope))
    notes = [f'envelope: {f}' for f in dropped]
    also = cleaned.get('also')
    if isinstance(also, list):
        entries: list[Any] = []
        for i, entry in enumerate(also):
            entry, entry_dropped = drop_inapplicable_fields(coerce_target_issue(entry))
            notes += [f'envelope.also[{i}]: {f}' for f in entry_dropped]
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
