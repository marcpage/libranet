"""Tests for each application's store, the files it is kept in, and its endpoints."""

from __future__ import annotations
from dataclasses import replace
from hashlib import sha256
from json import dumps, loads
from logging import WARNING
from pathlib import Path
from threading import Thread

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.config.models import DEFAULT_CONFIG_HOSTS, StorageConfig
from libranet.problems import CONTENT_TOO_LARGE, INVALID_CONFIG_REQUEST, PROBLEM_CONTENT_TYPE
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.webserver.app_registry import ApplicationRegistry, RegisteredApplications
from libranet.webserver.app_store import (
    MAX_STORE_BYTES,
    MAX_VALUE_BYTES,
    STORE_KEY_PATTERN,
    STORE_PATTERN,
    ApplicationStore,
    IfMatch,
    StoreHandler,
    StoreRemovalHandler,
    StoreValueHandler,
    StoreWriteHandler,
    StoredValue,
    StoredValues,
)
from libranet.webserver.errors import StoreFileError, StoreLimitError, ValueChangedError
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.local_only import LocalOnly
from libranet.webserver.own_pages import OwnPages
from libranet.webserver.router import Router
from libranet.webserver.site_checks import SiteChecks

PLAYLISTS = [{"name": "Family", "bundle": "sha256/" + "a" * 64}]
LOCAL = "127.0.0.1"
REMOTE = "203.0.113.42"
HOST = "localhost:8080"
# The applications whose pages ask for their stores.
APPLICATIONS = RegisteredApplications(
    dict.fromkeys(("/", "movie", "mövie", "wiki"), ContentId.for_data(b"a page", "sha256"))
)


@fixture
def directory(tmp_path: Path) -> Path:
    return StorageConfig(data_dir=tmp_path / "data").application_stores_dir


@fixture
def store(directory: Path) -> ApplicationStore:
    return ApplicationStore(directory)


@fixture
def router(store: ApplicationStore, tmp_path: Path) -> Router:
    """The store's routes, changed only by local clients, as the main port serves them."""
    checks = SiteChecks(DEFAULT_CONFIG_HOSTS, "This endpoint")
    pages = OwnPages(ApplicationRegistry(tmp_path / "applications.json", APPLICATIONS))
    router = Router()
    router.add("GET", STORE_PATTERN, StoreHandler(store, pages))
    router.add("GET", STORE_KEY_PATTERN, StoreValueHandler(store, pages))
    router.add("PUT", STORE_KEY_PATTERN, LocalOnly(StoreWriteHandler(store, pages), checks))
    router.add("DELETE", STORE_KEY_PATTERN, LocalOnly(StoreRemovalHandler(store, pages), checks))
    return router


def kept(value: object) -> StoredValue:
    return StoredValue.of(value)


def saved(path: Path, value: object) -> None:
    """Write a store file as an administrator editing it by hand might."""
    write_atomically(path, dumps(value).encode("utf-8"))


def request(
    method: str,
    path: str,
    *,
    value: object = None,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    client_address: str = LOCAL,
) -> Request:
    """A request for ``path``; a ``PUT`` carries ``value`` as JSON unless ``body`` is given.

    It is from a page of the application whose store ``path`` names, as
    ``Referer`` says, unless ``headers`` say otherwise.
    """
    if body is None:
        body = dumps(value).encode("utf-8") if method == "PUT" else b""

    page = f"http://{HOST}/{path.split('/')[3]}/"
    return Request(
        method,
        path,
        headers={
            "Content-Type": JSON_CONTENT_TYPE,
            "Host": HOST,
            "Referer": page,
            **(headers or {}),
        },
        client_address=client_address,
        body=RequestBody.of(body),
    )


def problem(response: Response, status: int) -> dict[str, object]:
    assert response.status == status
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    found: dict[str, object] = loads(response.body)
    return found


def test_the_stores_are_kept_under_the_data_directory(tmp_path: Path) -> None:
    storage = StorageConfig(data_dir=tmp_path / "data")

    assert storage.application_stores_dir == tmp_path / "data" / "store"


def test_an_application_keeps_nothing_until_a_value_is_put(
    store: ApplicationStore, directory: Path
) -> None:
    assert store.values("movie") == StoredValues("movie")
    assert not directory.exists()


def test_a_value_put_is_saved_under_the_hash_of_the_name_and_read_back(
    store: ApplicationStore, directory: Path
) -> None:
    held = store.put("movie", "playlists", kept(PLAYLISTS))

    path = directory / f"{sha256(b'movie').hexdigest()}.json"
    assert not held
    assert store.path("movie") == path
    assert loads(path.read_bytes()) == {"application": "movie", "values": {"playlists": PLAYLISTS}}
    assert store.values("movie").values == {"playlists": kept(PLAYLISTS)}
    assert ApplicationStore(directory).values("movie") == store.values("movie")


def test_a_value_put_again_replaces_the_one_held(store: ApplicationStore) -> None:
    store.put("movie", "playlists", kept(PLAYLISTS))

    held = store.put("movie", "playlists", kept([]))

    assert held
    assert store.values("movie").values == {"playlists": kept([])}


def test_each_application_keeps_its_own_values(store: ApplicationStore) -> None:
    store.put("movie", "last", kept(1))
    store.put("/", "last", kept(2))

    assert store.values("movie").values == {"last": kept(1)}
    assert store.values("/").values == {"last": kept(2)}
    assert store.path("movie") != store.path("/")


@mark.parametrize("name", ["Movie", "MOVIE", "movie"])
def test_names_differing_only_in_case_share_a_store(store: ApplicationStore, name: str) -> None:
    store.put("Movie", "last", kept(1))

    assert store.values(name).values == {"last": kept(1)}
    assert store.path(name) == store.path("movie")


@mark.parametrize("name", ["data", "web", "", "..", "a/b", "a\0b"])
def test_a_name_no_application_could_have_has_no_store(store: ApplicationStore, name: str) -> None:
    with raises(ValueError):
        store.values(name)

    with raises(ValueError):
        store.put(name, "last", kept(1))


def test_keys_are_kept_as_they_are_cased(store: ApplicationStore) -> None:
    store.put("movie", "Last", kept(1))
    store.put("movie", "last", kept(2))

    assert store.values("movie").values == {"Last": kept(1), "last": kept(2)}


def test_a_value_deleted_is_kept_no_longer(store: ApplicationStore) -> None:
    store.put("movie", "playlists", kept(PLAYLISTS))
    store.put("movie", "last", kept(1))

    removed = store.delete("movie", "playlists")

    assert removed
    assert store.values("movie").values == {"last": kept(1)}


def test_a_store_left_holding_nothing_has_no_file(store: ApplicationStore) -> None:
    store.put("movie", "last", kept(1))

    store.delete("movie", "last")

    assert not store.path("movie").exists()
    assert store.values("movie") == StoredValues("movie")


def test_deleting_a_key_not_held_leaves_the_file_alone(store: ApplicationStore) -> None:
    store.put("movie", "last", kept(1))
    before = store.path("movie").stat().st_mtime_ns

    removed = store.delete("movie", "missing")

    assert not removed
    assert store.path("movie").stat().st_mtime_ns == before
    assert not store.delete("other", "missing")


def test_the_file_is_read_again_when_it_changes(store: ApplicationStore) -> None:
    store.put("movie", "last", kept(1))
    store.values("movie")

    saved(store.path("movie"), {"application": "movie", "values": {"last": 2}})

    assert store.values("movie").values == {"last": kept(2)}


def test_a_file_removed_by_hand_leaves_nothing_kept(store: ApplicationStore) -> None:
    store.put("movie", "last", kept(1))
    store.values("movie")

    store.path("movie").unlink()

    assert store.values("movie") == StoredValues("movie")


@mark.parametrize(
    "contents",
    [
        b"{not json",
        b"[]",
        b'{"values": {}}',
        b'{"application": "movie"}',
        b'{"application": "movie", "values": []}',
        b'{"application": "movie", "values": {"last": NaN}}',
        b'{"application": "data", "values": {}}',
        b'{"application": "Movie", "values": {}}',
        b'{"application": "wiki", "values": {}}',
    ],
)
def test_a_file_not_holding_the_store_is_an_error_and_never_saved_over(
    store: ApplicationStore, contents: bytes
) -> None:
    write_atomically(store.path("movie"), contents)

    with raises(StoreFileError):
        store.values("movie")

    with raises(StoreFileError):
        store.put("movie", "last", kept(1))

    with raises(StoreFileError):
        store.delete("movie", "last")

    assert store.path("movie").read_bytes() == contents


def test_a_file_that_cannot_be_looked_at_is_an_error(store: ApplicationStore) -> None:
    # A file within a path that is not a directory cannot be looked at.
    write_atomically(store.directory, b"not a directory")

    with raises(StoreFileError, match="Cannot read"):
        store.values("movie")


def test_a_value_larger_than_a_store_allows_is_refused(store: ApplicationStore) -> None:
    largest = kept("x" * (MAX_VALUE_BYTES - 2))
    store.put("movie", "largest", largest)

    with raises(StoreLimitError, match="stored value"):
        store.put("movie", "larger", kept("x" * (MAX_VALUE_BYTES - 1)))

    assert store.values("movie").values == {"largest": largest}


def test_a_store_grown_past_its_limit_is_refused_and_left_as_it_was(
    store: ApplicationStore,
) -> None:
    value = kept("x" * (MAX_VALUE_BYTES - 2))
    count = MAX_STORE_BYTES // MAX_VALUE_BYTES - 1

    for index in range(count):
        store.put("movie", f"{index}", value)

    before = store.path("movie").read_bytes()

    with raises(StoreLimitError, match="application's store"):
        store.put("movie", "more", value)

    assert store.path("movie").read_bytes() == before
    assert len(store.values("movie").values) == count


def test_a_change_is_made_when_its_tag_matches_the_value_held(store: ApplicationStore) -> None:
    store.put("movie", "last", kept(1))
    if_match = IfMatch.parse(f'"other", {kept(1).tag()}')

    held = store.put("movie", "last", kept(2), if_match=if_match)
    removed = store.delete("movie", "last", if_match=IfMatch.parse(kept(2).tag()))

    assert held
    assert removed


@mark.parametrize("header", ['"sha256-other"', f"W/{StoredValue.of(1).tag()}", ""])
def test_a_change_whose_tag_does_not_match_is_refused(store: ApplicationStore, header: str) -> None:
    store.put("movie", "last", kept(1))

    with raises(ValueChangedError, match="now"):
        store.put("movie", "last", kept(2), if_match=IfMatch.parse(header))

    with raises(ValueChangedError):
        store.delete("movie", "last", if_match=IfMatch.parse(header))

    assert store.values("movie").values == {"last": kept(1)}


def test_any_tag_matches_any_value_held_and_none_not_held(store: ApplicationStore) -> None:
    any_value = IfMatch.parse(" * ")

    with raises(ValueChangedError, match="No value"):
        store.put("movie", "last", kept(1), if_match=any_value)

    with raises(ValueChangedError):
        store.delete("movie", "last", if_match=any_value)

    store.put("movie", "last", kept(1))

    assert store.put("movie", "last", kept(2), if_match=any_value)
    assert store.delete("movie", "last", if_match=any_value)


def test_one_value_has_one_form_and_one_tag_however_it_was_written() -> None:
    first = StoredValue.of(loads('{"b": [1, 2], "a": {"y": null, "x": "é"}}'))
    second = StoredValue.of(loads('{"a":{"x":"\\u00e9","y":null},"b":[1,2]}'))

    assert first == second
    assert first.text == b'{"a":{"x":"\\u00e9","y":null},"b":[1,2]}'
    assert first.tag() == f'"sha256-{sha256(first.text).hexdigest()}"'
    assert first.value() == {"a": {"x": "é", "y": None}, "b": [1, 2]}
    assert StoredValue.of(1).tag() != StoredValue.of("1").tag()


@mark.parametrize("value", [float("nan"), [float("inf")], {"x": -float("inf")}])
def test_a_value_json_has_no_form_for_is_refused(value: object) -> None:
    with raises(ValueError):
        StoredValue.of(value)


def test_values_put_at_once_are_all_kept(store: ApplicationStore) -> None:
    keys = [f"key-{index}" for index in range(20)]
    threads = [Thread(target=store.put, args=("movie", key, kept(key))) for key in keys]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert sorted(store.values("movie").values) == sorted(keys)


def test_a_store_read_whole_lists_every_value(router: Router) -> None:
    router.dispatch(request("PUT", "/data/store/movie/playlists", value=PLAYLISTS))
    router.dispatch(request("PUT", "/data/store/movie/last", value=None))

    response = router.dispatch(request("GET", "/data/store/movie"))

    assert response.status == 200
    assert response.headers["Content-Type"] == JSON_CONTENT_TYPE
    assert response.headers["Cache-Control"] == "no-cache"
    assert "ETag" not in response.headers
    assert loads(response.body) == {"values": {"playlists": PLAYLISTS, "last": None}}


def test_a_store_never_written_lists_no_values(router: Router) -> None:
    response = router.dispatch(request("GET", "/data/store/movie"))

    assert response.status == 200
    assert loads(response.body) == {"values": {}}


def test_a_value_put_is_created_then_replaced_and_read_back_with_its_tag(router: Router) -> None:
    created = router.dispatch(request("PUT", "/data/store/movie/playlists", value=PLAYLISTS))
    replaced = router.dispatch(request("PUT", "/data/store/movie/playlists", value=[]))
    read = router.dispatch(request("GET", "/data/store/movie/playlists"))

    assert created.status == 201
    assert replaced.status == 204
    assert "ETag" not in created.headers
    assert "ETag" not in replaced.headers
    assert read.status == 200
    assert read.headers["Content-Type"] == JSON_CONTENT_TYPE
    assert read.headers["ETag"] == kept([]).tag()
    assert read.headers["Cache-Control"] == "no-cache"
    assert read.body == b"[]"


def test_a_key_not_held_is_404(router: Router) -> None:
    found = problem(router.dispatch(request("GET", "/data/store/movie/missing")), 404)

    assert found["instance"] == "/data/store/movie/missing"


@mark.parametrize(
    "path, application, key",
    [
        ("/data/store/Movie/Last", "movie", "Last"),
        ("/data/store/%2F/last", "/", "last"),
        ("/data/store/%2f/a%2Fb", "/", "a/b"),
        ("/data/store/M%C3%B6vie/caf%C3%A9", "mövie", "café"),
    ],
)
def test_names_are_percent_decoded_and_the_application_case_folded(
    router: Router, store: ApplicationStore, path: str, application: str, key: str
) -> None:
    response = router.dispatch(request("PUT", path, value=1))

    assert response.status == 201
    assert store.values(application).values == {key: kept(1)}


UNUSABLE_KEY_PATHS = [
    "/data/store/data/last",
    "/data/store/%2e%2e/last",
    "/data/store/a%2Fb/last",
    "/data/store/a%00b/last",
    "/data/store/%FF/last",
    "/data/store/movie/%FF",
]


@mark.parametrize("path", ["/data/store/data", "/data/store/%FF", *UNUSABLE_KEY_PATHS])
def test_a_name_no_store_could_have_is_404_to_read(router: Router, path: str) -> None:
    problem(router.dispatch(request("GET", path)), 404)


@mark.parametrize("path", UNUSABLE_KEY_PATHS)
@mark.parametrize("method", ["PUT", "DELETE"])
def test_a_name_no_store_could_have_is_404_to_change(
    router: Router, store: ApplicationStore, path: str, method: str
) -> None:
    problem(router.dispatch(request(method, path, value=1)), 404)

    assert not store.directory.exists()


def test_a_change_with_a_matching_tag_is_made(router: Router) -> None:
    router.dispatch(request("PUT", "/data/store/movie/last", value=1))
    tag = kept(1).tag()

    replaced = router.dispatch(
        request("PUT", "/data/store/movie/last", value=2, headers={"If-Match": tag})
    )
    removed = router.dispatch(
        request("DELETE", "/data/store/movie/last", headers={"If-Match": kept(2).tag()})
    )

    assert replaced.status == 204
    assert removed.status == 204


@mark.parametrize("method", ["PUT", "DELETE"])
def test_a_change_with_a_tag_not_matching_is_412_and_not_made(
    router: Router, store: ApplicationStore, method: str
) -> None:
    router.dispatch(request("PUT", "/data/store/movie/last", value=1))

    response = router.dispatch(
        request(method, "/data/store/movie/last", value=2, headers={"If-Match": '"stale"'})
    )

    found = problem(response, 412)
    assert kept(1).tag() in str(found["detail"])
    assert store.values("movie").values == {"last": kept(1)}


@mark.parametrize("method", ["PUT", "DELETE"])
def test_a_change_matching_any_value_is_412_for_a_key_not_held(
    router: Router, store: ApplicationStore, method: str
) -> None:
    response = router.dispatch(
        request(method, "/data/store/movie/last", value=1, headers={"If-Match": "*"})
    )

    problem(response, 412)
    assert store.values("movie") == StoredValues("movie")


def test_a_change_matching_any_value_is_made_to_a_key_held(router: Router) -> None:
    router.dispatch(request("PUT", "/data/store/movie/last", value=1))

    response = router.dispatch(
        request("PUT", "/data/store/movie/last", value=2, headers={"If-Match": "*"})
    )

    assert response.status == 204


def test_deleting_a_key_not_held_is_404(router: Router) -> None:
    problem(router.dispatch(request("DELETE", "/data/store/movie/last")), 404)


def test_a_value_deleted_is_read_no_longer(router: Router) -> None:
    router.dispatch(request("PUT", "/data/store/movie/last", value=1))

    removed = router.dispatch(request("DELETE", "/data/store/movie/last"))
    read = router.dispatch(request("GET", "/data/store/movie/last"))

    assert removed.status == 204
    assert removed.body == b""
    assert read.status == 404


@mark.parametrize("body", [b"{not json", b"NaN", b"[Infinity]", b""])
def test_a_body_carrying_no_json_value_is_400(
    router: Router, store: ApplicationStore, body: bytes
) -> None:
    found = problem(router.dispatch(request("PUT", "/data/store/movie/last", body=body)), 400)

    assert found["type"] == INVALID_CONFIG_REQUEST
    assert store.values("movie") == StoredValues("movie")


def test_a_body_of_another_type_is_415(router: Router, store: ApplicationStore) -> None:
    sent = request(
        "PUT",
        "/data/store/movie/last",
        body=b"last=1",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    problem(router.dispatch(sent), 415)

    assert store.values("movie") == StoredValues("movie")


@mark.parametrize("method", ["PUT", "DELETE"])
def test_a_body_larger_than_a_value_is_413_unread(router: Router, method: str) -> None:
    body = dumps(" " * MAX_VALUE_BYTES).encode("utf-8")
    sent = request(method, "/data/store/movie/last", body=body)

    found = problem(router.dispatch(sent), 413)

    assert found["type"] == CONTENT_TOO_LARGE
    assert not sent.body.consumed


def test_a_value_escaped_past_its_limit_is_413(router: Router) -> None:
    # Each é is two bytes as sent, and six once escaped as it is kept.
    body = dumps("é" * (MAX_VALUE_BYTES // 4), ensure_ascii=False).encode("utf-8")

    found = problem(router.dispatch(request("PUT", "/data/store/movie/last", body=body)), 413)

    assert found["type"] == CONTENT_TOO_LARGE
    assert "stored value" in str(found["detail"])


@mark.parametrize("path", ["/data/store/movie", "/data/store/movie/last"])
def test_a_remote_client_reads_a_store(router: Router, path: str) -> None:
    router.dispatch(request("PUT", "/data/store/movie/last", value=1))

    response = router.dispatch(request("GET", path, client_address=REMOTE))

    assert response.status == 200


@mark.parametrize("method", ["PUT", "DELETE"])
def test_a_remote_client_or_another_sites_page_changes_no_store(
    router: Router, store: ApplicationStore, method: str
) -> None:
    router.dispatch(request("PUT", "/data/store/movie/last", value=1))

    remote = router.dispatch(
        request(method, "/data/store/movie/last", value=2, client_address=REMOTE)
    )
    cross_site = router.dispatch(
        request(method, "/data/store/movie/last", value=2, headers={"Sec-Fetch-Site": "cross-site"})
    )

    problem(remote, 403)
    problem(cross_site, 403)
    assert store.values("movie").values == {"last": kept(1)}


@mark.parametrize(
    "method, path",
    [
        ("GET", "/data/store/movie"),
        ("GET", "/data/store/movie/last"),
        ("PUT", "/data/store/movie/last"),
        ("DELETE", "/data/store/movie/last"),
    ],
)
def test_a_store_file_that_cannot_be_read_is_500_without_naming_it(
    router: Router, store: ApplicationStore, caplog: LogCaptureFixture, method: str, path: str
) -> None:
    write_atomically(store.path("movie"), b"{not json")

    with caplog.at_level(WARNING):
        found = problem(router.dispatch(request(method, path, value=1)), 500)

    assert str(store.path("movie")) not in str(found["detail"])
    assert str(store.path("movie")) in caplog.text
    assert store.path("movie").read_bytes() == b"{not json"


@mark.parametrize(
    "method, path",
    [
        ("GET", "/data/store/movie"),
        ("GET", "/data/store/movie/last"),
        ("PUT", "/data/store/movie/last"),
        ("DELETE", "/data/store/movie/last"),
    ],
)
@mark.parametrize(
    "referer, named",
    [
        (None, "carries no Referer"),
        (f"http://{HOST}/wiki/", "'wiki'"),
        (f"http://{HOST}/", "'/'"),
        (f"http://{HOST}/films/", "'/'"),
        (f"http://{HOST}/config/", "no application's page"),
        (f"http://{HOST}/data/sha256/{'a' * 64}/page.html", "no application's page"),
        ("http://localhost:9000/movie/", "localhost:9000"),
    ],
)
def test_a_store_is_served_only_to_its_own_applications_pages(
    router: Router,
    store: ApplicationStore,
    caplog: LogCaptureFixture,
    method: str,
    path: str,
    referer: str | None,
    named: str,
) -> None:
    router.dispatch(request("PUT", "/data/store/movie/last", value=1))
    sent = request(method, path, value=2)
    headers = {name: value for name, value in sent.headers.items() if name != "Referer"}

    with caplog.at_level(WARNING):
        response = router.dispatch(
            replace(sent, headers=headers if referer is None else {**headers, "Referer": referer})
        )

    assert named in str(problem(response, 403)["detail"])
    assert store.values("movie").values == {"last": kept(1)}
    assert f"Refusing {method} {path}: " in caplog.text


def test_the_root_applications_store_is_its_pages_whatever_their_path(router: Router) -> None:
    for page in ("", "/", "/index.html", "/films/"):
        headers = {"Referer": f"http://{HOST}{page}"}

        response = router.dispatch(request("PUT", "/data/store/%2F/last", value=1, headers=headers))

        assert response.status in (201, 204)
