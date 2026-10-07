"""A part of a file, as a bundle names it: stored as it is, or encrypted (BundleSpecification §7).

A bundle names each part of a file, or of an extended attribute's value
(§2.4), by CAS path. A plain path, ``{algorithm}/{hash}``, names the part
itself. An encrypted one adds the cipher and the key,
``{algorithm}/{hash}/AES256-CBC/{key}``, and names the part's ciphertext, so
that a node holding it can check it against its identifier without being able
to read it (§7.1). ``AES256-CBC`` is the only cipher read or written, with an
IV of all zeros unless the path names one, as ``AES256-CBC-IV:{hex}``.

A part is encrypted under its own SHA-256 (§7.2), with the default IV, so the
same part always encrypts to the same object and dedups. It is zlib-compressed
first, at the highest level, when that makes it smaller, since ciphertext does
not compress (§7.3). A reader tells whether it was by the key, which is the
SHA-256 of the part however it was encrypted: decrypted bytes that hash to it
are the part, and otherwise they must be a zlib stream of it. The key thereby
checks the part, as well as decrypting it.

Padding makes ciphertext up to one block longer than what was encrypted, so
an encrypted file is cut into parts a block short of the object limit, which
every part then fits even when it does not compress.

A file whose bytes are at hand, as a request gives them, is cut into parts
here, as a file read from disk is (:mod:`libranet.bundle.building`), and its
bundle records what its bytes decide (Phase 3 Step 72).

Ciphertext is held whole to be decrypted. It is stored within the object
limit, and does not compress, so it is capped at the protocol's limit once
decompressed, should a node have stored it compressed (HttpApi §8): no node
could have stored ciphertext that expands to more.

A bundle stored encrypted is named, and read, as a part is: as an extension
another bundle names, or as one a client reads into by its encrypted id
(HttpApi §12.1, Phase 3 Step 71). The id then appears in a request's target,
and the key it carries is left out of whatever logs that.
"""

from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from re import IGNORECASE, compile as compile_pattern, escape
from typing import Callable, Final, Iterable, Iterator
from zlib import compress

from libranet.bundle.content import (
    CAS_PATH_SEPARATOR,
    ENCRYPTED_PATH_SEGMENTS,
    PLAIN_PATH_SEGMENTS,
    ContentSource,
    content_chunks,
    parse_cas_path,
)
from libranet.bundle.encryption import BLOCK_BYTES, DEFAULT_IV, KEY_BYTES, Aes256Cbc
from libranet.bundle.errors import (
    BundleError,
    BundleTooLargeError,
    BundleVerificationError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.shapes import FileBundle, Metadata
from libranet.bundle.storing import HASH_ALGORITHM, ContentSink, store_object
from libranet.cas.algorithms import DEFAULT_REGISTRY, Sha256Algorithm
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentMismatchError
from libranet.cas.verification import matching_chunks
from libranet.config.models import MIB

# The one cipher read or written, and how a path names an IV after it (§7.1).
CIPHER: Final = "AES256-CBC"
_IV_SEPARATOR: Final = "-IV:"

# The cipher's segment of an encrypted path written in other text, and the
# key's after it, which is left out of anything logged. Any case is matched,
# so that a key is left out even where its cipher is misspelled.
_KEY_IN_TEXT: Final = compile_pattern(
    rf"(/{escape(CIPHER)}(?:{escape(_IV_SEPARATOR)}[^/\s]*)?/)[^/\s]+", IGNORECASE
)

# What a key is shown as in text it is left out of.
_KEY_LEFT_OUT: Final = "<key>"

# Never changed once parts are written with it, or identical parts encrypted
# before and after would no longer dedup (§7.3).
_COMPRESSION_LEVEL: Final = 9

# No node could have stored ciphertext larger than the object limit, since it
# does not compress.
_MAX_CIPHERTEXT_BYTES: Final = MIB

# What a key is the hash of a part with, whatever the CAS hashes with (§7.2).
_KEY_ALGORITHM: Final = Sha256Algorithm()


@dataclass(frozen=True)
class PartPath:
    """A part as a bundle names it: what is stored, and the key to it if it is encrypted (§7).

    ``content_id`` names what is stored: the part itself, or its ciphertext.
    ``key`` is ``None`` for a part stored as it is.

    Raises:
        MalformedBundleError: ``key`` is not an AES-256 key, or ``iv`` is not
            one block.
    """

    content_id: ContentId
    key: bytes | None = None
    iv: bytes = DEFAULT_IV

    def __post_init__(self) -> None:
        if self.key is not None and len(self.key) != KEY_BYTES:
            raise MalformedBundleError(f"A part's key is {KEY_BYTES} bytes, got {len(self.key)}")

        if len(self.iv) != BLOCK_BYTES:
            raise MalformedBundleError(f"A part's IV is {BLOCK_BYTES} bytes, got {len(self.iv)}")

    def __str__(self) -> str:
        if self.key is None:
            return str(self.content_id)

        cipher = CIPHER if self.iv == DEFAULT_IV else f"{CIPHER}{_IV_SEPARATOR}{self.iv.hex()}"
        return CAS_PATH_SEPARATOR.join((str(self.content_id), cipher, self.key.hex()))

    @classmethod
    def parse(cls, path: str) -> PartPath:
        """The part a bundle names by the CAS path ``path``.

        The key is not shown in any error, since it is what keeps the part
        unread.

        Raises:
            UnsupportedBundleError: ``path`` names a hash algorithm or a
                cipher this node lacks.
            MalformedBundleError: ``path`` is not a CAS path, or its key or
                IV is not hex of the right length.
        """
        segments = path.split(CAS_PATH_SEPARATOR)

        if len(segments) <= PLAIN_PATH_SEGMENTS:
            return cls(parse_cas_path(path))

        if len(segments) != ENCRYPTED_PATH_SEGMENTS:
            raise MalformedBundleError(
                f"A part's CAS path has {PLAIN_PATH_SEGMENTS} or {ENCRYPTED_PATH_SEGMENTS} "
                f"segments, got {len(segments)}"
            )

        content_id = parse_cas_path(CAS_PATH_SEPARATOR.join(segments[:PLAIN_PATH_SEGMENTS]))
        cipher, key_hex = segments[PLAIN_PATH_SEGMENTS:]
        name, separator, iv_hex = cipher.partition(_IV_SEPARATOR)

        if name != CIPHER:
            raise UnsupportedBundleError(f"Unsupported part encryption: {cipher!r}")

        key = _hex_bytes(key_hex, f"The key of encrypted part {content_id}")
        iv = (
            _hex_bytes(iv_hex, f"The IV of encrypted part {content_id}")
            if separator
            else DEFAULT_IV
        )
        return cls(content_id, key, iv)

    @staticmethod
    def without_keys(text: str) -> str:
        """``text``, such as a request's target, with every encrypted path's key left out."""
        return _KEY_IN_TEXT.sub(rf"\g<1>{_KEY_LEFT_OUT}", text)

    @staticmethod
    def is_cipher(segment: str) -> bool:
        """Whether ``segment`` names a cipher this node reads, as an encrypted path's third does."""
        return segment.partition(_IV_SEPARATOR)[0] == CIPHER

    @property
    def encrypted(self) -> bool:
        """Whether the part is stored encrypted."""
        return self.key is not None

    def chunks(self, source: ContentSource, size_bytes: int | None = None) -> Iterator[bytes]:
        """The part's bytes, read from ``source``, decrypted if it is encrypted.

        What is stored is checked against :attr:`content_id`, and a part
        decrypted, against its key, only once all of it has been produced,
        so a caller discards what it consumed if this raises. So is its
        length against ``size_bytes``, its size as a bundle records it
        (§2.1), if given, though no more than that is produced.

        Raises:
            MissingContentError: ``source`` does not hold it.
            BundleVerificationError: what is stored does not match
                :attr:`content_id`, or does not decrypt under the key to the
                part the key is the SHA-256 of, or the part is not
                ``size_bytes`` long.
            UnsupportedBundleError: the ciphertext is larger than any node
                could have stored.
        """
        if size_bytes is None:
            yield from self._chunks(source)
            return

        produced_bytes = 0

        for chunk in self._chunks(source):
            produced_bytes += len(chunk)

            if produced_bytes > size_bytes:
                raise BundleVerificationError(
                    f"Part {self.content_id} is larger than its size, {size_bytes}"
                )

            yield chunk

        if produced_bytes != size_bytes:
            raise BundleVerificationError(
                f"Part {self.content_id} is {produced_bytes} bytes, not its size, {size_bytes}"
            )

    def _chunks(self, source: ContentSource) -> Iterator[bytes]:
        """The part's bytes, read from ``source``, decrypted and checked as :meth:`chunks` says.

        Raises:
            As :meth:`chunks` does, but for the part's size.
        """
        if self.key is None:
            yield from content_chunks(source, self.content_id)
            return

        decrypted = self._decrypted(source, self.key)

        try:
            yield from matching_chunks(decrypted, _KEY_ALGORITHM, self.key.hex())

        except ContentMismatchError:
            raise BundleVerificationError(
                f"Encrypted part {self.content_id} does not decrypt to the part its key names"
            ) from None

    def _decrypted(self, source: ContentSource, key: bytes) -> bytes:
        """The ciphertext ``source`` holds for this part, decrypted under ``key``.

        Raises:
            MissingContentError: ``source`` does not hold it.
            BundleVerificationError: what is stored does not match
                :attr:`content_id`, or is not ciphertext under ``key``.
            UnsupportedBundleError: it is larger than any node could have
                stored.
        """
        ciphertext = bytearray()

        for chunk in content_chunks(source, self.content_id):
            ciphertext += chunk

            if len(ciphertext) > _MAX_CIPHERTEXT_BYTES:
                raise UnsupportedBundleError(
                    f"Encrypted part {self.content_id} is over {_MAX_CIPHERTEXT_BYTES} bytes"
                )

        try:
            return Aes256Cbc(key, self.iv).decrypt(bytes(ciphertext))

        except ValueError:
            raise BundleVerificationError(
                f"Encrypted part {self.content_id} does not decrypt under its key"
            ) from None


@dataclass(frozen=True)
class PartWriter:
    """How parts of files and attribute values are stored in ``sink``: plain, or encrypted.

    ``max_object_bytes`` is the most an object stored may hold.

    Raises:
        ValueError: ``max_object_bytes`` leaves no room for a part: it is
            not positive, or, to encrypt, no larger than one block.
    """

    sink: ContentSink
    max_object_bytes: int = MIB
    encrypted: bool = False

    def __post_init__(self) -> None:
        if self.part_bytes < 1:
            raise ValueError(
                f"max_object_bytes leaves no room for a part, got {self.max_object_bytes}"
            )

    @property
    def part_bytes(self) -> int:
        """The most bytes of a file, or of an attribute's value, that one part holds."""
        return self.max_object_bytes - BLOCK_BYTES if self.encrypted else self.max_object_bytes

    def store(self, part: bytes) -> PartPath:
        """Store ``part`` unless it is held, and return the path a bundle names it by.

        Raises:
            BundleTooLargeError: ``part`` is not held, and does not fit in an
                object, as it is longer than :attr:`part_bytes`.
        """
        if not self.encrypted:
            return PartPath(store_object(part, self.sink, self.max_object_bytes))

        key = sha256(part).digest()
        compressed = compress(part, _COMPRESSION_LEVEL)
        ciphertext = Aes256Cbc(key).encrypt(compressed if len(compressed) < len(part) else part)
        content_id = ContentId.for_data(ciphertext, HASH_ALGORITHM)

        if not self.sink.exists(content_id):
            if len(ciphertext) > self.max_object_bytes:
                raise BundleTooLargeError(
                    f"{len(part)} bytes encrypt to {len(ciphertext)}, "
                    f"over {self.max_object_bytes} bytes"
                )

            # Ciphertext does not compress, so it is stored as it is.
            self.sink.write(content_id, ciphertext)

        return PartPath(content_id, key)

    def file(self, data: bytes) -> FileBundle:
        """The bundle for a file of ``data``, each part stored unless it is held.

        It records only what the bytes decide: their size, each part's, and
        their whole-file hash (§2.1, §2.3), with no times or permissions. An
        empty file has no parts.
        """
        return self.file_of(self.cut(data))

    def file_of(
        self, parts: Iterable[bytes], *, stored: Callable[[int], None] | None = None
    ) -> FileBundle:
        """The bundle for a file whose bytes are ``parts``, in order, each stored unless held.

        It records only what :meth:`file` does. Each part is no longer than
        :attr:`part_bytes`. ``stored``, if given, is told the size of each
        part once it is stored.

        Raises:
            BundleTooLargeError: a part is not held, and is too long to store.
        """
        hasher = DEFAULT_REGISTRY.get(HASH_ALGORITHM).hasher()
        paths: list[str] = []
        sizes_bytes: list[int] = []

        for part in parts:
            hasher.update(part)
            paths.append(str(self.store(part)))
            sizes_bytes.append(len(part))

            if stored is not None:
                stored(len(part))

        metadata = Metadata(
            size_bytes=sum(sizes_bytes), algorithm=HASH_ALGORITHM, hash=hasher.hexdigest()
        )
        return FileBundle(tuple(paths), metadata, part_sizes_bytes=tuple(sizes_bytes))

    def cut(self, data: bytes) -> Iterator[bytes]:
        """``data`` cut into parts, each :attr:`part_bytes` long but the last."""
        step = self.part_bytes
        return (data[start : start + step] for start in range(0, len(data), step))

    def keeps(self, parts: Iterable[str]) -> bool:
        """Whether ``parts``, named by an earlier bundle, are stored as this writer stores them.

        They are if each is encrypted, when this writer encrypts, and
        otherwise if none is.
        """
        try:
            return all(PartPath.parse(part).encrypted == self.encrypted for part in parts)

        except BundleError:
            # Not logged: a part this node cannot read is not kept, but
            # stored again from the file, as this writer stores it.
            return False


def _hex_bytes(text: str, what: str) -> bytes:
    """The bytes the hex ``text`` spells; ``what`` it is says what failed if it is not hex.

    Raises:
        MalformedBundleError: ``text`` is not hex.
    """
    try:
        return bytes.fromhex(text)

    except ValueError:
        raise MalformedBundleError(f"{what} is not hex") from None
