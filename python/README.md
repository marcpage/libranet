# Libranet in Python

The reference node of [Libranet](../README.md), written in Python. It runs
on macOS and Linux, and needs Python 3.11 or newer and
[uv](https://docs.astral.sh/uv/). What every node must do is in the
specifications, in [`docs/specs/`](../docs/specs/). This directory holds the
Python node: its code, its tests, and the documents and plans about it.

---

## Running a Node

From the top of the repository, or from this directory:

```bash
uv sync
uv run libranet
```

The [Operator Guide](docs/Operator%20Guide.md) covers installing and
configuring a node, the administration page, backups, applications, and
running test networks and super nodes. The command line and the
configuration file it describes are this node's own; other implementations
are not bound by them.

---

## Development

The tools keep their settings in this directory's `pyproject.toml`, so run
them from here, or from the top of the repository with
`uv run --directory python`.

```bash
cd python
uv sync                  # install the package and dev tools
uv run pytest            # run the test suite
uv run pytest --cov      # with coverage (the build fails under 90%)
uv run black .           # format (line length 100)
uv run flake8            # lint
uv run mypy              # type-check (strict)
uv run pylint src tests hatch_build.py   # lint further
```

Before opening a pull request, please make sure `black`, `flake8`, `mypy`,
`pylint`, and `pytest` all pass. CI runs the lint and type checks once, and
the test suite on Ubuntu and macOS against Python 3.11 and 3.14. It also
verifies that [`examples/libranet.yaml`](examples/libranet.yaml) still
loads.

The layout is one package per module area under
[`src/libranet/`](src/libranet/), with a matching
`tests/test_<area>_<file>.py` for each source file. New code is expected to
come with tests; coverage is gated at 90%.

The applications the node ships, and the seed list it starts from, are
shared by every implementation, so they are kept at the top of the
repository, in [`applications/`](../applications/) and [`data/`](../data/).
A node run from a checkout reads them there, and a wheel carries them:
[`hatch_build.py`](hatch_build.py) builds the applications into it.

The documents about this node, and its implementation plans, are listed in
the [top-level README](../README.md#the-python-node).
