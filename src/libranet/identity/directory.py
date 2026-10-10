"""The user directory: every person's id, in a directory bundle kept at a drop (HttpApi §11.5).

Each person is an entry whose path is their id, and whose only part is that
id, their public key (Phase 4 Step 92)::

    {"contents": {"sha256/…": {"contents": ["sha256/…"]}}}

A directory is made a drop at ``user directory``. Content found there is one
only if it is a drop whose bundle holds nothing but such entries, so that
content that is only the nearest the node holds is never taken for one, nor
blocked. A directory has no ``versions``.

A directory is rewritten whole as people are added. Once one would not fit
in a drop, the entries the newest holds itself are moved into an extension,
a directory of the same shape stored at its own id, which extends whatever
the newest extended. The directory made then holds the people no extension
lists, and names it. An extension is never at the drop, so it is never
blocked.

The newest directory is the one listing the most people, and of two that
list as many, the one whose id comes first. When it lacks anyone another
lists, a new one is made, holding everyone, and every directory it was made
from is replaced.
"""

from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Final, Iterable, Mapping

from libranet.bundle.errors import BundleError, MalformedBundleError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.parsing import decode_bundle
from libranet.bundle.parts import PartPath
from libranet.bundle.protection import strip_targeting
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import Bundle, DirectoryBundle, Entry, FileBundle
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DROP_SEPARATOR, DropTarget
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError

#: Where the user directory is kept (HttpApi §11.5).
DIRECTORY_TARGET: Final = DropTarget.of("user directory")


@dataclass(frozen=True)
class PeopleListing:
    """The people one directory bundle lists itself, and the extensions it names, by id."""

    people: frozenset[ContentId]
    extensions: tuple[ContentId, ...] = ()

    @classmethod
    def of_bundle(cls, bundle: DirectoryBundle) -> PeopleListing | None:
        """What ``bundle`` lists, or ``None`` if it holds an entry that is not a person's.

        It is no listing either if an extension is named by anything but a
        plain id.

        Raises:
            UnknownAlgorithmError: an entry, or an extension, names a hash
                algorithm this node lacks; all of them are named.
        """
        people = _people(bundle.entries)

        if people is None:
            return None

        extensions: list[ContentId] = []
        unknown: Counter[str] = Counter()

        for path in bundle.extensions:
            try:
                extensions.append(ContentId.parse(path))

            except UnknownAlgorithmError:
                # Not logged: counted, and raised with every other once all are read.
                unknown[path.partition("/")[0]] += 1

            except InvalidContentIdError:
                # Not logged: it makes the bundle no directory, which the caller says.
                return None

        _raise_unknown(unknown, "extensions")
        return cls(people, tuple(extensions))

    def encoded(self) -> bytes:
        """This listing as directory bundle JSON, the same bytes every time."""
        people = sorted(str(person) for person in self.people)
        return encode_bundle(
            DirectoryBundle(
                {person: FileBundle((person,)) for person in people},
                extensions=tuple(str(extension) for extension in self.extensions),
            )
        )


@dataclass(frozen=True)
class FoundDirectory:
    """A directory found at the drop, as ``content_id``.

    ``listing`` is what it lists itself, and ``extended`` the people its
    extensions list.
    """

    content_id: ContentId
    listing: PeopleListing
    extended: frozenset[ContentId] = frozenset()

    @classmethod
    def read(
        cls, content_id: ContentId, data: bytes, load: Callable[[PartPath], Bundle]
    ) -> FoundDirectory | None:
        """The directory ``data``, held as ``content_id``, is, its extensions read with ``load``.

        Returns:
            The directory, or ``None`` if ``data`` is no drop, or holds no
            directory of people.

        Raises:
            UnknownAlgorithmError: it names a hash algorithm this node lacks.
            MissingContentError: some of its extensions are not held; all
                that could be found are named.
            BundleError: an extension cannot be read, is not a directory of
                people, or more of them are reached than a reader follows.
        """
        if DROP_SEPARATOR not in data:
            return None

        try:
            bundle = decode_bundle(strip_targeting(data))

        except BundleError:
            # Not logged: content at the drop is often only the nearest held.
            return None

        if not isinstance(bundle, DirectoryBundle):
            return None

        listing = PeopleListing.of_bundle(bundle)

        if listing is None:
            return None

        extended = _people(
            resolve_directory(DirectoryBundle({}, extensions=bundle.extensions), load)
        )

        if extended is None:
            raise MalformedBundleError(
                f"An extension of the directory {content_id} lists more than people"
            )

        return cls(content_id, listing, extended)

    @property
    def people(self) -> frozenset[ContentId]:
        """Everyone this directory lists, itself or in its extensions."""
        return self.listing.people | self.extended


@dataclass(frozen=True)
class DirectoryMerge:
    """The directories ``found`` at the drop, and the one to make so that one lists everyone."""

    found: tuple[FoundDirectory, ...]

    @property
    def newest(self) -> FoundDirectory | None:
        """The directory listing the most people, the first by id of those listing as many."""
        return min(
            self.found,
            key=lambda found: (-len(found.people), str(found.content_id)),
            default=None,
        )

    def everyone(self, added: Iterable[ContentId] = ()) -> frozenset[ContentId]:
        """Everyone any directory found lists, with ``added``."""
        return frozenset(added).union(*(found.people for found in self.found))

    def rewritten(self, everyone: frozenset[ContentId]) -> PeopleListing | None:
        """The directory to make so that one lists ``everyone``, extended as the newest is.

        Returns:
            The directory, or ``None`` if the newest lists them all, or there
            is no one to list.
        """
        newest = self.newest

        if newest is None:
            return PeopleListing(everyone) if everyone else None

        if everyone <= newest.people:
            return None

        return PeopleListing(everyone - newest.extended, newest.listing.extensions)

    def extension(self) -> PeopleListing | None:
        """The extension to make when the directory rewritten does not fit in a drop.

        It holds what the newest lists itself, and extends what the newest
        extends.

        Returns:
            The extension, or ``None`` if there is no newest, or it lists no
            one itself, so that an extension would leave no more room.
        """
        newest = self.newest

        if newest is None or not newest.listing.people:
            return None

        return newest.listing

    def extended(self, everyone: frozenset[ContentId], extension: ContentId) -> PeopleListing:
        """The directory listing ``everyone``, over ``extension``, made of the newest's own list."""
        newest = self.newest
        listed = frozenset() if newest is None else newest.people
        return PeopleListing(everyone - listed, (extension,))

    def replaced(
        self, people: frozenset[ContentId], kept: ContentId | None = None
    ) -> list[ContentId]:
        """Each directory found, but ``kept``, that lists no one ``people`` lacks, to be blocked."""
        return [
            found.content_id
            for found in self.found
            if found.content_id != kept and found.people <= people
        ]


def _people(entries: Mapping[str, Entry | None]) -> frozenset[ContentId] | None:
    """The people ``entries`` list, or ``None`` if one is not a person's.

    A person's entry is a file whose path is a content id, written as it
    always is, and whose only part is that id.

    Raises:
        UnknownAlgorithmError: an entry's path names a hash algorithm this
            node lacks; all of them are named.
    """
    people: list[ContentId] = []
    unknown: Counter[str] = Counter()

    for path, entry in entries.items():
        if not isinstance(entry, FileBundle) or entry.parts != (path,):
            return None

        try:
            person = ContentId.parse(path)

        except UnknownAlgorithmError:
            # Not logged: counted, and raised with every other once all are read.
            unknown[path.partition("/")[0]] += 1
            continue

        except InvalidContentIdError:
            # Not logged: it makes the entries no person's, which the caller says.
            return None

        if str(person) != path:
            return None

        people.append(person)

    _raise_unknown(unknown, "entries")
    return frozenset(people)


def _raise_unknown(unknown: Counter[str], what: str) -> None:
    """Raise if ``unknown`` counts any hash algorithm, naming each and how many ``what`` used it.

    Raises:
        UnknownAlgorithmError: it does.
    """
    if unknown:
        named = ", ".join(f"{algorithm} ({count})" for algorithm, count in sorted(unknown.items()))
        raise UnknownAlgorithmError(
            f"Directory {what} name hash algorithms this node lacks: {named}"
        )
