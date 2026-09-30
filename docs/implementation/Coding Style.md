# Libranet Coding Style

Version 0.1 • September 2026

---

## 1. Purpose

This document describes how the Python in this repository is written: how a
file is laid out, how things are named and typed, how errors are raised and
logged, and how tests are written. It was drawn from the code as it stands at
Phase 2 Step 51, not from an outside standard, so each rule is one the code
already follows. The few places where the code does not are listed in §12.

A rule here is one of three kinds:

- **Checked by a tool**: `black`, `flake8`, `mypy`, or `pylint` fails the
  build (§2).
- **Checked by a test**: a test in `tests/` fails.
- **Convention**: nothing checks it; it is kept by hand and in review.

A rule is a convention unless its section says what checks it. Where no rule
covers a case, match the code around it.
[`CLAUDE.md`](../../CLAUDE.md) holds the short list of rules a Claude Code
session is given, which this document expands on. How modules, messages, and
processes fit together is in [Module System](Module%20System.md).

## 2. What the Tools Check

```bash
uv run black .           # format
uv run flake8            # lint
uv run mypy              # type-check
uv run pylint src tests scripts hatch_build.py   # lint further
uv run pytest --cov      # tests, with coverage
```

CI runs all five on every pull request, and the tests on Ubuntu and macOS
against Python 3.11 and 3.14.

| Tool | Settings | What it holds to |
| --- | --- | --- |
| `black` | Line length 100, target `py311` | Layout of code: quotes, wrapping, trailing commas |
| `flake8` | `.flake8`, with `flake8-bugbear`; `F401` allowed in a package's `__init__.py` | Unused names, undefined names, PEP 8 spacing, likely bugs, and any line past 110 columns (`B950`) |
| `mypy` | `strict`, `warn_unreachable`, over `src`, `tests`, and `scripts` | Every annotation, in tests too |
| `pylint` | `[tool.pylint]` in `pyproject.toml`, with `pylint-per-file-ignores`; over `src`, `tests`, `scripts`, and `hatch_build.py` | Docstrings, unused arguments, names, how large a function or class may grow, mistakes it can infer, and any line past 100 columns |
| `pytest` | Branch coverage, failing under 90% | Behavior |

Code is written for Python 3.11, the oldest version supported.

`src/` holds no `# type: ignore`, no `# noqa`, and no `# pragma: no cover`.
When a tool objects, the code changes, not the tool's view of it. A library
that ships no types gets a `[[tool.mypy.overrides]]` entry in
`pyproject.toml`, and a lint rule that does not fit one file is turned off
for that file in `.flake8`, each with a comment saying why.

### 2.1 Turning a pylint Message Off

`pylint` judges more than the other tools do, and is the one tool that may
be turned off in the code itself. When it objects, the code changes if the
change makes the code better. Where it would not, the message is turned off
as narrowly as it can be:

- **For the whole project**, in `disable` in `pyproject.toml`, where a check
  does not fit how this code is written: `too-few-public-methods`, since a
  `Protocol` often asks for one method (§5); `too-many-arguments`, since
  what is keyword-only is named in the call (§6.4); and `duplicate-code`,
  which cannot be turned off for the tests alone.
- **For a file or a directory**, in `per-file-ignores` there. The checks
  that tests are not held to are listed this way (§11).
- **For one line, function, or class**, with a comment that names the
  message, at the end of the line `pylint` reports:

  ```Python
  class ConnectionsModule(ModuleBase):  # pylint: disable=too-many-instance-attributes
  ```

  Where the line has no room for it, it goes on a line of its own: as
  `# pylint: disable-next=` above a statement, or as `# pylint: disable=`
  first in the body of a function.

Every entry in `pyproject.toml` has a comment saying why, and so does a
comment in the code, on the line above it:

```Python
# Kept open for reads until close() closes it.
self._archive = ZipFile(file)  # pylint: disable=consider-using-with
```

Two kinds need no reason given, because it is always the same one:
`broad-exception-caught` at a boundary (§9.2), and a limit on size
(`too-many-instance-attributes`, `too-many-locals`, and the like) that a
class or function has outgrown.

A disable that turns nothing off fails the run (`useless-suppression`), so
none outlives its reason.

## 3. Layout of a File

### 3.1 Order

A module reads top to bottom in this order:

1. The module docstring (§8.1).
2. Imports (§3.2).
3. `_LOGGER`, where the module logs (§9.3).
4. Constants (§7).
5. Public classes and functions.
6. Private helpers, named with a leading underscore, at the bottom.

Within a class, `__init__` comes first, then the public methods, then the
private ones.

### 3.2 Imports

Each symbol is imported by name from the module that defines it. A module is
not imported whole, imports are absolute, and none sit inside a function.

```Python
from __future__ import annotations
from dataclasses import dataclass
from json import dumps, loads
from logging import getLogger
from pathlib import Path
from typing import Any, Final, Iterable

from pydantic import BaseModel, ConfigDict, Field

from libranet.atomic_file import write_atomically
from libranet.bundle.errors import BundleError
from libranet.cas.content_id import ContentId
```

- `from __future__ import annotations` comes first in every module that has
  annotations, with the standard library following on the next line.
- Three groups, a blank line between each: standard library, third-party,
  `libranet`.
- Within a group, modules are in alphabetical order. Within one import, names
  are in `isort`'s order: `CONSTANTS`, then `Classes`, then `functions`.
- Code imports from the submodule (`libranet.cas.content_id`), never from the
  package (`libranet.cas`). A package's `__init__.py` re-exports its public
  names, with a sorted `__all__`, for users of the library.
- A name that would be unclear or would shadow a builtin where it is used is
  renamed as it is imported:

  ```Python
  from os import open as open_file
  from re import compile as compile_pattern
  from zlib import error as ZlibError
  ```

Two exceptions are in use. A module of loose functions may be imported as a
namespace (`from libranet import cli`, `from libranet.config import paths`).
And `supervisor.py` has `import sys`, because `sys.stderr` must be looked up
when it is used, not when the module loads; the comment above it says so.

### 3.3 Blank Lines

`black` sets the blank lines between definitions. Inside a function, these
are kept by hand:

- A compound statement (`if`, `for`, `while`, `try`, `with`) has a blank line
  before it and after it, unless it opens or closes its block.
- Each `elif`, `else`, `except`, and `finally` has a blank line before it.
- Simple statements that follow one another are not separated, and a `return`
  sits directly under the statement before it.
- No blank line comes between a docstring and the first statement.

```Python
def load_jobs(path: Path) -> dict[str, BackupJob]:
    """The jobs saved at ``path``, by id; none if nothing has been saved there.

    Raises:
        JobFileError: the file cannot be read, or does not hold backup jobs.
    """
    try:
        value = loads(path.read_bytes())

    except FileNotFoundError:
        # Not logged: there are no jobs until one is saved.
        return {}

    except (OSError, ValueError) as error:
        raise JobFileError(f"Cannot read backup jobs from {path}: {error}") from None

    jobs = value.get("jobs") if isinstance(value, dict) else None

    if not isinstance(jobs, list):
        raise JobFileError(f'{path} must hold an object with a "jobs" array')

    try:
        parsed = [BackupJob.from_value(job) for job in jobs]

    except ValueError as error:
        raise JobFileError(f"{path} holds an unusable backup job: {error}") from None

    return {job.job_id: job for job in parsed}
```

Tests differ in one way: blank lines also separate setting up, acting, and
asserting (§11).

### 3.4 Line Length

Code runs to 100 columns, which `black` enforces. Prose is wrapped by hand at
79 columns, in comments and in the body of a docstring. A docstring's first
line may run to 100 so that it stays one line.

`black` does not rewrap a docstring, a comment, or a long string, so
`pylint` is what holds those to 100 columns (`line-too-long`). `flake8`
catches them only once they pass 110: `B950` allows a line a tenth over the
limit, and `E501`, which allows nothing over, is turned off in its favor.
A `# pylint:` comment at the end of a line is not counted by `pylint`, but
is by `flake8`.

## 4. Names

PEP 8 casing throughout: `snake_case` for modules, functions, and variables,
`CapWords` for classes, `UPPER_CASE` for constants.

| Thing | Named | Examples |
| --- | --- | --- |
| Module | For what it holds, a noun | `content_id.py`, `peer_exchange.py` |
| A package's process | `module.py`, class `{Area}Module` | `validator/module.py`, `ValidatorModule` |
| A package's exceptions | `errors.py` | `cas/errors.py` |
| Exception | Ends in `Error` | `MalformedBundleError` |
| Protocol | For the role it plays | `ContentSource`, `Publish`, `StopSignal` |
| Function returning a value | For the value, with no `get_` | `ancestors()`, `nearest()`, `advertised_endpoint()` |
| Function that acts | A verb | `publish()`, `save_jobs()`, `write_atomically()` |
| Predicate | `is_…`, or a verb that reads as a question | `is_entry_path()`, `exists()`, `matches()`, `wants()` |
| Message handler | `_on_{event}` | `_on_data_stored` |
| Caught exception | `error` | `except OSError as error:` |
| Test | `test_` and a sentence | `test_no_file_means_no_jobs` |

- **Units go in the name** of anything that holds a quantity:
  `retry_after_seconds`, `max_object_bytes`, `bucket_prefix_bits`,
  `_MAX_EXPANDED_BYTES`. A name set outside the code keeps the spelling it
  has there: a payload or JSON key (`"size"`, `"retry_after"`), a database
  column, or a standard library parameter (`timeout`).
- **Private means a leading underscore**: module-level helpers, constants
  only one module uses, methods, and instance attributes. State is kept in
  `self._name` and shown, where it is needed, through a read-only
  `@property`. No property has a setter.
- **Words are spelled out**: `content_id`, `connection`, `message`,
  `request`, not `cid`, `conn`, `msg`, `req`.
- **A parameter that must be taken but is not used has a leading
  underscore**: `_message` in a handler that needs only to be told,
  `_config` in a factory that builds without it. `pylint` checks that every
  other parameter is used. Where the name is set elsewhere, by a `Protocol`
  or by the method being overridden, it is kept, and `unused-argument` is
  turned off for that function (§2.1).

A classmethod that builds its class is named for what it builds from:

| Name | Builds from |
| --- | --- |
| `from_value(value)` | A decoded JSON value (§6.3) |
| `from_row(row)` | A database row |
| `parse(text)` | A string form |
| `create(...)` | Parts that must be checked and normalized first |
| `of(config)`, `load(config)`, `open(...)` | The node's configuration or files |
| `for_data(...)`, `for_node(...)` | The thing it is made for |

## 5. Types

Every parameter, return value, and class member is annotated, in tests and
scripts as well. `mypy --strict` checks it, and a function that returns
nothing says `-> None`.

- **Built-in generics and `|`**: `list[str]`, `dict[str, Any]`,
  `tuple[str, ...]`, `Path | None`. `Optional`, `Union`, `List`, and `Dict`
  are not used.
- **Abstract in, concrete out.** A parameter takes the widest type that
  works (`Mapping`, `Sequence`, `Iterable`, `Callable`, all from `typing`); a
  return type says exactly what comes back (`dict`, `list`, `tuple`).
- **Immutable fields.** A frozen dataclass holds `tuple[...]`, `Mapping`, and
  `frozenset`, never `list`, `dict`, or `set`.
- **`object` for what has not been checked.** Decoded JSON and anything else
  from outside is typed `object` until `isinstance` has narrowed it, so
  `mypy` refuses code that trusts it (§10.1).
- **`Any` is for JSON that is already known**, as in `dict[str, Any]` for a
  message payload or a value about to be written, and for what a library
  hands over untyped.
- **No `cast`.** Narrow with `isinstance`, or restructure.
- **`Protocol` for an interface**, not an abstract base class. `ModuleBase`
  is the one `ABC`, because it carries the receive loop its subclasses share.
- **`StrEnum` for a string more than one module spells**, such as
  `EventType` and `ModuleName`.
- **`TypeAlias` for a union with a name**:
  `Entry: TypeAlias = FileBundle | Symlink | DirectoryMarker`.

## 6. Classes and Functions

### 6.1 Methods Over Functions

Behavior belongs on the type it is about. That includes factories: a loader
goes on the class it builds (`NodeIdentity.load(config)`,
`CasStore.source_of_truth(storage)`), not beside it as `load_identity()`.

A function stays a function when it does its own module's job with another
module's class, when it has no one type to belong to (`write_atomically`,
`nearest`), or when it is a small private reader such as
`_string(value, key)`. This is a preference, not an absolute.

### 6.2 Dataclasses

A value is a `@dataclass(frozen=True)`. A mutable dataclass is used only for
a module's own bookkeeping, and is private (`_Progress`, `_Peer`, `_Child`).
pydantic is used only in `config/`, for what an administrator writes by
hand; the settings models are frozen and refuse unknown keys.

### 6.3 A Type Checks Its Own Values

The rules on what a value may be are checked by its type, in
`__post_init__`, so that no invalid instance can exist however it was built.
A parser checks only that untrusted input has the right JSON types, then
constructs.

```Python
@dataclass(frozen=True)
class Symlink:
    """A symlink entry: a relative POSIX target, from the link's own location (§3.1).

    Raises:
        MalformedBundleError: ``target`` is empty, absolute, or holds a NUL.
    """

    target: str

    def __post_init__(self) -> None:
        if not self.target or self.target.startswith(PATH_SEPARATOR) or "\0" in self.target:
            raise MalformedBundleError(
                f"Symlink target must be a non-empty relative path: {self.target!r}"
            )
```

A type with a JSON form carries it in both directions: a
`from_value(value: object)` classmethod that checks the JSON types and
constructs, and a `value()` method that gives the JSON object back.

A classmethod such as `ContentId.create` does the checking instead only when
building must also normalize, which a frozen `__post_init__` cannot easily
do.

### 6.4 Parameters

- **Collaborators are passed in**, through the constructor. A class does not
  reach for a global, which is what lets a module be tested with plain
  `queue.Queue` objects.
- **The clock is a parameter**: `clock: Callable[[], float] = time`. Code
  that stamps or compares wall-clock time calls `self._clock()`, so a test
  can set the time. `monotonic()` is called directly for deadlines.
- **No mutable default arguments.**
- **Optional collaborators and tuning are keyword-only**, after a `*`:

  ```Python
  def __init__(
      self,
      name: ModuleName,
      queues: ModuleQueues,
      *,
      logger: Logger | None = None,
      clock: Callable[[], float] = time,
      poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
  ) -> None:
  ```

- **No more than five parameters are positional**, not counting `self` or
  `cls`, which `pylint` checks. Any more go after the `*`, where a call has
  to name them: `back_up(directory, latest, store, secret, made_at,
  settings=settings)`.
- **A constructor refuses arguments it cannot work with** by raising
  `ValueError` before it stores anything.

### 6.5 Shared State

State that more than one thread touches is guarded by a lock, taken with
`with self._lock:`. A file the node may be reading is never written in
place: it goes through `write_atomically` or `atomic_writer`.

## 7. Constants

```Python
# The files are this node's own, and a million-file directory's run to
# hundreds of megabytes, so the only limit is one no directory reaches.
_MAX_EXPANDED_BYTES: Final = 1 << 40
```

- A constant is declared `Final`, with the type left to be inferred unless
  inference would make it too narrow (`Final[Mapping[str, str]]`).
- The comment above it says why it has that value, or what it means, and
  names the specification section that sets it.
- A number or string with a meaning gets a name, rather than appearing bare
  in a function body.
- A size is written as arithmetic that shows its unit: `1 << 40`,
  `64 * 1024`, `4 * MIB`.
- A constant only one module uses is private. One that another module also
  needs is defined once, public, in the lowest module of the layer its
  meaning belongs to, and imported from there even where the literal would
  be shorter. A fact no layer owns gets a small top-level module, as
  `json_format.py` is. Two constants that merely share a value stay apart.
  ([Module System](Module%20System.md) §5.3.)

## 8. Docstrings and Comments

### 8.1 Docstrings

Every module, every class, and every public function and method has a
docstring, which `pylint` checks. Most private helpers do too. Dunder
methods do not, nor does a method that only implements a documented
`Protocol`, nor does a test. The `Protocol` documents each method it asks
for, and a class whose methods only implement it turns
`missing-function-docstring` off for itself, with a comment naming the
`Protocol` (§2.1).

```Python
@classmethod
def create(
    cls, algorithm: str, hash_value: str, registry: AlgorithmRegistry = DEFAULT_REGISTRY
) -> ContentId:
    """Validate and normalize an algorithm name and hash.

    Raises:
        UnknownAlgorithmError: ``algorithm`` is not registered.
        InvalidContentIdError: ``hash_value`` is not hex of the right length.
    """
```

- **Shape**: the summary sits on the line of the opening quotes and ends in
  a full stop; a blank line follows; the closing quotes have a line to
  themselves.
- **The summary names what comes back**, as a noun phrase, when the function
  returns a value: "The jobs saved at ``path``, by id", "Whether ``data``
  hashes to this identifier", "Every directory above one of the entry
  paths". A function that acts gets a verb: "Replace what ``path`` holds
  with ``jobs``".
- **Parameters are named in the prose**, in double backticks. There is no
  `Args:` list.
- **`Returns:` says what comes back when the summary does not**: where the
  summary is about what the function does, as in "Replace ``path`` with
  ``body`` unless it already holds exactly that", or where the result takes
  more than a clause to describe. A summary that names the result needs
  none: "The jobs saved at ``path``", "Store ``data`` under ``content_id``
  and return its path".
- **`Raises:` lists every exception a caller should expect**, each with the
  condition that raises it, starting in lower case.
- **Other code is named with a role**: ``:class:`ContentId` ``,
  ``:meth:`create` ``, ``:mod:`libranet.bundle.parsing` ``. Literals are in
  double backticks.
- **A module docstring says what the module is for and why it is built as it
  is**, and names the specification section or plan step it implements
  ("HttpApi §5.4", "Phase 2 Step 48"). A module that publishes messages or
  writes a file shows the payload or file format in a literal block.

### 8.2 Comments

A comment says why, not what: the reason for a value, the case a branch
covers, the thing that would break if the line changed. Comments are whole
sentences, wrapped at 79 columns, above the code they are about.

```Python
# The verified bytes are written rather than the upload file moved:
# a new upload from the same node may replace that file at any time.
self._source_of_truth.write(content_id, data)
```

There are no `TODO` or `FIXME` comments. Work left over is recorded in the
phase plan or an issue.

## 9. Errors and Logging

### 9.1 Raising

- **Each package has its own exceptions**, in `errors.py`, under one base
  (`CasError`, `BundleError`, `IdentityError`) where callers need to catch
  the whole family. They derive from the built-in that fits: `ValueError`
  for a bad value, `ConnectionError` for a failed connection, `OSError` for
  a failed file.
- **A message is an f-string with no full stop**, saying what was expected
  and then what was given:
  `f"retry_after_seconds must not be negative, got {retry_after_seconds}"`.
  It starts with a capital unless it starts with a parameter or field name.
- **A value from outside is shown with `!r`**, so that an empty or odd
  string is visible.
- **An exception raised in place of a caught one says so**, with `from None`
  or `from error`. Most say `from None`, and put the cause's text in the new
  message.
- **`assert` is for tests.** Source code raises.

### 9.2 Catching

Catch the narrowest exception that can happen, and name it `error`.
`except Exception` is only for a boundary that must survive whatever it runs:
a module's receive loop, a worker thread, a request handler. Each is marked
`# pylint: disable=broad-exception-caught`, so that a new one is a decision
and not an accident. `except BaseException` is only for cleaning up and
raising again.

Every handler does one of three things, and
`tests/test_exception_logging.py` fails on one that does none:

1. It logs.
2. It ends in `raise`.
3. It has a comment that starts `# Not logged:` and gives the reason.

`# Not logged:` is for an exception that is how the code asks a question (a
queue with nothing in it, a file that may not be there), or one handed to
code that logs it. It is never for skipping data that is not what it should
be.

### 9.3 Logging

A module process logs with `self.logger`. Code with no logger at hand uses a
module-level `_LOGGER = getLogger(__name__)`. `print` is for the command
line only: the supervisor's startup errors and `scripts/`.

```Python
_LOGGER.warning("Cannot read %s, so the last backup is read back: %s", path, error)
```

- **Arguments are passed, not formatted in**: `%s` and arguments, never an
  f-string.
- **A message starts with a capital and has no full stop.** Where something
  was given up on, it says what happens instead.
- **The level follows what became of the failure:**

| Level | When |
| --- | --- |
| `debug` | It became a `4xx`, `404`, or `503` response, or a value the caller reports; or a client or peer sent bad data and it was refused |
| `info` | Normal progress: started, connected, stored |
| `warning` | This node's own data is not what it should be, or something failed and the node carried on |
| `error` | Something had to be stopped or left out: the node cannot start, a module had to be killed, a value is of no known kind |
| `exception` | Inside `except Exception`, to keep the traceback |

Data that is not what it should be is always logged. An id under a hash
algorithm this node does not support is a warning wherever it is met, since
the node may need an update, and a list or archive logs it once for all its
entries rather than once each.

## 10. Defensive Checks

### 10.1 Untrusted Input

Input from a peer, a client, or a file is checked type by type before it is
used. A JSON integer check also refuses `bool`, which Python counts as an
`int`:

```Python
if not isinstance(skipped, int) or isinstance(skipped, bool):
    raise ValueError('"skipped" must be an integer')
```

### 10.2 Every Kind Is Checked

Code that branches on which kind a value is tests each kind with its own
`isinstance`. The final `else` logs an error and leaves the value out or
raises; it never takes "none of the others" to mean the last kind. Because
`mypy` would call that `else` unreachable, the local is declared `object`,
with a comment saying why:

```Python
# Looked at as whatever it is, so that a kind of entry this does not
# know is logged and left out, rather than taken for another.
entry: object = pending[path]

if isinstance(entry, Symlink):
    ...

elif isinstance(entry, FileBundle):
    ...

elif isinstance(entry, DirectoryMarker):
    ...

else:
    kind = type(entry).__name__
    _LOGGER.error(
        "Cannot restore %s into %s, as a %s is not a file, a symlink, or a directory",
        path,
        self._request.directory,
        kind,
    )
    raise UnsupportedBundleError(f"Not a file, a symlink, or a directory: {kind}")
```

### 10.3 Stop Conditions Are Inclusive

A check that ends a loop or a count compares with `>=` or `<=`, not `==`, so
that a value already past the limit still stops it:
`if search.pass_number >= search.passes:`. Validation can be bypassed, and
`==` would then never be true.

## 11. Tests

- **One test file per source file**: `src/libranet/{area}/{file}.py` is
  tested by `tests/test_{area}_{file}.py`. A test of something that cuts
  across files is named for it (`test_hash_case.py`,
  `test_exception_logging.py`).
- **Tests are plain functions**, not classes, fully annotated, returning
  `None`.
- **A test's name is a sentence saying what is true**:
  `test_a_file_that_cannot_be_read_is_an_error`,
  `test_jobs_are_saved_in_order_of_directory`. It takes the place of a
  docstring. The file's docstring starts "Tests for …".
- **pytest names are imported**: `from pytest import fixture, mark, raises`,
  then `@fixture`, `@mark.parametrize`, `with raises(...)`.
- **Set up, act, assert**, with a blank line between each.
- **Many inputs, one behavior**: `@mark.parametrize`, not a loop or copies.
- **No `unittest.mock`.** A stand-in is a small class written in the test
  file (`Recorder`, `FakeLoader`, `FailingStore`), and a function is swapped
  with `MonkeyPatch`.
- **Real files, in `tmp_path`.** Time is set by passing a `clock`, and waits
  are kept short by passing a small `poll_interval_seconds`.
- **That there is exactly one of something is asserted by unpacking it**:
  `(message,) = published(queues)`.
- **A fixture wanted only for what it sets up is named in
  `@mark.usefixtures`**, not taken as a parameter the test never reads. A
  test asks for no fixture it does not need.
- **Shared data is a module-level constant**, in upper case; helpers and
  fixtures come before the tests. Long numbers are grouped:
  `1_789_000_000.5`.
- **A module is tested without starting a process**: build it with
  `queue.Queue` objects, call `handle()` with a message, and read its
  outbox. ([Module System](Module%20System.md) §11.)

`pylint` reads the tests too, less five checks that the rules above set
aside, turned off for `tests/` in `pyproject.toml` (§2.1):

| Check | Why tests are not held to it |
| --- | --- |
| `missing-function-docstring` | A test's name is its docstring, and a helper's says enough |
| `redefined-outer-name` | A fixture is asked for by naming it as a parameter |
| `too-many-positional-arguments` | A test may ask for many fixtures |
| `unbalanced-tuple-unpacking` | Unpacking asserts that there is exactly one, from a list `pylint` takes to be empty |
| `use-implicit-booleaness-not-comparison` | `== []` checks the type of what came back, which `not` would not |

New code comes with tests, and coverage is gated at 90%.

## 12. Where the Code Departs

These are in the code as of this version. New code follows the rules above.
None of these is fixed in passing: a change set keeps to its task.

| Where | Departure |
| --- | --- |
| `tests/test_config_models.py` | Three `Test…` classes (§11) |
| `logging_setup.get_logger` | A `get_` name (§4) |
