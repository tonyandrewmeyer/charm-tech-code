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


"""Turn a range of commits into our changelog format.

Text in, structured data and formatted strings out. Nothing here touches the
network, git, the filesystem or the clock: the `changelog` console script
(`_cli`) is the package's one I/O boundary, and is how a workflow step calls
this. See `_constants` for the format, and why `chore` commits do not appear
in a changelog.
"""
