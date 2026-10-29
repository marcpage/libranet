# Python Coding Style

Version 0.2 • October 2026

---

## 1. Purpose

This document describes how Python is written in a project that follows it:
how a file is laid out, how things are named and typed, how errors are raised
and logged, and how tests are written. It names nothing in any one project,
so that it can be carried to another. The source is in `src/` and the tests
are in `tests/`; the project's own package is called `example` here.

A rule here is one of three kinds:

- **Checked by a tool**: `black`, `flake8`, `mypy`, or `pylint` fails the
  build (§2).
- **Checked by a test**: a test in `tests/` fails.
- **Convention**: nothing checks it; it is kept by hand and in review.

A rule is a convention unless its section says what checks it. Where no rule
covers a case, match the code around it.

What is particular to a project is kept in a document of the project's own:
the helpers and tests these rules lean on, and the places where its code
departs from them. New code follows the rules here. A departure is not fixed
in passing: a change set keeps to its task.

## 2. What the Tools Check

```bash
uv run black .           # format
uv run flake8            # lint
uv run mypy              # type-check
uv run pylint src tests  # lint further
uv run pytest --cov      # tests, with coverage
```

CI runs all five on every pull request, and the tests on each operating
system the project supports, against the oldest and the newest Python it
supports.

| Tool | Settings | What it holds to |
| --- | --- | --- |
| `black` | Line length 100, target the oldest Python supported | Layout of code: quotes, wrapping, trailing commas |
| `flake8` | `.flake8`, with `flake8-bugbear`; `F401` allowed in a package's `__init__.py` | Unused names, undefined names, PEP 8 spacing, likely bugs, and any line past 110 columns (`B950`) |
| `mypy` | `strict`, `warn_unreachable`, over `src` and `tests` | Every annotation, in tests too |
| `pylint` | `[tool.pylint]` in `pyproject.toml`, with `pylint-per-file-ignores`; over `src`, `tests`, and any other Python the build runs | Docstrings, unused arguments, names, how large a function or class may grow, mistakes it can infer, and any line past 100 columns |
| `pytest` | Branch coverage, failing under 90% | Behavior |

Code is written for the oldest Python the project supports, which is 3.11 or
later: the rules below use `StrEnum` and `X | None`.

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
  class Scheduler(Worker):  # pylint: disable=too-many-instance-attributes
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
class or function has outgrown. Each limit is `pylint`'s own but one: a
class may keep ten attributes, not seven, since a value has a field for
each member of what it stands for (§6.2), and a class keeps each
collaborator it is handed (§6.4).

A disable that turns nothing off fails the run (`useless-suppression`), so
none outlives its reason.

## 3. Layout of a File

### 3.1 Order

A module reads top to bottom in this order:

1. The module docstring (§8.1).
2. Imports (§3.2).
3. `_LOGGER`, where the module logs (§9.3).
4. Constants (§7).
5. Private classes the public ones use: a base class has to come first, and
   the bookkeeping a public class keeps (`_Pending`, `_Attempt`) is read
   before the class that keeps it.
6. Public classes and functions.
7. Private helper functions, named with a leading underscore, at the bottom.

Within a class, `__init__` comes first, then the public methods, then the
private ones. A settings model's validators are the exception: each follows
the fields it checks, before the model's other methods.

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

from example.paths import data_directory
from example.storage.errors import StorageError
from example.storage.record_id import RecordId
```

- `from __future__ import annotations` comes first in every module that has
  annotations, with the standard library following on the next line.
- Three groups, a blank line between each: standard library, third-party,
  the project's own.
- Within a group, modules are in alphabetical order. Within one import, names
  are in `isort`'s order: `CONSTANTS`, then `Classes`, then `functions`.
- Code imports from the submodule (`example.storage.record_id`), never from
  the package (`example.storage`). A package's `__init__.py` re-exports its
  public names, with a sorted `__all__`, for users of the library, and a
  test checks that it does. A module left out has the reason in the
  package's `__init__.py`.
- A name that would be unclear or would shadow a builtin where it is used is
  renamed as it is imported:

  ```Python
  from os import open as open_file
  from re import compile as compile_pattern
  from zlib import error as ZlibError
  ```

Two exceptions are allowed. A module of loose functions may be imported as a
namespace (`from example import cli`). And a module is imported whole where
one of its attributes must be looked up when it is used, not when the module
loads (`sys.stderr`), or is assigned to, as a hook for uncaught exceptions is
(`sys.excepthook`, `threading.excepthook`). The comment above the import says
which.

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
@classmethod
def load_all(cls, path: Path) -> dict[str, Task]:
    """The tasks saved at ``path``, by id; none if nothing has been saved there.

    Raises:
        TaskFileError: the file cannot be read, or does not hold tasks.
    """
    try:
        value = loads(path.read_bytes())

    except FileNotFoundError:
        # Not logged: there are no tasks until one is saved.
        return {}

    except (OSError, ValueError) as error:
        raise TaskFileError(f"Cannot read tasks from {path}: {error}") from None

    tasks = value.get("tasks") if isinstance(value, dict) else None

    if not isinstance(tasks, list):
        raise TaskFileError(f'{path} must hold an object with a "tasks" array')

    try:
        parsed = [cls.from_value(task) for task in tasks]

    except ValueError as error:
        raise TaskFileError(f"{path} holds an unusable task: {error}") from None

    return {task.task_id: task for task in parsed}
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
| Module | For what it holds, a noun | `record_id.py`, `retry_policy.py` |
| A package's exceptions | `errors.py` | `storage/errors.py` |
| Exception | Ends in `Error` | `MalformedRecordError` |
| Protocol | For the role it plays | `RecordSource`, `Notify`, `StopSignal` |
| Function returning a value | For the value, with no `get_` | `ancestors()`, `nearest()`, `total_bytes()` |
| Function that acts | A verb | `publish()`, `save_all()`, `send_reply()` |
| Predicate | `is_…`, or a verb that reads as a question | `is_hidden()`, `exists()`, `matches()`, `wants()` |
| Message handler | `_on_{event}` | `_on_file_saved` |
| Caught exception | `error` | `except OSError as error:` |
| Test | `test_` and a sentence | `test_no_file_means_no_tasks` |

- **Units go in the name** of anything that holds a quantity:
  `timeout_seconds`, `max_upload_bytes`, `prefix_bits`,
  `_MAX_REPLY_BYTES`. A name set outside the code keeps the spelling it
  has there: a payload or JSON key (`"size"`, `"retry_after"`), a database
  column, or a standard library parameter (`timeout`).
- **Private means a leading underscore**: module-level helpers, constants
  only one module uses, methods, and instance attributes. State is kept in
  `self._name` and shown, where it is needed, through a read-only
  `@property`. No property has a setter.
- **Words are spelled out**: `record_id`, `connection`, `message`,
  `request`, not `rid`, `conn`, `msg`, `req`.
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
| `of(config)`, `load(config)`, `open(...)` | The program's configuration or files |
| `for_data(...)`, `for_user(...)` | The thing it is made for |

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
- **`Protocol` for an interface**, not an abstract base class. No class
  derives from `ABC`. A plain base class is used only where it carries
  behavior its subclasses share, such as a receive loop, and its hooks do
  nothing until a subclass overrides them.
- **`StrEnum` for a string more than one module spells**, such as the names
  of the events a program sends.
- **`TypeAlias` for a union with a name**:
  `Entry: TypeAlias = File | Link | Directory`.

## 6. Classes and Functions

### 6.1 Methods Over Functions

Behavior belongs on the type it is about. That includes factories: a loader
goes on the class it builds (`Settings.load(path)`,
`Store.open(directory)`), not beside it as `load_settings()`.

A function stays a function when it does its own module's job with another
module's class, when it has no one type to belong to (writing a file
atomically, finding the nearest of several values), or when it is a small
private reader such as `_strings(value, name)`. This is a preference, not an
absolute.

### 6.2 Dataclasses

A value is a `@dataclass(frozen=True)`. So is an HTTP handler or guard: a
frozen dataclass of the collaborators it is given, called once per request.
A collaborator that keeps state of its own, made for the handler alone, is
made by the handler's factory classmethod and given to it like any other.
A mutable dataclass is used only for a module's own bookkeeping, and is
private (`_Pending`, `_Attempt`).
pydantic is used only for settings, what an administrator writes by hand;
the settings models are frozen and refuse unknown keys.

### 6.3 A Type Checks Its Own Values

The rules on what a value may be are checked by its type, in
`__post_init__`, so that no invalid instance can exist however it was built.
A parser checks only that untrusted input has the right JSON types, then
constructs.

```Python
@dataclass(frozen=True)
class Link:
    """A symbolic link: a relative POSIX target, from the link's own location.

    Raises:
        MalformedEntryError: ``target`` is empty, absolute, or holds a NUL.
    """

    target: str

    def __post_init__(self) -> None:
        if not self.target or self.target.startswith(SEPARATOR) or "\0" in self.target:
            raise MalformedEntryError(
                f"Link target must be a non-empty relative path: {self.target!r}"
            )
```

A type with a JSON form carries it in both directions: a
`from_value(value: object)` classmethod that checks the JSON types and
constructs, and a `value()` method that gives the JSON object back.

A classmethod such as `create` does the checking instead only when building
must also normalize, which a frozen `__post_init__` cannot easily do.

### 6.4 Parameters

- **Collaborators are passed in**, through the constructor. A class does not
  reach for a global, which is what lets a class that talks through queues
  be tested with plain `queue.Queue` objects.
- **The clock is a parameter**: `clock: Callable[[], float] = time`. Code
  that stamps or compares wall-clock time calls `self._clock()`, so a test
  can set the time. `monotonic()` is called directly for deadlines, unless
  a test must move one: then it is the clock's default,
  `clock: Callable[[], float] = monotonic`, as for how long a session has
  gone unused or a search has run.
- **No mutable default arguments.**
- **Optional collaborators and tuning are keyword-only**, after a `*`:

  ```Python
  def __init__(
      self,
      name: str,
      inbox: Queue[Message],
      *,
      logger: Logger | None = None,
      clock: Callable[[], float] = time,
      poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
  ) -> None:
  ```

  A method that overrides or stands in for a standard-library one keeps that
  one's signature, as an override of `BaseHTTPRequestHandler.send_error` or
  a stand-in for `Queue.get` does, but for a parameter named for a builtin.
  That one is renamed and made positional-only, since the standard library
  passes it by position: an override of `BaseHTTPRequestHandler.log_message`,
  whose parameter is `format`, takes `(self, template, /, *args)`.
  `redefined-builtin` is never turned off.

- **No more than five parameters are positional**, not counting `self` or
  `cls`, which `pylint` checks. Any more go after the `*`, where a call has
  to name them: `copy_tree(source, target, store, key, started_at,
  settings=settings)`.
- **A constructor refuses arguments it cannot work with** by raising
  `ValueError` before it stores anything.

### 6.5 Shared State

State that more than one thread touches is guarded by a lock, taken with
`with self._lock:`. A file the program may be reading is never written in
place: it is written to a temporary file beside it, which then replaces it.

## 7. Constants

```Python
# A reply larger than this is taken for a server gone wrong, since the
# largest the protocol sends is a page of a thousand short entries.
_MAX_REPLY_BYTES: Final = 4 * MIB
```

- A constant is declared `Final`, with the type left to be inferred unless
  inference would make it too narrow (`Final[Mapping[str, str]]`).
- The comment above it says why it has that value, or what it means, and
  names the specification section that sets it.
- Above a public constant or type alias, that comment is a Sphinx doc
  comment: every line of it starts `#:`, so that Sphinx's `autodoc`
  documents the name with it. Above a private one, it starts `#`, as any
  other comment does (§8.2).

  ```Python
  #: The fewest leading bits an id shares with the target it is placed at
  #: (Protocol §3.2).
  MIN_MATCHING_BITS: Final = 16
  ```

- A number or string with a meaning gets a name, rather than appearing bare
  in a function body.
- A size is written as arithmetic that shows its unit: `1 << 40`,
  `64 * 1024`, `4 * MIB`. So is a duration of an hour or more, with
  constants such as `HOUR_SECONDS` and `DAY_SECONDS`: `30 * DAY_SECONDS`.
- A constant only one module uses is private. One that another module also
  needs is defined once, public, in the lowest module of the layer its
  meaning belongs to, and imported from there even where the literal would
  be shorter. A fact no layer owns gets a small top-level module of its own.
  Two constants that merely share a value stay apart.

## 8. Docstrings and Comments

### 8.1 Docstrings

Every module, every class, and every public function and method has a
docstring, which `pylint` checks. Most private helpers do too. Dunder
methods do not, nor does a test. A method that implements a `Protocol` has
its own, saying what this implementation gives or does where the
`Protocol` says only what is asked for: `missing-function-docstring` is
never turned off in `src/`.

```Python
@classmethod
def create(cls, algorithm: str, digest: str, registry: Registry = DEFAULT_REGISTRY) -> RecordId:
    """Validate and normalize an algorithm name and digest.

    Raises:
        UnknownAlgorithmError: ``algorithm`` is not registered.
        InvalidRecordIdError: ``digest`` is not hex of the right length.
    """
```

- **Shape**: the summary sits on the line of the opening quotes and ends in
  a full stop; a blank line follows; the closing quotes have a line to
  themselves.
- **The summary names what comes back**, as a noun phrase, when the function
  returns a value: "The tasks saved at ``path``, by id", "Whether ``data``
  hashes to this identifier", "Every directory above one of the entry
  paths". A function that acts gets a verb: "Replace what ``path`` holds
  with ``tasks``".
- **Parameters are named in the prose**, in double backticks. There is no
  `Args:` list.
- **`Returns:` says what comes back when the summary does not**: where the
  summary is about what the function does, as in "Replace ``path`` with
  ``body`` unless it already holds exactly that", or where the result takes
  more than a clause to describe. A summary that names the result needs
  none: "The tasks saved at ``path``", "Store ``data`` under ``record_id``
  and return its path".
- **`Raises:` lists every exception a caller should expect**, each with the
  condition that raises it, starting in lower case.
- **Other code is named with a role**: ``:class:`RecordId` ``,
  ``:meth:`create` ``, ``:mod:`example.storage.parsing` ``. Literals are in
  double backticks.
- **A module docstring says what the module is for and why it is built as it
  is**, and names the specification section or plan step it implements,
  where there is one ("Protocol §5.4", "Step 48"). A module that publishes
  messages or writes a file shows the payload or file format in a literal
  block.

### 8.2 Comments

A comment says why, not what: the reason for a value, the case a branch
covers, the thing that would break if the line changed. Comments are whole
sentences, wrapped at 79 columns, above the code they are about.

Comments are formatted for Sphinx, as docstrings are (§8.1), in
reStructuredText: a literal in double backticks (` ``reached`` `), other
code named with a role (``:meth:`shutdown` ``), and never Markdown's single
backticks. A comment that documents a public constant is a doc comment,
each line starting `#:` (§7).

```Python
# Sorted, so that the same entries always hash alike, whatever order the
# directory listed them in.
names = sorted(entry.name for entry in entries)
```

There are no `TODO` or `FIXME` comments. Work left over is recorded in the
project's plan or an issue.

## 9. Errors and Logging

### 9.1 Raising

- **Each package has its own exceptions**, in `errors.py`, under one base
  (`StorageError`, `ParseError`) where callers need to catch the whole
  family. They derive from the built-in that fits: `ValueError` for a bad
  value, `ConnectionError` for a failed connection, `OSError` for a failed
  file.
- **A message is an f-string with no full stop**, saying what was expected
  and then what was given:
  `f"timeout_seconds must not be negative, got {timeout_seconds}"`.
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
a receive loop, a worker thread's loop, a request handler, a task whose
failure must be recorded, a callback that a library calls and would otherwise
log for itself. A boundary catches what it is known to meet by name first, at
the level §9.3 gives it, and leaves `except Exception` to what no one
expected, which it logs with its traceback. Each `except Exception` is marked
`# pylint: disable=broad-exception-caught`, so that a new one is a decision
and not an accident. One that ends by raising, as a process does to exit once
it has logged its crash, has no mark: `pylint` does not report it, and
refuses a mark that turns nothing off (§2.1). `except BaseException` is only
for cleaning up and raising again.

Every handler does one of three things, and a test that reads every handler
in `src/` fails on one that does none:

1. It logs.
2. It ends in `raise`.
3. It has a comment that starts `# Not logged:` and gives the reason.

`# Not logged:` is for an exception that is how the code asks a question (a
queue with nothing in it, a file that may not be there), or one handed to
code that logs it. It is never for skipping data that is not what it should
be.

An exception that nothing catches, and that ends a thread or the process, is
logged at error, with its traceback, by hooks the logging setup installs in
every process (`sys.excepthook`, `threading.excepthook`), rather than printed
to stderr by Python's own. So a thread that would do no more than log what
ends it has no catch of its own, and puts what it must do either way in
`finally`. Its name says what it works on, as `send {host}` does, so that
the hook's message says it too.

### 9.3 Logging

A class that is handed a logger logs with it. Code with no logger at hand
uses a module-level `_LOGGER = getLogger(__name__)`. `print` is for the
command line only: a program's startup errors, and the output of a
command-line tool.

```Python
_LOGGER.warning("Cannot read %s, so the last good copy is read instead: %s", path, error)
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
| `warning` | The program's own data is not what it should be, or something failed and the program carried on |
| `error` | Something had to be stopped or left out: the program cannot start, a process had to be killed, a value is of no known kind |
| `exception` | Inside `except Exception`, to keep the traceback |

Signed content that does not check, or that someone other than the one
expected signed, is what a client or peer sent, since anyone may place it:
debug. What the program holds that is not what its id names is a warning.

A request refused for where it came from, such as another site's page, a
`Host` another site may have pointed at this machine, or a page of another
application, is a warning, though it is a `4xx`, since it may be an attack.

Data that is not what it should be is always logged. Data in a form the
program does not support, such as an id under a hash algorithm or content
under an encryption scheme it lacks, is a warning wherever it is met, since
the program may need an update, and a list or archive logs it once for all
its entries rather than once each.

## 10. Defensive Checks

### 10.1 Untrusted Input

Input from a peer, a client, or a file is checked type by type before it is
used. A JSON integer or number is checked with a helper that also refuses
`bool`, since Python counts it as an `int`. A field that must be there is
read with a helper that raises the error it is given:

```Python
skipped = integer_field(value, "skipped")
seconds = number_field(value, "seconds", error=InvalidRequestError)
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

if isinstance(entry, Link):
    ...

elif isinstance(entry, File):
    ...

elif isinstance(entry, Directory):
    ...

else:
    kind = type(entry).__name__
    _LOGGER.error(
        "Cannot restore %s into %s, as a %s is not a file, a link, or a directory",
        path,
        self._target,
        kind,
    )
    raise UnsupportedEntryError(f"Not a file, a link, or a directory: {kind}")
```

### 10.3 Stop Conditions Are Inclusive

A check that ends a loop or a count compares with `>=` or `<=`, not `==`, so
that a value already past the limit still stops it:
`if search.pass_number >= search.passes:`. Validation can be bypassed, and
`==` would then never be true.

## 11. Tests

- **One test file per source file**: `src/example/{area}/{file}.py` is
  tested by `tests/test_{area}_{file}.py`. A test of something that cuts
  across files is named for it, as a test of the rule in §9.2 might be
  `test_exception_logging.py`. A module that only defines exceptions,
  enums, protocols, constants, or types has none of its own, and is tested
  where it is used.
- **Tests are plain functions**, not classes, fully annotated, returning
  `None`.
- **A test's name is a sentence saying what is true**:
  `test_a_file_that_cannot_be_read_is_an_error`,
  `test_tasks_are_saved_in_order_of_directory`. It takes the place of a
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
- **A class that runs in a process of its own is tested without starting
  one**: build it with `queue.Queue` objects, hand it a message, and read
  what it sent.

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
