"""Tests for how much the node stakes, as ``PUT /config/api/karma`` asks, and the file keeps it."""

from __future__ import annotations
from logging import WARNING
from pathlib import Path

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.protocol.errors import InvalidRequestError
from libranet.protocol.karma_staking import STAKING_FILE, StakingRequest, StakingRule

DEFAULT_PERCENT = 10.0


@fixture
def rule(tmp_path: Path) -> StakingRule:
    return StakingRule(tmp_path / "karma", DEFAULT_PERCENT)


@mark.parametrize("percent", [0, 12.5, 100])
def test_the_share_asked_for_is_read_and_written_back(percent: float) -> None:
    asked = StakingRequest.from_value({"stake_percent": percent, "later": 1})

    assert asked == StakingRequest(percent)
    assert asked.value() == {"stake_percent": percent}


@mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {"stake_percent": "10"},
        {"stake_percent": True},
        {"stake_percent": -1},
        {"stake_percent": 100.5},
        {"stake_percent": float("nan")},
        {"stake_percent": float("inf")},
    ],
)
def test_anything_but_a_percent_from_0_to_100_is_refused(value: object) -> None:
    with raises(InvalidRequestError, match="stake_percent"):
        StakingRequest.from_value(value)


def test_the_configured_share_is_staked_until_one_is_kept(rule: StakingRule) -> None:
    assert rule.percent() == DEFAULT_PERCENT

    rule.write(2.5)

    assert rule.percent() == 2.5
    assert (rule.karma_dir / STAKING_FILE).read_text(encoding="utf-8") == (
        '{\n  "stake_percent": 2.5\n}\n'
    )


@mark.parametrize("kept", [b"{", b"[]", b'{"stake_percent": 101}', b"\xff"])
def test_a_file_that_cannot_be_read_stakes_nothing_and_is_logged(
    rule: StakingRule, caplog: LogCaptureFixture, kept: bytes
) -> None:
    rule.karma_dir.mkdir()
    (rule.karma_dir / STAKING_FILE).write_bytes(kept)

    with caplog.at_level(WARNING):
        assert rule.percent() == 0.0

    assert "so it stakes nothing until it is saved again" in caplog.text


def test_a_file_that_cannot_be_written_is_said(rule: StakingRule) -> None:
    rule.karma_dir.write_bytes(b"not a directory")

    with raises(OSError):
        rule.write(5.0)
