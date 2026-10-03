"""Tests for reading an application file from its parts, a part at a time, as a response is sent."""

from __future__ import annotations
from hashlib import sha256
from logging import INFO, WARNING
from pathlib import Path
from threading import Timer
from time import monotonic
from typing import Any, Mapping

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.bundle.errors import (
    BundleVerificationError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.parts import PartWriter
from libranet.bundle.shapes import FileBundle, Metadata
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.webserver.errors import ResponseCutShortError
from libranet.webserver.file_stream import FileStream, PartReader

PARTS = (b"aaaa", b"bbbb", b"cc")
CONTENT = b"".join(PARTS)
ASK_AGAIN_SECONDS = 60.0


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def ids(self, event: EventType) -> list[ContentId]:
        """The content each message of ``event`` names, in order."""
        return [ContentId.from_fields(payload) for sent, payload in self.messages if sent == event]


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", 4)


@fixture
def published() -> Recorder:
    return Recorder()


def reader_for(
    store: CasStore,
    published: Recorder,
    wait_seconds: float = 0.0,
    read_ahead_parts: int = 8,
    ask_again_seconds: float = ASK_AGAIN_SECONDS,
) -> PartReader:
    return PartReader(
        store,
        published,
        wait_seconds,
        ask_again_seconds,
        read_ahead_parts=read_ahead_parts,
        poll_interval_seconds=0.01,
    )


@fixture
def reader(store: CasStore, published: Recorder) -> PartReader:
    return reader_for(store, published)


def id_of(part: bytes) -> ContentId:
    return ContentId.for_data(part, "sha256")


def hold(store: CasStore, *parts: bytes) -> None:
    for part in parts:
        store.write(id_of(part), part)


def entry_of(
    *parts: bytes, whole: bytes | None = None, sizes: bool = True, size: bool = True
) -> FileBundle:
    """The entry of a file joining ``parts``, checked against ``whole`` (their join by default).

    ``sizes`` records each part's size, and ``size`` the file's.
    """
    content = b"".join(parts) if whole is None else whole
    return FileBundle(
        tuple(str(id_of(part)) for part in parts),
        Metadata(
            size_bytes=len(content) if size else None,
            algorithm="sha256",
            hash=sha256(content).hexdigest(),
        ),
        part_sizes_bytes=tuple(len(part) for part in parts) if sizes else None,
    )


def sent(stream: FileStream, deadline_seconds: float = 0.0) -> list[bytes]:
    """What ``stream`` sends, a part at a time, once it begins."""
    assert stream.begin(monotonic() + deadline_seconds)
    return list(stream.chunks())


def test_a_whole_file_is_sent_a_part_at_a_time(reader: PartReader, store: CasStore) -> None:
    hold(store, *PARTS)

    stream = reader.stream(entry_of(*PARTS))

    assert stream.length_bytes == len(CONTENT)
    assert sent(stream) == list(PARTS)


def test_each_part_read_is_reported_as_this_nodes_own_request(
    reader: PartReader, store: CasStore, published: Recorder
) -> None:
    hold(store, *PARTS)

    sent(reader.stream(entry_of(*PARTS)))

    assert published.messages == [
        (EventType.DATA_REQUESTED, {**id_of(part).fields(), "external": False}) for part in PARTS
    ]


@mark.parametrize(
    "start, stop, expected, read",
    [
        (1, 3, [b"aa"], [b"aaaa"]),
        (2, 9, [b"aa", b"bbbb", b"c"], list(PARTS)),
        (4, 8, [b"bbbb"], [b"bbbb"]),
        (8, 10, [b"cc"], [b"cc"]),
        (9, None, [b"c"], [b"cc"]),
        (0, 10, list(PARTS), list(PARTS)),
        (5, 5, [], []),
        (10, 10, [], []),
    ],
)
def test_a_span_reads_only_the_parts_holding_it(
    reader: PartReader,
    store: CasStore,
    published: Recorder,
    start: int,
    stop: int | None,
    expected: list[bytes],
    read: list[bytes],
) -> None:
    hold(store, *PARTS)

    stream = reader.stream(entry_of(*PARTS), start_bytes=start, stop_bytes=stop)

    assert stream.length_bytes == len(b"".join(expected))
    assert sent(stream) == expected
    assert published.ids(EventType.DATA_REQUESTED) == [id_of(part) for part in read]


def test_a_span_ending_or_starting_beside_a_part_of_no_bytes_does_not_read_it(
    reader: PartReader, store: CasStore, published: Recorder
) -> None:
    parts = (b"aaaa", b"", b"bbbb")
    hold(store, *parts)

    first = sent(reader.stream(entry_of(*parts), start_bytes=0, stop_bytes=4))
    second = sent(reader.stream(entry_of(*parts), start_bytes=4, stop_bytes=8))

    assert (first, second) == ([b"aaaa"], [b"bbbb"])
    assert published.ids(EventType.DATA_REQUESTED) == [id_of(b"aaaa"), id_of(b"bbbb")]


def test_the_whole_file_reads_its_parts_of_no_bytes_too(
    reader: PartReader, store: CasStore
) -> None:
    parts = (b"aaaa", b"", b"bbbb")
    hold(store, *parts)

    assert sent(reader.stream(entry_of(*parts))) == list(parts)


@mark.parametrize("start, stop", [(-1, 4), (3, 2), (0, 11), (11, None)])
def test_a_span_must_lie_within_the_file(reader: PartReader, start: int, stop: int | None) -> None:
    with raises(ValueError, match="within the file's 10 bytes"):
        reader.stream(entry_of(*PARTS), start_bytes=start, stop_bytes=stop)


@mark.parametrize("start, stop", [(1, None), (0, 10)])
def test_a_file_recording_no_part_sizes_is_read_only_whole(
    reader: PartReader, start: int, stop: int | None
) -> None:
    with raises(ValueError, match="does not record"):
        reader.stream(entry_of(*PARTS, sizes=False), start_bytes=start, stop_bytes=stop)


@mark.parametrize("size, length", [(True, len(CONTENT)), (False, None)])
def test_a_file_recording_no_part_sizes_is_sent_whole_in_turn(
    reader: PartReader, store: CasStore, size: bool, length: int | None
) -> None:
    hold(store, *PARTS)

    stream = reader.stream(entry_of(*PARTS, sizes=False, size=size))

    assert stream.length_bytes == length
    assert sent(stream) == list(PARTS)


def test_encrypted_parts_are_sent_decrypted(reader: PartReader, store: CasStore) -> None:
    writer = PartWriter(store, encrypted=True)
    paths = [writer.store(part) for part in PARTS]
    entry = FileBundle(
        tuple(str(path) for path in paths),
        Metadata(size_bytes=len(CONTENT), algorithm="sha256", hash=sha256(CONTENT).hexdigest()),
        part_sizes_bytes=tuple(len(part) for part in PARTS),
    )

    assert b"".join(sent(reader.stream(entry, start_bytes=3))) == CONTENT[3:]


def test_an_empty_file_sends_nothing(reader: PartReader) -> None:
    stream = reader.stream(entry_of())

    assert stream.length_bytes == 0
    assert sent(stream) == []


def test_an_empty_file_not_matching_its_hash_fails_at_once(reader: PartReader) -> None:
    with raises(BundleVerificationError, match="whole-file hash"):
        reader.stream(entry_of(whole=b"not empty", size=False))


@mark.parametrize(
    "parts, error",
    [
        (("md5/" + "0" * 32,), UnsupportedBundleError),
        (("not a path",), MalformedBundleError),
    ],
)
def test_a_part_this_node_cannot_read_fails_before_anything_is_read(
    reader: PartReader, parts: tuple[str, ...], error: type[Exception]
) -> None:
    with raises(error):
        reader.stream(FileBundle(parts))


def test_a_whole_file_hash_this_node_cannot_check_fails_before_anything_is_read(
    reader: PartReader,
) -> None:
    entry = FileBundle((str(id_of(b"aaaa")),), Metadata(algorithm="md5", hash="0" * 32))

    with raises(UnsupportedBundleError, match="Whole-file hash"):
        reader.stream(entry)


def test_the_first_part_is_asked_for_with_those_after_it_in_order(
    store: CasStore, published: Recorder
) -> None:
    parts = tuple(bytes([letter]) * 4 for letter in b"abcdefgh")
    hold(store, parts[1])
    reader = reader_for(store, published, read_ahead_parts=4)

    begun = reader.stream(entry_of(*parts)).begin(monotonic())

    assert not begun
    # The second part is held, and the sixth on lie past the read-ahead.
    assert published.ids(EventType.DATA_NOT_FOUND) == [id_of(parts[i]) for i in (0, 2, 3, 4)]
    assert published.ids(EventType.DATA_REQUESTED) == []


def test_more_parts_are_asked_for_as_the_response_moves_on(
    store: CasStore, published: Recorder
) -> None:
    parts = tuple(bytes([letter]) * 4 for letter in b"abcdef")
    hold(store, *parts[:3])
    reader = reader_for(store, published, read_ahead_parts=2)
    stream = reader.stream(entry_of(*parts))
    chunks = stream.chunks()

    assert stream.begin(monotonic())
    assert next(chunks) == parts[0]
    assert published.ids(EventType.DATA_NOT_FOUND) == []

    assert next(chunks) == parts[1]
    assert published.ids(EventType.DATA_NOT_FOUND) == [id_of(parts[3])]

    # The fourth part was asked for just now, so only the fifth is.
    assert next(chunks) == parts[2]
    assert published.ids(EventType.DATA_NOT_FOUND) == [id_of(parts[3]), id_of(parts[4])]


def test_a_part_still_not_held_is_asked_for_again_only_once_due(
    store: CasStore, published: Recorder
) -> None:
    waits = reader_for(store, published, wait_seconds=0.1, read_ahead_parts=0)
    again = reader_for(store, published, wait_seconds=0.1, read_ahead_parts=0, ask_again_seconds=0)

    assert not waits.stream(entry_of(b"aaaa")).begin(monotonic() + 0.1)
    asked_once = published.ids(EventType.DATA_NOT_FOUND)
    assert not again.stream(entry_of(b"aaaa")).begin(monotonic() + 0.1)

    assert asked_once == [id_of(b"aaaa")]
    assert len(published.ids(EventType.DATA_NOT_FOUND)) > 3


def test_a_part_arriving_during_the_wait_is_sent(reader: PartReader, store: CasStore) -> None:
    hold(store, b"bbbb", b"cc")
    arrival = Timer(0.05, hold, (store, b"aaaa"))
    arrival.start()

    stream = reader.stream(entry_of(*PARTS))
    begun = stream.begin(monotonic() + 5)
    arrival.join()

    assert begun
    assert list(stream.chunks()) == list(PARTS)


def test_a_later_part_arriving_during_the_wait_is_sent(
    store: CasStore, published: Recorder
) -> None:
    hold(store, b"aaaa", b"bbbb")
    stream = reader_for(store, published, wait_seconds=5).stream(entry_of(*PARTS))
    arrival = Timer(0.05, hold, (store, b"cc"))

    assert stream.begin(monotonic())
    arrival.start()
    chunks = list(stream.chunks())
    arrival.join()

    assert chunks == list(PARTS)


def test_a_later_part_never_arriving_cuts_the_response_short(
    store: CasStore, published: Recorder, caplog: LogCaptureFixture
) -> None:
    hold(store, b"aaaa", b"cc")
    stream = reader_for(store, published, wait_seconds=0.05).stream(entry_of(*PARTS))
    chunks = stream.chunks()

    with caplog.at_level(INFO):
        first = next(chunks)

        with raises(ResponseCutShortError, match="did not arrive"):
            next(chunks)

    assert first == b"aaaa"
    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.file_stream"]
    assert record.levelno == INFO
    assert record.getMessage() == (
        f"Part {id_of(b'bbbb')} did not arrive within 0.05 seconds, " "so the response is cut short"
    )


def test_a_first_part_of_the_wrong_size_fails_before_the_response_begins(
    reader: PartReader, store: CasStore
) -> None:
    hold(store, *PARTS)
    entry = FileBundle(tuple(str(id_of(part)) for part in PARTS), part_sizes_bytes=(3, 5, 2))

    with raises(BundleVerificationError, match="larger than its size"):
        reader.stream(entry).begin(monotonic())


def test_a_later_part_of_the_wrong_size_cuts_the_response_short(
    reader: PartReader, store: CasStore, caplog: LogCaptureFixture
) -> None:
    hold(store, *PARTS)
    entry = FileBundle(tuple(str(id_of(part)) for part in PARTS), part_sizes_bytes=(4, 5, 1))
    chunks = reader.stream(entry).chunks()

    assert next(chunks) == b"aaaa"

    with caplog.at_level(WARNING), raises(ResponseCutShortError, match="not its size"):
        next(chunks)

    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(
        f"Part {id_of(b'bbbb')} cannot be sent, so the response is cut short: "
    )


def test_a_whole_file_failing_its_hash_holds_its_last_part_back(
    reader: PartReader, store: CasStore
) -> None:
    hold(store, *PARTS)
    chunks = reader.stream(entry_of(*PARTS, whole=CONTENT.upper())).chunks()

    assert [next(chunks), next(chunks)] == [b"aaaa", b"bbbb"]

    with raises(ResponseCutShortError, match="whole-file hash"):
        next(chunks)


def test_a_single_part_file_failing_its_hash_fails_before_the_response_begins(
    reader: PartReader, store: CasStore
) -> None:
    hold(store, b"aaaa")

    with raises(BundleVerificationError, match="whole-file hash"):
        reader.stream(entry_of(b"aaaa", whole=b"AAAA")).begin(monotonic())


def test_a_span_of_a_file_failing_its_hash_is_sent_on_its_parts_checks_alone(
    reader: PartReader, store: CasStore
) -> None:
    hold(store, *PARTS)

    assert sent(reader.stream(entry_of(*PARTS, whole=CONTENT.upper()), start_bytes=1)) == [
        b"aaa",
        b"bbbb",
        b"cc",
    ]


def test_a_file_without_sizes_running_past_its_size_is_cut_short_before_the_excess(
    reader: PartReader, store: CasStore
) -> None:
    hold(store, *PARTS)
    entry = FileBundle(tuple(str(id_of(part)) for part in PARTS), Metadata(size_bytes=6))
    chunks = reader.stream(entry).chunks()

    assert next(chunks) == b"aaaa"

    with raises(ResponseCutShortError, match="larger than its size"):
        next(chunks)


@mark.parametrize(
    "settings, message",
    [
        ({"wait_seconds": -1}, "wait_seconds"),
        ({"ask_again_seconds": -1}, "ask_again_seconds"),
        ({"read_ahead_parts": -1}, "read_ahead_parts"),
        ({"poll_interval_seconds": 0}, "poll_interval_seconds"),
    ],
)
def test_a_reader_refuses_settings_it_cannot_work_with(
    store: CasStore, published: Recorder, settings: dict[str, float], message: str
) -> None:
    values: dict[str, Any] = {"wait_seconds": 0, "ask_again_seconds": 0, **settings}

    with raises(ValueError, match=message):
        PartReader(store, published, **values)
