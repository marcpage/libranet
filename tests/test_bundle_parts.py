"""Tests for a file's parts, stored as they are or encrypted."""

from __future__ import annotations
from hashlib import sha256
from os import urandom
from pathlib import Path
from zlib import compress

from pytest import fixture, mark, raises

from libranet.bundle.encryption import BLOCK_BYTES, DEFAULT_IV, Aes256Cbc
from libranet.bundle.errors import (
    BundleTooLargeError,
    BundleVerificationError,
    MalformedBundleError,
    MissingContentError,
    UnsupportedBundleError,
)
from libranet.bundle.parts import CIPHER, PartPath, PartWriter
from libranet.bundle.storing import store_object
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import MIB

from tests.helpers import encrypted_part

PART = b"a part of a file, " * 100
# What a path names, unlike the key, which is the part's own SHA-256.
CONTENT_ID = ContentId.for_data(b"what is stored", "sha256")
KEY = sha256(PART).digest()
KEY_HEX = KEY.hex()
IV = bytes(range(BLOCK_BYTES))
MAX_BYTES = 64


class RecordingStore(CasStore):
    """A CAS store that records every write."""

    def __init__(self, root: Path) -> None:
        super().__init__(root, prefix_length=4)
        self.writes: list[ContentId] = []

    def write(self, content_id: ContentId, data: bytes) -> Path:
        self.writes.append(content_id)
        return super().write(content_id, data)


@fixture
def store(tmp_path: Path) -> RecordingStore:
    return RecordingStore(tmp_path / "cas")


def read(path: PartPath, store: CasStore) -> bytes:
    return b"".join(path.chunks(store))


def test_a_plain_path_names_the_part_itself() -> None:
    path = PartPath.parse(str(CONTENT_ID))

    assert path == PartPath(CONTENT_ID)
    assert not path.encrypted
    assert str(path) == str(CONTENT_ID)


def test_an_encrypted_path_names_the_ciphertext_and_the_key() -> None:
    path = PartPath.parse(f"{CONTENT_ID}/{CIPHER}/{KEY_HEX}")

    assert path == PartPath(CONTENT_ID, KEY, DEFAULT_IV)
    assert path.encrypted
    assert str(path) == f"{CONTENT_ID}/AES256-CBC/{KEY_HEX}"


def test_an_encrypted_path_may_name_an_iv() -> None:
    path = PartPath.parse(f"{CONTENT_ID}/AES256-CBC-IV:{IV.hex()}/{KEY_HEX}")

    assert path == PartPath(CONTENT_ID, KEY, IV)
    assert str(path) == f"{CONTENT_ID}/AES256-CBC-IV:{IV.hex()}/{KEY_HEX}"


def test_a_path_is_read_whatever_the_case_of_its_hex() -> None:
    path = PartPath.parse(f"sha256/{CONTENT_ID.hash.upper()}/AES256-CBC/{KEY_HEX.upper()}")

    assert path == PartPath(CONTENT_ID, KEY)


@mark.parametrize(
    "path",
    [
        f"{CONTENT_ID}/DES-CBC/{KEY_HEX}",
        f"{CONTENT_ID}/aes256-cbc/{KEY_HEX}",
        f"blake3/{'a' * 64}/AES256-CBC/{KEY_HEX}",
    ],
)
def test_a_cipher_or_algorithm_this_node_lacks_is_unsupported(path: str) -> None:
    with raises(UnsupportedBundleError) as raised:
        PartPath.parse(path)

    assert KEY_HEX not in str(raised.value)


@mark.parametrize(
    "path",
    [
        "",
        f"{CONTENT_ID}/AES256-CBC",
        f"{CONTENT_ID}/AES256-CBC/{KEY_HEX}/extra",
        f"sha256/zz/AES256-CBC/{KEY_HEX}",
        f"{CONTENT_ID}/AES256-CBC/{KEY_HEX[:-2]}",
        f"{CONTENT_ID}/AES256-CBC/{KEY_HEX}00",
        f"{CONTENT_ID}/AES256-CBC/{KEY_HEX[:-1]}z",
        f"{CONTENT_ID}/AES256-CBC-IV:00/{KEY_HEX}",
        f"{CONTENT_ID}/AES256-CBC-IV:{'z' * 32}/{KEY_HEX}",
    ],
)
def test_a_path_that_is_not_a_part_is_malformed(path: str) -> None:
    with raises(MalformedBundleError) as raised:
        PartPath.parse(path)

    assert KEY_HEX[:-2] not in str(raised.value)


def test_a_plain_writer_stores_a_part_as_it_is(store: RecordingStore) -> None:
    writer = PartWriter(store, MAX_BYTES)

    path = writer.store(b"plain")

    assert writer.part_bytes == MAX_BYTES
    assert path == PartPath(store_object(b"plain", store))
    assert read(path, store) == b"plain"


def test_an_encrypted_part_is_stored_as_ciphertext_under_its_own_key(
    store: RecordingStore,
) -> None:
    path = PartWriter(store, encrypted=True).store(PART)

    assert path == encrypted_part(PART)
    assert path.key == KEY
    assert PART not in store.read(path.content_id)
    assert read(path, store) == PART


def test_the_same_part_encrypts_to_the_same_object_and_is_stored_once(
    store: RecordingStore,
) -> None:
    writer = PartWriter(store, encrypted=True)

    first, second = writer.store(PART), writer.store(PART)

    assert first == second
    assert store.writes == [first.content_id]


def test_a_part_that_compresses_is_compressed_before_it_is_encrypted(
    store: RecordingStore,
) -> None:
    path = PartWriter(store, encrypted=True).store(PART)

    stored = store.read(path.content_id)
    assert stored == Aes256Cbc(KEY).encrypt(compress(PART, 9))
    assert len(stored) < len(PART)


def test_a_part_that_does_not_compress_is_encrypted_as_it_is(store: RecordingStore) -> None:
    part = urandom(100)

    path = PartWriter(store, encrypted=True).store(part)

    assert store.read(path.content_id) == Aes256Cbc(sha256(part).digest()).encrypt(part)
    assert read(path, store) == part


def test_an_encrypted_part_is_cut_a_block_short_so_it_fits_once_padded(
    store: RecordingStore,
) -> None:
    writer = PartWriter(store, MAX_BYTES, encrypted=True)
    part = urandom(writer.part_bytes)

    path = writer.store(part)

    assert writer.part_bytes == MAX_BYTES - BLOCK_BYTES
    assert len(store.read(path.content_id)) == MAX_BYTES


def test_an_encrypted_part_longer_than_a_part_holds_is_too_large(store: RecordingStore) -> None:
    writer = PartWriter(store, MAX_BYTES, encrypted=True)

    with raises(BundleTooLargeError):
        writer.store(urandom(MAX_BYTES))

    assert store.writes == []


@mark.parametrize(("max_object_bytes", "encrypted"), [(0, False), (BLOCK_BYTES, True)])
def test_a_writer_whose_objects_hold_no_part_is_refused(
    store: RecordingStore, max_object_bytes: int, encrypted: bool
) -> None:
    with raises(ValueError, match="no room"):
        PartWriter(store, max_object_bytes, encrypted)


@mark.parametrize(
    ("encrypted", "parts", "kept"),
    [
        (False, [], True),
        (False, [str(CONTENT_ID)], True),
        (False, [str(CONTENT_ID), f"{CONTENT_ID}/AES256-CBC/{KEY_HEX}"], False),
        (True, [], True),
        (True, [f"{CONTENT_ID}/AES256-CBC/{KEY_HEX}"], True),
        (True, [f"{CONTENT_ID}/AES256-CBC/{KEY_HEX}", str(CONTENT_ID)], False),
        (True, [f"{CONTENT_ID}/DES-CBC/{KEY_HEX}"], False),
        (False, ["sha256/zz"], False),
    ],
)
def test_a_writer_keeps_only_parts_stored_as_it_stores_them(
    store: RecordingStore, encrypted: bool, parts: list[str], kept: bool
) -> None:
    assert PartWriter(store, encrypted=encrypted).keeps(parts) is kept


def test_an_iv_a_path_names_is_used_to_decrypt(store: RecordingStore) -> None:
    ciphertext = Aes256Cbc(KEY, IV).encrypt(PART)
    content_id = store_object(ciphertext, store)

    assert read(PartPath(content_id, KEY, IV), store) == PART


def test_ciphertext_a_node_stored_compressed_is_read(store: RecordingStore) -> None:
    ciphertext = Aes256Cbc(KEY).encrypt(compress(PART, 9))
    content_id = ContentId.for_data(ciphertext, "sha256")
    store.write(content_id, compress(ciphertext))

    assert read(PartPath(content_id, KEY), store) == PART


def test_an_encrypted_part_not_held_is_missing(store: RecordingStore) -> None:
    with raises(MissingContentError):
        read(encrypted_part(PART), store)


def test_ciphertext_that_does_not_match_its_identifier_is_refused(
    store: RecordingStore,
) -> None:
    path = encrypted_part(PART)
    store.write(path.content_id, b"something else entirely")

    with raises(BundleVerificationError):
        read(path, store)


def test_a_part_under_the_wrong_key_does_not_decrypt(store: RecordingStore) -> None:
    path = PartWriter(store, encrypted=True).store(PART)

    with raises(BundleVerificationError, match="does not decrypt"):
        read(PartPath(path.content_id, sha256(b"another part").digest()), store)


@mark.parametrize(
    "inner",
    [b"neither the part nor a stream of it", compress(b"a stream of another part")],
)
def test_a_part_that_decrypts_to_something_its_key_does_not_name_is_refused(
    store: RecordingStore, inner: bytes
) -> None:
    content_id = store_object(Aes256Cbc(KEY).encrypt(inner), store)

    with raises(BundleVerificationError, match="part its key names"):
        read(PartPath(content_id, KEY), store)


def test_ciphertext_larger_than_any_node_could_store_is_unsupported(
    store: RecordingStore,
) -> None:
    ciphertext = bytes(MIB + BLOCK_BYTES)
    content_id = ContentId.for_data(ciphertext, "sha256")
    store.write(content_id, compress(ciphertext))

    with raises(UnsupportedBundleError):
        read(PartPath(content_id, KEY), store)
