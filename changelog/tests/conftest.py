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

"""Nothing in this package's library may read the clock.

A library function that reads the clock makes the output of a release depend
on which day CI happened to run, and makes the function impossible to assert
on. So the date is an argument, and this fixture is what keeps it one: it
replaces the `datetime` module as each library module sees it, so a `now()`
or `today()` call anywhere under it fails the whole suite rather than quietly
passing on every day except the one that matters.

`_cli` is deliberately not in the list. A console script has to get a date
from somewhere for `--date` to be optional, so it is the package's I/O
boundary and the one module allowed to look: see its `_today`. Everything it
calls is still inside the fixture, so the boundary cannot drift inwards
without a test failing.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import charm_tech_code.changelog


class _NoClock:
    """Stands in for `datetime.datetime` and `datetime.date`."""

    @staticmethod
    def now(*args: object, **kwargs: object):
        raise AssertionError(
            'The clock was read by the package itself. `format_changes` takes '
            'a date argument precisely so that it does not do this.'
        )

    today = now
    utcnow = now


class _NoClockModule:
    datetime = _NoClock
    date = _NoClock


#: The one module allowed to read the clock. See the module docstring.
UNCHECKED_MODULES = frozenset({'_cli'})


def _library_modules() -> tuple[object, ...]:
    """Every module of the library except `_cli`.

    Discovered rather than listed, so that a module added later is covered
    without anyone having to remember this file exists. `raising=False` below
    means a module that does not import `datetime` today is covered in
    advance rather than having to be remembered when it does.
    """
    package = charm_tech_code.changelog
    modules = tuple(
        importlib.import_module(f'{package.__name__}.{info.name}')
        for info in pkgutil.iter_modules(package.__path__)
        if info.name not in UNCHECKED_MODULES
    )
    # An empty tuple would make the fixture a no-op that still passes, which
    # is the one way this can fail without anyone noticing.
    assert modules, 'no library modules discovered'
    return modules


LIBRARY_MODULES = _library_modules()


@pytest.fixture(autouse=True)
def no_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in LIBRARY_MODULES:
        monkeypatch.setattr(module, 'datetime', _NoClockModule, raising=False)
