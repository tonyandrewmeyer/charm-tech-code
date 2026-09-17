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


"""The OpenRouter call."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ._envelope import ENVELOPE_JSON_SCHEMA


def call_openrouter(
    system_prompt: str, user_prompt: str, model: str, api_key: str
) -> dict[str, Any]:
    """POST the prompt to OpenRouter with the envelope schema, return the parsed JSON.

    Uses urllib rather than requests so the script has no third-party
    dependencies at all. A non-2xx response is raised as an error carrying
    OpenRouter's own explanation, which main() treats the same as any other
    OpenRouter failure: fall back to the plain body.
    """
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'response_format': {
            'type': 'json_schema',
            'json_schema': {
                'name': 'ai_failure_notification',
                'strict': True,
                'schema': ENVELOPE_JSON_SCHEMA,
            },
        },
    }
    request = urllib.request.Request(
        'https://openrouter.ai/api/v1/chat/completions',
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        # S310: the URL is a literal https endpoint, not caller-controlled.
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        # `str(exc)` is only ever "HTTP Error 400: Bad Request", which says
        # nothing about which of the model, the key or the schema OpenRouter
        # objected to. The reason is in the response body, and reading it is
        # the difference between a glance and an afternoon.
        raise RuntimeError(f'{exc} - {_error_detail(exc)}') from exc
    content = body['choices'][0]['message']['content']
    return json.loads(content)


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """OpenRouter's own explanation of a non-2xx, as far as it can be read.

    The body is JSON in the ordinary case and can be anything at all when a
    proxy answers instead, so nothing here is allowed to raise: an unreadable
    explanation must not replace the status code that came with it.
    """
    try:
        raw = exc.read().decode(errors='replace').strip()
    except Exception:  # noqa: BLE001 - any read failure means no detail, not a crash.
        return 'no response body'
    if not raw:
        return 'empty response body'
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw[:500]
    error = parsed.get('error') if isinstance(parsed, dict) else None
    if isinstance(error, dict) and error.get('message'):
        return str(error['message'])[:500]
    return raw[:500]
