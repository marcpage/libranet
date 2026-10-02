"""Tests for this node's identity."""

from __future__ import annotations
from hashlib import sha256
from pathlib import Path

from pytest import raises

from libranet.cas.errors import UnknownAlgorithmError
from libranet.cas.store import CasStore
from libranet.config.models import IdentityConfig, LibranetConfig, StorageConfig
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity


def make_config(tmp_path: Path, **identity: object) -> LibranetConfig:
    return LibranetConfig(
        storage=StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
        identity=IdentityConfig.model_validate(identity),
    )


def test_node_id_is_the_hash_of_the_published_public_key() -> None:
    identity = NodeIdentity.from_private_key(generate_private_key(), "sha256")

    assert identity.node_id.algorithm == "sha256"
    assert identity.node_id.hash == sha256(identity.public_key).hexdigest()
    assert identity.key_id == f"sha256/{identity.node_id.hash}"


def test_unknown_hash_algorithm_is_rejected() -> None:
    with raises(UnknownAlgorithmError):
        NodeIdentity.from_private_key(generate_private_key(), "md5")


def test_repr_does_not_include_key_material() -> None:
    identity = NodeIdentity.from_private_key(generate_private_key(), "sha256")

    assert "PUBLIC KEY" not in repr(identity)
    assert "private" not in repr(identity).lower()
    assert identity.node_id.hash in repr(identity)


def test_publish_public_key_stores_it_under_the_node_id(tmp_path: Path) -> None:
    store = CasStore(tmp_path, 4)
    identity = NodeIdentity.from_private_key(generate_private_key(), "sha256")

    identity.publish_public_key(store)
    identity.publish_public_key(store)

    assert store.read(identity.node_id) == identity.public_key


def test_load_or_create_is_stable_across_runs(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    first = NodeIdentity.load_or_create(config)
    second = NodeIdentity.load_or_create(config)

    assert first.node_id == second.node_id
    assert (tmp_path / "data" / "keys" / "node_private_key.pem").is_file()


def test_load_or_create_publishes_the_public_key(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    identity = NodeIdentity.load_or_create(config)

    assert CasStore.source_of_truth(config.storage).read(identity.node_id) == identity.public_key


def test_load_or_create_honors_the_configured_key_dir(tmp_path: Path) -> None:
    config = make_config(tmp_path, key_dir=tmp_path / "elsewhere")

    NodeIdentity.load_or_create(config)

    assert (tmp_path / "elsewhere" / "node_private_key.pem").is_file()


def test_load_reads_the_identity_the_node_was_started_with(tmp_path: Path) -> None:
    config = make_config(tmp_path, key_dir=tmp_path / "elsewhere")
    started = NodeIdentity.load_or_create(config)

    assert NodeIdentity.load(config).node_id == started.node_id


def test_load_creates_nothing_when_the_node_has_no_key(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    # A module restarted after the key file was lost must not make another.
    with raises(FileNotFoundError):
        NodeIdentity.load(config)

    assert not config.private_key_path.exists()
    assert not (tmp_path / "data").exists()
