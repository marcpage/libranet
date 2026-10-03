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

Ciphertext is held whole to be decrypted. It is stored within the object
limit, and does not compress, so it is capped at the protocol's limit once
decompressed, should a node have stored it compressed (HttpApi §8): no node
could have stored ciphertext that expands to more.
"""

from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from typing import Final, Iterable, Iterator
from zlib import compress

from libranet.bundle.content import ContentSource, content_chunks, parse_cas_path
from libranet.bundle.encryption import BLOCK_BYTES, DEFAULT_IV, KEY_BYTES, Aes256Cbc
from libranet.bundle.errors import (
    BundleError,
    BundleTooLargeError,
    BundleVerificationError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.storing import HASH_ALGORITHM, ContentSink, store_object
from libranet.cas.compression import decompressed_chunks
from libranet.cas.content_id import ContentId
from libranet.cas.errors import NotZlibStreamError
from libranet.config.models import MIB

_SEPARATOR: Final = "/"

# {hash algorithm}/{encrypted data hash}/{encryption algorithm}/{encryption key}
# (§7): the stored object's two segments, then the cipher's and the key's.
_ENCRYPTED_PATH_SEGMENTS: Final = 4
_ADDRESS_SEGMENTS: Final = 2

# The one cipher read or written, and how a path names an IV after it (§7.1).
CIPHER: Final = "AES256-CBC"
_IV_SEPARATOR: Final = "-IV:"

# Never changed once parts are written with it, or identical parts encrypted
# before and after would no longer dedup (§7.3).
_COMPRESSION_LEVEL: Final = 9

# No node could have stored ciphertext larger than the object limit, since it
# does not compress.
_MAX_CIPHERTEXT_BYTES: Final = MIB


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
        return _SEPARATOR.join((str(self.content_id), cipher, self.key.hex()))

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
        segments = path.split(_SEPARATOR)

        if len(segments) <= _ADDRESS_SEGMENTS:
            return cls(parse_cas_path(path))

        if len(segments) != _ENCRYPTED_PATH_SEGMENTS:
            raise MalformedBundleError(
                f"A part's CAS path has {_ADDRESS_SEGMENTS} or {_ENCRYPTED_PATH_SEGMENTS} "
                f"segments, got {len(segments)}"
            )

        content_id = parse_cas_path(_SEPARATOR.join(segments[:_ADDRESS_SEGMENTS]))
        cipher, key_hex = segments[_ADDRESS_SEGMENTS:]
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

    @property
    def encrypted(self) -> bool:
        """Whether the part is stored encrypted."""
        return self.key is not None

    def chunks(self, source: ContentSource) -> Iterator[bytes]:
        """The part's bytes, read from ``source``, decrypted if it is encrypted.

        What is stored is checked against :attr:`content_id`, and a part
        decrypted, against its key, only once all of it has been produced,
        so a caller discards what it consumed if this raises.

        Raises:
            MissingContentError: ``source`` does not hold it.
            BundleVerificationError: what is stored does not match
                :attr:`content_id`, or does not decrypt under the key to the
                part the key is the SHA-256 of.
            UnsupportedBundleError: the ciphertext is larger than any node
                could have stored.
        """
        if self.key is None:
            yield from content_chunks(source, self.content_id)
            return

        decrypted = self._decrypted(source, self.key)

        # The key is the part's SHA-256, whatever the CAS hashes with (§7.2).
        if sha256(decrypted).digest() == self.key:
            yield decrypted
            return

        hasher = sha256()

        try:
            for chunk in decompressed_chunks(decrypted):
                hasher.update(chunk)
                yield chunk

        except NotZlibStreamError:
            raise BundleVerificationError(
                f"Encrypted part {self.content_id} does not decrypt to the part its key names"
            ) from None

        if hasher.digest() != self.key:
            raise BundleVerificationError(
                f"Encrypted part {self.content_id} does not decrypt to the part its key names"
            )

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
