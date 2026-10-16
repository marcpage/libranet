"""Tests for the HTTP that nodes speak to one another."""

from __future__ import annotations

from libranet.cas.content_id import ContentId
from libranet.protocol.http_syntax import data_path


def test_content_is_named_beneath_data_by_its_id() -> None:
    content_id = ContentId("sha256", "ab" * 32)

    assert data_path(content_id) == f"/data/sha256/{'ab' * 32}"
