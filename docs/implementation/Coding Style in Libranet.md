# Coding Style in Libranet

Version 0.1 • October 2026

---

## 1. Purpose

The [Coding Style](Coding%20Style.md) holds rules for writing Python that
name nothing in this repository, so that they can be carried to another
project. This document holds what they leave to the project: how the tools
are run here, the modules and tests the rules lean on, and the places where
the code departs from the rules. It changes with the code, where the Coding
Style does not. A section number here with no document named is the Coding
Style's.

[`CLAUDE.md`](../../CLAUDE.md) holds the short list of rules a Claude Code
session is given, which the Coding Style expands on. How modules, messages,
and processes fit together is in [Module System](Module%20System.md).

## 2. The Tools

```bash
uv run black .
uv run flake8
uv run mypy
uv run pylint src tests hatch_build.py
uv run pytest --cov
```

`pylint` reads `hatch_build.py`, the hook that builds the wheel, as well as
`src` and `tests`. CI runs all five on every pull request, and the tests on
Ubuntu and macOS against Python 3.11, the oldest version supported, and
3.14.

## 3. What the Rules Lean On

- **Imports (§3.2).** `tests/test_package_exports.py` checks that each
  package re-exports its public names. `cas/layered.py` is left out, for
  the reason `cas/__init__.py` gives. `cli` and `config.paths` are imported
  as namespaces. `supervisor.py` imports `sys` whole, so that `sys.stderr`
  is looked up when it is used, and `logging_setup.py` imports `sys` and
  `threading` whole, to install the hooks for uncaught exceptions.
- **Names (§4).** A package's process is in `module.py`, as the class
  `{Area}Module` (`validator/module.py`, `ValidatorModule`), and it handles
  each event it subscribes to in `_on_{event}`.
- **Types (§5).** `ModuleBase` is the plain base class, carrying the receive
  loop the modules share. `EventType` and `ModuleName` are the `StrEnum`s
  more than one module spells.
- **Dataclasses (§6.2).** pydantic is used only in `config/`.
- **Parameters (§6.4).** A module is tested with plain `queue.Queue`
  objects. `Sessions` and the nonce searches, among others, take
  `monotonic` as their clock's default.
- **Shared state (§6.5).** `write_atomically` and `atomic_writer`, in
  `atomic_file.py`, write a file the node may be reading.
- **Constants (§7).** `MIB`, `HOUR_SECONDS`, and `DAY_SECONDS` are in
  `config/models.py`. `json_format.py` is the small top-level module for a
  fact no layer owns. The layers are in Module System §5.3.
- **Docstrings (§8.1).** A module docstring names a specification section
  ("HttpApi §5.4") or a plan step ("Phase 2 Step 48").
- **Catching (§9.2).** `tests/test_exception_logging.py` fails on a handler
  that does none of the three things. `configure_logging`, in
  `logging_setup.py`, installs the hooks for uncaught exceptions.
- **Logging (§9.3).** A module process logs with `self.logger`. `print` is
  used only for the supervisor's startup errors and by
  `libranet-local-network`.
- **Untrusted input (§10.1).** `json_fields.py` holds `is_integer`,
  `is_number`, `string_field`, `integer_field`, and `number_field`.
- **Tests (§11).** A module is tested without starting a process as Module
  System §11 describes.

## 4. Where the Code Departs

These are in the code as of Phase 6 Step 143 and the cleanup of #464.
Paths are relative to `src/libranet/`, except those that start with
`tests/`.

| Where | Departure |
| --- | --- |
| `tests/test_config_models.py` | Three `Test…` classes (§11) |
| `logging_setup.get_logger` | A `get_` name (§4) |
| `applications/packaged.py` `_Objects`; `backup/builds.py` `_Earlier` | A private class at the bottom, after the public code that uses it (§3.1) |
| `bundle/building.py` every private function from `_identity` to `_metadata`, eleven, before `modified_time`; `identity/keys.py` `_decompressed`; `stats/database.py` `_source_rank`; `stats/lists.py` `_cost`; `webserver/config_guard.py` `_is_page_navigation`, `_api_page_refusal`; `webserver/config_handlers.py` `_not_registered_response`, `_unreadable_registry_response`; `webserver/server.py` `_registry`, `_config_identities`, `_bundle_paths` | A private function before public code (§3.1) |
| `webserver/data_handler.py` `DataReadHandler`; `webserver/data_write_handler.py` `DataWriteHandler` | A handler that keeps its collaborators in private attributes set by `__init__`, where every other handler and guard class is a frozen dataclass of them (§6.2) |
| `problems.py` `Problem` | Its JSON form is `to_dict()`, and its body `to_json()`, rather than `value()` (§6.3) |
| `webserver/config_credential.py` `StoredCredential` | `from_json()` and `to_json()` read and write the file's bytes, rather than `from_value()` and `value()` (§6.3) |
| 82 public callables in 42 files, listed in 4.1 below | An optional parameter a call may pass by position (§6.4) |
| `backup/imports.py`, `connections/peer_session.py`, `messaging/queues.py`, `protocol/karma_carried.py`, `protocol/karma_payments.py`, `stats/records.py`, `stats/schema.py`, `webserver/data_handler.py`, `webserver/search_handler.py`, `webserver/shun_handler.py`, `webserver/signed_versions.py`, `webserver/site_checks.py` | No test file of its own: each is tested through the code that uses it (§11) |

### 4.1 Optional Parameters Taken Positionally

Each callable is shown with the parameters that have a default but may be
passed by position. A constructor is shown as its class. A method that keeps
the signature of the one it overrides or stands in for is not counted
(§6.4).

| File | Callables |
| --- | --- |
| `applications/packaged.py` | `PackagedApplications.build(directory, names)` |
| `backup/restores.py` | `Restore.attempt(xattrs)` |
| `backup/runs.py` | `AnnouncingStore(make_room)` |
| `backup/writing.py` | `DirectoryWriter(xattrs)`, `DirectoryWriter.open(xattrs)` |
| `bundle/building.py` | `IgnoredPaths(paths)`, `build_file(max_object_bytes)`, `build_directory(supersedes, max_object_bytes)` |
| `bundle/encryption.py` | `Aes256Cbc(iv)` |
| `bundle/extensions.py` | `resolve_directory(max_extensions)` |
| `bundle/loading.py` | `load_bundle(max_bytes, password, targeted)` |
| `bundle/parts.py` | `PartPath.chunks(size_bytes)` |
| `bundle/protection.py` | `protect(max_object_bytes)` |
| `bundle/shapes.py` | `Metadata.xattr_parts(included)` |
| `bundle/storing.py` | `store_object(max_object_bytes)`, `store_bundle(password, max_object_bytes, max_extensions)`, `StoredDirectory.store(password, max_object_bytes, max_extensions)` |
| `bundle/symlinks.py` | `path_reached(directories)` |
| `bundle/xattrs.py` | `ExtendedAttributes(excluded)` |
| `cas/algorithms.py` | `AlgorithmRegistry(algorithms)` |
| `cas/archive.py` | `ArchiveSource(registry)`, `ArchiveSource.open(registry)` |
| `cas/content_id.py` | `ContentId.create(registry)`, `ContentId.parse(registry)`, `ContentId.from_fields(registry)`, `ContentId.from_stored_name(registry)`, `ContentId.for_data(registry)`, `ContentId.matches(registry)` |
| `cas/layered.py` | `packaged_archives(directory)`, `LayeredSource(archives, applications)`, `LayeredSource.open(packaged, sources)` |
| `cas/verification.py` | `content_matches(registry)` |
| `cli.py` | `parse_args(argv)` |
| `config/models.py` | `LibranetConfig.load(path)` |
| `config/paths.py` | `default_local_folders(dirs)` |
| `config/seeds.py` | `SeedPeer.load_all(path)` |
| `connections/peer_connection.py` | `PeerConnection.request(headers, body)` |
| `eviction/pressure.py` | `StoragePressure(headroom_bytes)`, `StoragePressure.of(free_bytes, headroom_bytes)`, `StoragePressure.over_limits(adding_bytes)` |
| `identity/authentication.py` | `RequestAuthenticator(max_tracked_signers)`, `RequestAuthenticator.authenticate(body)` |
| `identity/signatures.py` | `MessageSigner(clock)`, `MessageSigner.sign_request(body)`, `MessageSigner.sign_response(body)`, `MessageVerifier(clock)`, `MessageVerifier.of(clock)`, `MessageVerifier.verify_request(body)`, `MessageVerifier.verify_response(body)` |
| `local_network.py` | `LocalNetwork.create(debug)`, `ConnectionLog(offset, inode)`, `parse_args(argv)`, `main(argv)` |
| `messaging/dispatcher.py` | `Dispatcher.run(stop)` |
| `messaging/envelope.py` | `make_message(payload)` |
| `messaging/module.py` | `ModuleBase.publish(payload)`, `ModuleBase.receive(timeout_seconds)`, `ModuleBase.run(stop)` |
| `messaging/queues.py` | `create_module_queues(start_method, subscriptions)` |
| `problems.py` | `Problem.for_status(detail, instance)` |
| `protocol/backup_requests.py` | `BackupJobRequest.create(interval_seconds)`, `BuildRequest.create(password)`, `ExportRequest.create(password)` |
| `protocol/search.py` | `normalize_prefix(registry)`, `LocalSearch(registry)` |
| `stats/database.py` | `StatsDatabase.eviction_order(exclude)`, `StatsDatabase.record_connection_opened(endpoint)`, `StatsDatabase.last_good_endpoints(exclude)`, `StatsDatabase.candidate_endpoints(exclude)`, `StatsDatabase.record_seek(node_id)`, `StatsDatabase.clear_seek(node_id)`, `StatsDatabase.seek_values(node_id)` |
| `supervisor.py` | `main(argv)` |
| `webserver/app_outcomes.py` | `ApplicationOutcomes(max_outcomes)` |
| `webserver/app_registry.py` | `ApplicationRegistry(initial)` |
| `webserver/bundle_edits.py` | `BundleEdit.read(max_bundle_bytes)` |
| `webserver/http_types.py` | `RequestBody(stream)`, `bytes_response(content_type, headers)`, `json_response(status)`, `problem_response(headers)` |
| `webserver/own_pages.py` | `OwnPages.refusal(application)`, `OwnPages.refused(application)` |
