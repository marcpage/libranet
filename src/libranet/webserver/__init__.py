"""Peer-facing and `/config` HTTP endpoint (Phase 1 Steps 5, 7, 9, 14).

Serves the content-addressed source of truth, writes incoming PUT bodies to
a per-connection directory, and publishes messages about what happened. It
does not validate, fetch, evict, or resolve bundles itself.
"""
