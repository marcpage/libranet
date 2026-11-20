# Libranet C++ Implementation Plan — Phase 6

Version 0.1 • November 2026

---

## 1. Purpose

This document plans user accounts. A person makes an identity on a C++ node,
or signs in there with one made on any node, and uses the applications that
need someone signed in: mail, and the user directory.

It is shown on a network of Python nodes and a C++ node. A person made on a
Python node signs in on the C++ node with the same username and password,
and sends mail from there that arrives at the Python node. The user
directory on each node lists people made on the other.

What it takes:

- Making an identity, its fallback, and signing in and out, `/data/users`,
  `/data/fallback`, and `/data/session`
  ([HTTP API §11.3](../../../docs/specs/HttpApi.md#113-a-persons-identity)
  and [§11.4](../../../docs/specs/HttpApi.md#114-signing-in)), with the
  costly derivation of a key from a password.
- Signing as a person, `/data/signed`
  ([§11.5](../../../docs/specs/HttpApi.md#115-signing-as-a-person)).
- Encrypted blocks, `/data/encrypted`
  ([§11.6](../../../docs/specs/HttpApi.md#116-encrypting-a-block) and
  [Bundle Specification §8](../../../docs/specs/BundleSpecification.md#8-encryption-for-a-person)).
- Drops: making one from a page, and searching one by its target, here and
  across the peers, `/data/drop`
  ([§9](../../../docs/specs/HttpApi.md#9-drops)).
- Shunning content, `DELETE` of `/data/{algorithm}/{hash}`
  ([§5.5](../../../docs/specs/HttpApi.md#55-shunned-content)).
- Creating a user from `/config`, `/config/api/users`.

Trust in what people say and sign comes in [Phase 7](Phase%207.md), and
paying and staking as a person in [Phase 8](Phase%208.md).

The Python node built the same in its
[Phase 4](../../../python/docs/plans/Phase%204.md). Its issues are in the
GitHub **C++ Phase 6 - User accounts** milestone.
