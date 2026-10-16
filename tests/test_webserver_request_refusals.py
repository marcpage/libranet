"""Tests for the responses more than one handler gives in place of what was asked for."""

from __future__ import annotations
from http import HTTPStatus
from json import loads
from logging import DEBUG, WARNING, getLogger

from pytest import LogCaptureFixture, mark

from libranet.bundle.errors import BundleError, MalformedBundleError, UnsupportedBundleError
from libranet.problems import INVALID_REQUEST, UNUSABLE_BUNDLE
from libranet.webserver.http_types import Request
from libranet.webserver.request_refusals import invalid_request_response, unusable_bundle_response

_LOGGER = getLogger("test_refusals")


@mark.parametrize(
    ("error", "level"),
    [
        (UnsupportedBundleError("an algorithm this node lacks"), WARNING),
        (MalformedBundleError("not a bundle"), DEBUG),
    ],
)
def test_an_unusable_bundle_is_refused_and_logged_as_its_kind_calls_for(
    caplog: LogCaptureFixture, error: BundleError, level: int
) -> None:
    caplog.set_level(DEBUG)

    response = unusable_bundle_response(Request("GET", "/data/signed/x"), error, _LOGGER)

    assert response.status == HTTPStatus.BAD_REQUEST
    assert loads(response.body)["type"] == UNUSABLE_BUNDLE
    assert loads(response.body)["detail"] == str(error)
    assert caplog.record_tuples == [
        ("test_refusals", level, f"Refusing GET /data/signed/x: {error}")
    ]


def test_a_body_that_cannot_be_acted_on_is_refused_and_logged_at_debug(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG)
    error = ValueError('"seconds" may be at most 1 here, got 2')

    response = invalid_request_response(Request("POST", "/data/drop"), error, _LOGGER)

    assert response.status == HTTPStatus.BAD_REQUEST
    assert loads(response.body)["type"] == INVALID_REQUEST
    assert loads(response.body)["detail"] == str(error)
    assert caplog.record_tuples == [("test_refusals", DEBUG, f"Refusing POST /data/drop: {error}")]
