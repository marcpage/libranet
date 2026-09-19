"""Tests for the bundle library's errors."""

from __future__ import annotations

from libranet.bundle.errors import (
    BundleError,
    BundleTooLargeError,
    IncorrectPasswordError,
    MalformedBundleError,
    MissingContentError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.cas.content_id import ContentId


def test_missing_content_names_every_identifier() -> None:
    content_ids = (ContentId.for_data(b"one", "sha256"), ContentId.for_data(b"two", "sha256"))

    error = MissingContentError(content_ids)

    assert error.content_ids == content_ids
    assert all(str(content_id) in str(error) for content_id in content_ids)


def test_password_protected_bundle_is_an_unsupported_one() -> None:
    assert issubclass(PasswordProtectedBundleError, UnsupportedBundleError)


def test_incorrect_password_is_a_password_protected_bundle() -> None:
    assert issubclass(IncorrectPasswordError, PasswordProtectedBundleError)


def test_bundle_too_large_is_a_bundle_error() -> None:
    assert issubclass(BundleTooLargeError, BundleError)


def test_malformed_bundle_is_a_value_error() -> None:
    assert issubclass(MalformedBundleError, ValueError)
    assert issubclass(MalformedBundleError, BundleError)
