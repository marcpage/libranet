"""No module package imports from another (Module System §2).

Modules never call each other, and none holds another's code: what two of
them share is in :mod:`libranet.protocol`, :mod:`libranet.messaging`, or a
library package below them all, such as :mod:`libranet.cas`. So each piece
has one home, and a module's process loads no other module's code.
"""

from __future__ import annotations
from ast import Import, ImportFrom, parse, walk
from pathlib import Path

from pytest import mark

from libranet.modules import ModuleName

SOURCE = Path(__file__).resolve().parents[1] / "src" / "libranet"

#: Every process with a package of its own: all but the supervisor and dispatcher.
MODULE_PACKAGES = sorted(name.value for name in ModuleName if (SOURCE / name.value).is_dir())


def imported_packages(source: str) -> set[str]:
    """The ``libranet`` packages ``source`` imports from, by name."""
    found: set[str] = set()

    for node in walk(parse(source)):
        if isinstance(node, ImportFrom) and node.module is not None:
            names = [node.module]

        elif isinstance(node, Import):
            names = [alias.name for alias in node.names]

        else:
            continue

        found.update(name.split(".")[1] for name in names if name.startswith("libranet."))

    return found


def test_every_module_but_the_supervisor_and_dispatcher_has_a_package() -> None:
    assert len(MODULE_PACKAGES) == len(ModuleName) - 2


@mark.parametrize("package", MODULE_PACKAGES)
def test_a_module_package_imports_from_no_other(package: str) -> None:
    others = set(MODULE_PACKAGES) - {package}

    imports = [
        f"{path.relative_to(SOURCE)} imports from libranet.{imported}"
        for path in sorted((SOURCE / package).rglob("*.py"))
        for imported in sorted(imported_packages(path.read_text(encoding="utf-8")) & others)
    ]

    assert imports == []


def test_an_import_from_another_package_is_found() -> None:
    source = "from libranet.webserver.search import SearchCache\nimport libranet.stats.lists\n"

    assert imported_packages(source) == {"webserver", "stats"}
