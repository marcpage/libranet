"""Stakes: what a validation block is a collection of (KarmaLedger §6.2).

A stake is a signed bundle (BundleSpecification §5), signed by its staker,
whose ``contents`` is the JSON object::

    {"staker": "sha256/0a1b…", "transaction_block": "sha256/…",
     "kismet": "1000000000000000", "created": "2026-10-21T14:03:22Z"}

It stakes ``kismet`` on the transaction block it names, so it cannot be made
before that block exists. ``created`` lets one staker put up several stakes
of one amount, and two that one staker makes of one amount in the same
second are one stake. A stake is known by its ``hash``, as a transaction is
(§4.2). It names its ``staker``, though its signer names them too, so that
two stakers' stakes of one amount, made in one second, have two hashes.

Nothing here reads the chain: whether a staker's stakes are covered (§6.3)
is for whoever checks the validation block that holds them.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.json_format import MOMENT_FORMAT, compact_json
from libranet.karma.amounts import Kismet
from libranet.karma.members import (
    CREATED_MEMBER,
    check_moment,
    contents_value,
    id_member,
    moment_member,
)

# The members of a stake besides when it was made (§6.2).
_STAKER: Final = "staker"
_TRANSACTION_BLOCK: Final = "transaction_block"
_KISMET: Final = "kismet"


@dataclass(frozen=True)
class Stake:
    """``staker`` staking ``kismet`` on the transaction block ``transaction_block`` (§6.2).

    They staked it at ``created``.

    Raises:
        ValueError: ``created`` is not a UTC time to the second.
    """

    staker: ContentId
    transaction_block: ContentId
    kismet: Kismet
    created: datetime

    def __post_init__(self) -> None:
        check_moment(self.created, named=f'A stake\'s "{CREATED_MEMBER}"')

    @classmethod
    def from_value(cls, value: object, *, named: str = "A stake") -> Stake:
        """The stake ``value``, the decoded ``contents`` of a signed bundle, is (§6.2).

        Members it does not know are passed over.

        Raises:
            UnknownAlgorithmError: it names its staker or its transaction
                block under a hash algorithm this node lacks.
            ValueError: ``value``, called ``named``, is not an object, or a
                member of it is missing or is not what it must be, as the
                error names.
        """
        if not isinstance(value, dict):
            raise ValueError(f"{named} must be a JSON object, got {type(value).__name__}")

        return cls(
            id_member(value.get(_STAKER), named=f'"{_STAKER}"'),
            id_member(value.get(_TRANSACTION_BLOCK), named=f'"{_TRANSACTION_BLOCK}"'),
            Kismet.from_value(value.get(_KISMET), named=f'"{_KISMET}"'),
            moment_member(value.get(CREATED_MEMBER), named=f'"{CREATED_MEMBER}"'),
        )

    def value(self) -> dict[str, Any]:
        """This stake as JSON (§6.2)."""
        return {
            _STAKER: str(self.staker),
            _TRANSACTION_BLOCK: str(self.transaction_block),
            _KISMET: self.kismet.value(),
            CREATED_MEMBER: self.created.strftime(MOMENT_FORMAT),
        }

    def text(self) -> str:
        """This stake as compact JSON text, as its staker signs it."""
        return compact_json(self.value()).decode("utf-8")


@dataclass(frozen=True)
class SignedStake:
    """``stake``, as its staker signed it in ``bundle``, known by its :attr:`hash` (§6.2).

    :meth:`of` and :meth:`from_value` build one, reading ``stake`` from what
    ``bundle`` signs. Its signature is checked only by :meth:`verified`,
    given its staker's key.

    Raises:
        ValueError: ``stake``'s staker is not ``bundle``'s signer.
    """

    bundle: SignedBundle
    stake: Stake

    def __post_init__(self) -> None:
        if self.stake.staker != self.bundle.signer:
            raise ValueError(
                f'A stake\'s "{_STAKER}" must be its signer, {self.bundle.signer}, '
                f"got {self.stake.staker}"
            )

    @classmethod
    def of(cls, bundle: SignedBundle) -> SignedStake:
        """The stake ``bundle`` signs.

        Raises:
            UnknownAlgorithmError: it names its staker or its transaction
                block under a hash algorithm this node lacks.
            ValueError: what it signs is no stake (:meth:`Stake.from_value`),
                or its staker is not its signer.
        """
        return cls(bundle, Stake.from_value(contents_value(bundle.contents, named="A stake")))

    @classmethod
    def from_value(cls, value: object) -> SignedStake:
        """The stake ``value``, an object of a validation block's ``stakes``, is (§6.1).

        Raises:
            MalformedBundleError: ``value`` is not a signed bundle's object
                (:meth:`SignedBundle.from_value`).
            UnsupportedBundleError: it is hashed with an algorithm other than
                SHA-256, or its signer is named under a hash algorithm this
                node lacks.
            BundleSignatureError: its ``hash`` is not the hash of its
                ``contents``.
            UnknownAlgorithmError: what it signs names its staker or its
                transaction block under a hash algorithm this node lacks.
            ValueError: what it signs is no stake, or its staker is not its
                signer.
        """
        return cls.of(SignedBundle.from_value(value))

    def value(self) -> dict[str, str]:
        """This stake as a validation block's ``stakes`` holds it: its signed bundle, an object."""
        return self.bundle.value()

    @property
    def hash(self) -> str:
        """What it is known by: the SHA-256 of what its staker signed, in lower-case hex."""
        return self.bundle.hash

    @property
    def staker(self) -> ContentId:
        """Who staked it: its signer, whom it names (§6.2)."""
        return self.bundle.signer

    def verified(self, public_key: bytes) -> None:
        """Raise unless its staker signed it, with ``public_key``, the key their id names.

        Raises:
            BundleSignatureError: ``public_key`` is not the staker's, or does
                not verify the signature (:meth:`SignedBundle.verified`).
        """
        self.bundle.verified(public_key)
