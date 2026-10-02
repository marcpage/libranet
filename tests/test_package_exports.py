"""Every package re-exports the public names its modules define (Coding Style §3.2).

So a user of the library finds each one in the package, as its ``__all__``
lists it. :mod:`libranet.cas.layered` is the one module left out, as the
package's docstring explains.
"""

from __future__ import annotations
from ast import AnnAssign, Assign, ClassDef, FunctionDef, Name, parse
from importlib import import_module
from pathlib import Path

from pytest import mark

SOURCE = Path(__file__).resolve().parents[1] / "src" / "libranet"

#: Modules whose names their package does not export, each saying why.
NOT_EXPORTED = frozenset({"cas.layered"})

PACKAGES = sorted(init.parent.name for init in SOURCE.glob("*/__init__.py"))


def defined_names(source: str) -> set[str]:
    """The public names ``source`` defines at its top level."""
    names: set[str] = set()

    for node in parse(source).body:
        if isinstance(node, (ClassDef, FunctionDef)):
            names.add(node.name)

        elif isinstance(node, AnnAssign) and isinstance(node.target, Name):
            names.add(node.target.id)

        elif isinstance(node, Assign):
            names.update(target.id for target in node.targets if isinstance(target, Name))

    return {name for name in names if not name.startswith("_")}


@mark.parametrize("package", PACKAGES)
def test_a_package_exports_every_public_name_its_modules_define(package: str) -> None:
    exported = set(import_module(f"libranet.{package}").__all__)

    missing = [
        f"{module.stem}.{name}"
        for module in sorted((SOURCE / package).glob("*.py"))
        if module.name != "__init__.py" and f"{package}.{module.stem}" not in NOT_EXPORTED
        for name in sorted(defined_names(module.read_text(encoding="utf-8")) - exported)
    ]

    assert missing == []


@mark.parametrize("package", PACKAGES)
def test_a_package_exports_only_names_it_holds(package: str) -> None:
    imported = import_module(f"libranet.{package}")

    assert [name for name in imported.__all__ if not hasattr(imported, name)] == []


def test_the_public_top_level_definitions_are_found() -> None:
    source = (
        "from os import path\n"
        "LIMIT: Final = 1\n"
        "Alias = int\n"
        "_PRIVATE = 2\n"
        "class Thing:\n"
        "    INSIDE = 3\n"
        "def make() -> None: ...\n"
        "def _helper() -> None: ...\n"
    )

    assert defined_names(source) == {"LIMIT", "Alias", "Thing", "make"}
