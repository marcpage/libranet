#!/usr/bin/env python3
"""Filter a CMake compilation database to translation units whose .cpp or
transitively included .hpp files changed since the last manifest."""

from __future__ import annotations

from argparse import ArgumentParser
from hashlib import sha256
from json import dump, load
from os import walk
from pathlib import Path
from re import compile as re_compile
from sys import exit as sys_exit
from sys import stderr

INCLUDE_RE = re_compile(r'^\s*#\s*include\s+[<"]([^>"]+)[>"]')


def sha256_file(path: Path) -> str:
    """hash a file"""
    digest = sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)

    return digest.hexdigest()


def collect_sources(src_root: Path) -> tuple[set[Path], set[Path]]:
    """Return (hpp_files, cpp_files) as {resolved_path: resolved_path} under src_root."""
    hpp_files: set[Path] = set()
    cpp_files: set[Path] = set()

    for root, dirs, files in walk(src_root):
        dirs[:] = [  # Skip common build/cache subdirectories if src_root overlaps them
            d for d in dirs if d not in {"build", ".git", ".conan2", "__pycache__"}
        ]

        for name in files:
            p = (Path(root) / name).resolve()

            if p.suffix == ".hpp":
                hpp_files.add(p)
            elif p.suffix == ".cpp":
                cpp_files.add(p)

    return hpp_files, cpp_files


def resolve_include(  # pylint: disable=too-many-positional-arguments
    inc_target: str,
    including_file: Path,
    src_root: Path,
    search_dirs: list[Path],
    known_hpp: set[Path],
    known_cpp: set[Path],
) -> Path | None:
    """Resolve an #include path to a known source file if possible."""
    candidates: list[Path] = []

    # 1. Relative to the file containing the #include
    candidates.append(including_file.parent / inc_target)

    # 2. Relative to src_root and any extra search directories
    candidates.append(src_root / inc_target)

    for base in search_dirs:
        candidates.append(base / inc_target)

    for cand in candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue

        if resolved in known_hpp or resolved in known_cpp:
            return resolved

    # Fallback: match suffix under src_root (e.g., #include "net/SocketPosix.hpp"
    # when src_root is .../cpp/src and header is .../cpp/src/net/SocketPosix.hpp)
    inc_path = Path(inc_target)

    for known in (*known_hpp, *known_cpp):
        try:
            rel = known.relative_to(src_root)
        except ValueError:
            continue

        if rel == inc_path or str(rel).endswith("/" + inc_target):
            return known

    return None


def parse_includes(file_path: Path) -> list[str]:
    """Get includes"""
    includes: list[str] = []

    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = INCLUDE_RE.match(line)

                if m:
                    includes.append(m.group(1).strip())
    except OSError as err:
        print(f"warning: failed to parse includes from {file_path}: {err}", file=stderr)

    return includes


def build_hpp_to_cpp_map(  # pylint: disable=too-many-locals
    hpp_files: set[Path],
    cpp_files: set[Path],
    src_root: Path,
    extra_dirs: list[Path],
) -> dict[Path, set[Path]]:
    """Map each .hpp file to the set of .cpp files that transitively include it."""
    # Direct edges: file -> set of directly included files
    direct_includes: dict[Path, set[Path]] = {}
    all_known = hpp_files | cpp_files

    for f in all_known:
        targets: set[Path] = set()

        for inc in parse_includes(f):
            resolved = resolve_include(
                inc, f, src_root, extra_dirs, hpp_files, cpp_files
            )

            if resolved is not None:
                targets.add(resolved)

        direct_includes[f] = targets

    # Reverse adjacency for headers: header -> set of files (hpp or cpp) that include it
    reverse_deps: dict[Path, set[Path]] = {h: set() for h in hpp_files}

    for src_file, inc_set in direct_includes.items():
        for inc_file in inc_set:
            if inc_file in reverse_deps:
                reverse_deps[inc_file].add(src_file)

    # Propagate from each .hpp through including headers until reaching .cpp files
    hpp_to_cpp: dict[Path, set[Path]] = {h: set() for h in hpp_files}
    for hpp in hpp_files:
        visited: set[Path] = set()
        stack: list[Path] = list(reverse_deps.get(hpp, ()))

        while stack:
            cur = stack.pop()

            if cur in visited:
                continue

            visited.add(cur)

            if cur in cpp_files:
                hpp_to_cpp[hpp].add(cur)
            elif cur in reverse_deps:
                stack.extend(reverse_deps[cur])

    return hpp_to_cpp


def resolve_entry_file(entry: dict, entry_dir: Path) -> Path | None:
    """find the file"""
    raw_file = entry.get("file")

    if not raw_file:
        return None

    p = Path(raw_file)

    if not p.is_absolute():
        p = entry_dir / p

    try:
        return p.resolve()
    except OSError:
        return None


def main() -> int:
    """do the work"""
    parser = ArgumentParser(
        description="Filter compile_commands.json to dirty .cpp translation units."
    )
    parser.add_argument(
        "compdb_path", type=Path, help="Path to input compile_commands.json"
    )
    parser.add_argument("manifest_path", type=Path, help="Path to manifest JSON file")
    parser.add_argument("src_path", type=Path, help="Source search root directory")
    parser.add_argument(
        "output_compdb_path",
        type=Path,
        help="Path to write filtered compile_commands.json",
    )
    args = parser.parse_args()

    compdb_path = args.compdb_path.resolve()
    manifest_path = args.manifest_path.resolve()
    src_root = args.src_path.resolve()
    output_compdb_path = args.output_compdb_path.resolve()

    if not compdb_path.is_file():
        print(f"Error: compilation database not found: {compdb_path}", file=stderr)
        return 1

    if not src_root.is_dir():
        print(f"Error: source search path not found: {src_root}", file=stderr)
        return 1

    with compdb_path.open("r", encoding="utf-8") as f:
        entries = load(f)

    if not isinstance(entries, list):
        print(f"Error: expected JSON array in {compdb_path}", file=stderr)
        return 1

    # Load existing manifest if it exists
    old_hashes: dict[str, str] = {}

    if manifest_path.is_file():
        with manifest_path.open("r", encoding="utf-8") as f:
            data = load(f)

            if isinstance(data, dict) and isinstance(data.get("files"), dict):
                old_hashes = {str(k): str(v) for k, v in data["files"].items()}

    hpp_set, cpp_set = collect_sources(src_root)

    # Collect entry directories as extra include search bases
    extra_dirs: list[Path] = []

    for entry in entries:
        d = entry.get("directory")

        if d:
            rd = Path(d).resolve()

            if rd not in extra_dirs:
                extra_dirs.append(rd)

    # Compute current hashes for all discovered .hpp and .cpp files
    current_hashes: dict[Path, str] = {}

    for p in hpp_set | cpp_set:
        current_hashes[p] = sha256_file(p)

    def manifest_key(p: Path) -> str:
        try:
            return p.relative_to(src_root).as_posix()
        except ValueError:
            return p.as_posix()

    # Determine which files changed compared to the previous manifest
    changed_files: set[Path] = set()

    for p, digest in current_hashes.items():
        key = manifest_key(p)
        if old_hashes.get(key) != digest:
            changed_files.add(p)

    # Build transitive map from .hpp -> affected .cpp files
    hpp_to_cpp = build_hpp_to_cpp_map(hpp_set, cpp_set, src_root, extra_dirs)

    # Collect all .cpp files that must be re-checked
    dirty_cpp: set[Path] = set()

    for p in changed_files:
        if p in cpp_set:
            dirty_cpp.add(p)
        elif p in hpp_set:
            dirty_cpp.update(hpp_to_cpp.get(p, ()))

    # Filter compilation database entries in memory
    filtered_entries = []

    for entry in entries:
        entry_dir = Path(entry.get("directory", compdb_path.parent)).resolve()
        resolved_file = resolve_entry_file(entry, entry_dir)

        if resolved_file is None:
            # Keep unknown/unresolvable entries rather than silently dropping them
            filtered_entries.append(entry)
            continue

        if resolved_file in dirty_cpp:
            filtered_entries.append(entry)

    # Write filtered compilation database
    output_compdb_path.parent.mkdir(parents=True, exist_ok=True)

    with output_compdb_path.open("w", encoding="utf-8") as f:
        dump(filtered_entries, f, indent=2)
        f.write("\n")

    # Write updated manifest (keyed relative to src_root when possible)
    new_manifest = {
        "files": {
            manifest_key(p): current_hashes[p]
            for p in sorted(current_hashes.keys(), key=manifest_key)
        }
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    with manifest_path.open("w", encoding="utf-8") as f:
        dump(new_manifest, f, indent=2)
        f.write("\n")

    print(
        f"Filtered compile_commands: {len(filtered_entries)}/{len(entries)} entries kept "
        f"({len(changed_files)} changed files, {len(dirty_cpp)} affected .cpp files)."
    )
    return 0


if __name__ == "__main__":
    sys_exit(main())
