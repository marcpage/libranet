# Bundle Format Specification

## 1. Overview

A **bundle** is a JSON container used to describe a file or directory,
optionally signed and/or password-protected, and designed to work natively with
content-addressed storage (CAS).

Bundles are **self-describing by structure**. There is no explicit `flavor` or
`type` tag anywhere in the format — a reader determines what it is looking at
purely from the shape of the data:

| Shape | Meaning |
| --- | --- |
| Top-level bytes are not valid JSON | Password-protected (encrypted) bundle |
| Object with a `signature` key | Signed bundle |
| Object with a `contents` array | File bundle |
| Object with a `contents` object | Directory bundle |
| Object with a `contents` string | Symlink entry |
| Object with no `contents` key | Metadata-only directory marker |

All paths, filenames, and symlink targets are **case-sensitive** and **UTF-8
encoded**. NFC (composed) normalization is **recommended but not enforced** for
these strings. Regardless, bundles should be internally consistent, and readers
perform plain byte comparison without normalizing.

---

## 2. Raw File Bundle

```json
{
  "metadata": {
    "created": "2026-08-01T12:00:00Z",
    "modified": "2026-09-01T08:30:00Z",
    "size": 4096,
    "writable": true,
    "executable": false,
    "algorithm": "sha256",
    "hash": "9f86d0..."
  },
  "contents": [
    "sha256/9f86d0...",
    "sha1/1b4f0e..."
  ],
  "versions": [
    ["sha256/aaa...", "sha256/bbb..."]
  ]
}
```

### 2.1 Fields

- **`metadata`** — optional. Any known metadata about the file:
  - `created`, `modified` — timestamps
  - `size` — size in bytes
  - `writable`, `executable` — booleans. **Default to `false` if omitted.**
  - `algorithm`, `hash` — the hash algorithm and hash value of the
    **fully reassembled file** (see §2.3).
  - `xattrs` — the file's extended attributes (see §2.4).
- **`contents`** — required. An ordered list of CAS paths, one per part of the
  file, in the order the parts appear in the reconstructed file. Example:
  `["sha256/...", "sha256/...", ...]`.
- **`versions`** — optional. A list of previous versions of this file, each
  itself a list of CAS paths (i.e., the `contents` of a prior file bundle).
  - A single entry means this version is an **update** to that version.
  - Multiple entries mean this version **combines/merges** those versions
    into one.
  - This distinction is contextual/human-interpreted — it is not derived or
    enforced by any algorithm.

### 2.2 Multi-algorithm parts (CAS design intent)

The parts listed in `contents` are **not required to share a single hashing
algorithm**. A file's parts may be addressed under old algorithms (e.g.
`sha1`) alongside parts addressed under newer algorithms (e.g. `sha256`,
`blake3`, or future/post-quantum algorithms):

```json
"contents": [
  "sha1/abc123...",
  "sha256/def456...",
  "blake3/789ghi..."
]
```

This is intentional: it allows the ecosystem to adopt new hashing algorithms
over time **without ever needing to re-hash, migrate, or duplicate existing
CAS data**. Old data remains valid and dedup-eligible indefinitely; new data
can immediately use better algorithms. Algorithm adoption is purely additive.

### 2.3 Whole-file hash

`metadata.algorithm` / `metadata.hash` are **independent of any per-part CAS
addressing algorithm**. They are computed over the **fully reassembled file**
(all parts concatenated in `contents` order), not per-part. This exists
specifically to guard against the rare case of a hash collision or corruption
affecting an individual part while the parts still resolve to *something*
addressable — the whole-file hash catches what a part-level check alone
could not.

### 2.4 Extended attributes

`metadata.xattrs` records a file's extended attributes: the named values some
filesystems attach to a file beside its contents, such as macOS Finder tags or
Linux `user.*` attributes.

```json
"metadata": {
  "xattrs": {
    "user.origin": "aHR0cHM6Ly9leGFtcGxlLm9yZy8=",
    "com.apple.ResourceFork": ["sha256/4e07b1...", "sha256/8c2d9a..."]
  }
}
```

- Each key is an attribute's name, exactly as the platform reports it. A name
  that is not valid UTF-8 cannot be a key, so that attribute cannot be
  recorded.
- Each value is the attribute's bytes, in one of two shapes, told apart by
  shape as everything else in a bundle is (§1):
  - a **string** — the bytes, base64-encoded (RFC 4648 §4, with padding);
  - an **array** — the bytes stored in CAS as parts, listed in order exactly
    as a file's `contents` are (§2.1, §2.2).

  A small value is simplest inline. A large one, such as a resource fork, is
  better stored as parts, so that it deduplicates like file content and does
  not grow the bundle; one too large to fit in a bundle inline has to be.
  A value stored as parts has no whole-value hash: unlike a file (§2.3), it
  is checked only part by part, as each part is against its CAS address.
- `xattrs` is optional. A bundle without it records no extended attributes,
  which does not mean the file had none.
- Parts named in `xattrs` are part of what the bundle needs, like a file's
  parts: whatever gathers or fetches everything a bundle names includes them.
- Attribute names are platform-specific: Linux requires a namespace prefix
  (`user.`, `trusted.`, `security.`, `system.`), and macOS does not. A reader
  restoring an entry sets each attribute it can, and MAY skip one it cannot or
  chooses not to, such as a name the platform rejects or one only a privileged
  user may set, without failing the entry. A writer MAY leave out attributes
  that describe the local copy rather than the content, such as a download
  quarantine flag.

---

## 3. Raw Directory Bundle

```json
{
  "metadata": {
    "created": "2026-01-01T00:00:00Z"
  },
  "contents": {
    "README.md": {
      "metadata": { "size": 128, "algorithm": "sha256", "hash": "..." },
      "contents": ["sha256/..."]
    },
    "docs/Specification.md": {
      "metadata": { "algorithm": "sha256", "hash": "..." },
      "contents": ["sha256/..."]
    },
    "docs/old-spec-link": {
      "contents": "Specification.md"
    },
    "docs": {
      "metadata": { "created": "2026-01-01T00:00:00Z" }
    }
  },
  "versions": ["sha256/..."],
  "extensions": ["sha256/..."]
}
```

### 3.1 Fields

- **`metadata`** — optional. Any metadata about the directory itself. A
  directory's metadata, here or in a metadata-only entry, may carry `xattrs`
  for the directory's own extended attributes, as a file's does (§2.4). A
  symlink entry carries no metadata, so a symlink's own extended attributes
  are not recorded.
- **`contents`** — required (may be `{}`). An object mapping **relative
  paths** (from the directory root) to entries. Full hierarchical paths
  (e.g. `docs/Specification.md`) may be used directly — there is **no
  requirement to explicitly enumerate intermediate directories**. A complete
  directory hierarchy can be represented without a single explicit directory
  entry.
  Paths use `/` as the separator, and **no segment may be empty, `.`, or
  `..`**. A path therefore cannot start or end with `/`, repeat a `/`, or
  reach outside the directory root. Such spellings would also give one entry
  more than one name, since readers compare paths byte for byte (§1). A
  bundle holding such a path is malformed.
  - A **file entry** is a directory-`contents` value shaped like §2 (a
    `contents` array plus optional `metadata`).
  - A **symlink entry** is `{ "contents": "<relative POSIX-style target>" }`
    — the target is relative to the **symlink's own location**, following
    POSIX symlink convention. The target may use `.` and `..` to reach
    other entries; the restriction on paths above applies only to
    `contents` keys.
  - A **metadata-only entry** (no `contents` key at all) is used only when
    an author wants to attach metadata to a directory, and/or to assert that
    an otherwise-unreferenced directory exists (e.g., an empty directory).
    It carries no implication about that directory's children — children are
    established independently via their own full-path keys.
- **`versions`** — optional, same semantics as file bundles (§2.1), but each
  entry is a path to a previous **directory bundle**.
- **`extensions`** — optional. A priority-ordered list of paths to other
  directory bundles whose `contents` should be merged in (see §4).

---

## 4. Directory Extensions

Directory bundles can be split across multiple bundles via `extensions` to
keep any single bundle from growing unbounded (e.g., a new version that only
adds files can extend the previous version rather than re-listing everything).

### 4.1 Resolution algorithm

Extensions may themselves have extensions, resolved recursively. Given a
bundle with `extensions: [A, B, C]`:

```Python
resolve(bundle):
    result = {}

    for ext_path in reverse(bundle.extensions):     # C, then B, then A
        ext_bundle = load(ext_path)
        resolved_ext = resolve(ext_bundle)          # recurse: resolve ITS extensions first

        for (key, entry) in resolved_ext.contents:
            result[key] = entry                     # whole-entry replace

    for (key, entry) in bundle.contents:
        result[key] = entry                         # top-level wins last

    return result
```

Net priority order: **top-level bundle > first extension > second extension
> ... > last extension's own extensions (deepest first)**.

### 4.2 Overlay semantics

Overlaying is **whole-entry replacement**, keyed on an exact, case-sensitive,
byte-for-byte match of the UTF-8 relative path string. If a higher-priority
layer defines an entry for a path, that entry **entirely replaces** any
lower-priority entry for the same path — there is no field-level merging
within an entry, and no special handling is needed when the entry types
differ (e.g., a file entry in one layer vs. a directory-metadata entry in
another at the same path) since the higher-priority entry simply wins outright.

A `null` value for a directory entry means the entry should be deleted (not
rendered) when resolving `contents`. This allows a bundle to reuse an
existing extension while removing individual entries it no longer wants,
without needing to duplicate the rest of that extension's contents.
The `null` entries can be completely ignored or removed after the full
directory bundle contents is resolved.

### 4.3 Example

```text
top-level.contents:      { "README.md": v3 }
top-level.extensions:    [A]

A.contents:               { "README.md": v2, "docs/spec.md": ... }
A.extensions:              [B]

B.contents:               { "README.md": v1, "LICENSE": ... }
```

Resolution: resolve `B` → `{README.md: v1, LICENSE}`. Overlay `A` →
`{README.md: v2, LICENSE, docs/spec.md}`. Overlay top-level →
`{README.md: v3, LICENSE, docs/spec.md}`.

---

## 5. Signed Bundle

```json
{
  "signer": "sha256/8f3a...",
  "algorithm": "sha256",
  "hash": "c2b1...",
  "signature": "MEUCIQ...",
  "contents": "{\"metadata\":{...},\"contents\":[...]}"
}
```

- **`signer`** — a relative CAS path pointing to the public key of the signer.
- **`algorithm`** — hashing algorithm used to hash the bundle contents.
- **`hash`** — the hash of `contents`.
- **`signature`** — the signature over the hash.
- **`contents`** — the raw bundle (file or directory bundle), serialized as
  **JSON text with standard string escaping** (not base64 — base64 would
  take up more space than escaped JSON text).

A bundle is identified as a signed bundle by the presence of the `signature`
key.

---

## 6. Password Protection

Password protection applies to an **entire raw bundle** (file or directory)
at once — the whole serialized bundle becomes the encrypted payload. A
password-protected bundle is recognized because its raw bytes are **not
valid JSON** (in fact generally not valid UTF-8 text at all).

### 6.1 Encoding (write path)

```Python
plaintext   = JSON-serialized raw bundle (file or directory bundle)
compressed  = zlib_compress(plaintext, level = author's choice)
padded      = pkcs7_pad(compressed, cipher block size)   # 16 bytes for AES
key         = hash_algorithm(password)          # single-pass hash, e.g. SHA256
iv          = explicit IV, or all-zero bytes if not specified
ciphertext  = cipher_encrypt(padded, key, iv)   # e.g. AES-256-CBC

payload = ciphertext
        + 0x00
        + "PW-{hash algorithm}-{cipher}-{mode}[-IV:{hex-encoded IV}]"
        [+ 0x00 + CAS address-targeting bytes]     # optional, see §6.4
```

Example descriptor strings:

- `PW-SHA256-AES256-CBC` (default all-zero IV)
- `PW-SHA256-AES256-CBC-IV:a1b2c3...` (explicit IV)

Block ciphers such as AES encrypt whole blocks, so the compressed bundle is
padded with **PKCS#7**
([RFC 5652 §6.3](https://www.rfc-editor.org/rfc/rfc5652#section-6.3)) before it
is encrypted. Padding adds from 1 byte up to a whole block, and every byte it
adds holds the number of bytes added. A bundle that already fills its last block
therefore gains a whole block, so the padding can always be removed
unambiguously. Encoders must pad this way, since identical ciphertext (§6.3)
depends on every encoder padding alike. Decoders remove the padding after
decrypting.

### 6.2 Key derivation

The password is hashed with a **single-pass hash** (e.g. plain SHA256 over
the password bytes) unless a different, stronger scheme is explicitly named
in the descriptor.

**No salt is used.** This is required rather than merely convenient. The
byte-for-byte identical ciphertext §6.3 depends on comes from every node
deriving the same key from the same password, which a per-bundle salt would
defeat. Salting and deduplication cannot both be had, and this format
chooses deduplication.

What a single-pass hash costs must be understood before choosing a
password for one. The ciphertext is a stored artifact, and on a
content-addressed network it is a published one: anyone who learns a
protected bundle's address can fetch it and test candidate passwords
offline without limit, deriving a key from each guess, decrypting, and
seeing whether the result decompresses to valid JSON. Because the
derivation takes no salt and nothing else particular to the bundle, a table
of derived keys computed once applies to every password-protected bundle in
existence. That the password is supplied fresh at decrypt time, and no hash
of it is stored anywhere, does not hinder this attack: the ciphertext
already supplies everything it needs.

A single-pass hash is therefore appropriate only where the password itself
puts guessing out of reach — a high-entropy secret generated by a machine,
such as the backup secret of
[Backup Specification §4.2](BackupSpecification.md#42-backup-secret-generation),
which is CSPRNG output and is never displayed or typed.

Where the password is instead chosen by a person, as in the Basic
Authentication prompt of
[HTTP API §13.1](HttpApi.md#131-password-protected-apps), an encoder SHOULD
name a deliberately costly key derivation function in the descriptor
instead, and a decoder MUST honor the one named. Cost and determinism are
compatible: a function such as scrypt under a fixed salt derives the same
key from the same password on every node, preserving §6.3, while making
each guess expensive enough to put offline search out of reach. No such
token is registered yet (§8).

### 6.3 Determinism and deduplication

The default IV (all-zero, when not explicitly specified) combined with
deterministic key derivation and padding (§6.1) means **encrypting
identical content with an identical password always produces byte-for-byte
identical ciphertext**.
This is intentional: it allows two independently encrypted copies of the
same (content, password) pair to resolve to the same CAS address, enabling
natural deduplication consistent with the rest of the format's CAS-native
design. An explicit IV may be specified (`-IV:{hex}`) when non-deterministic
output is desired instead.

### 6.4 CAS address targeting ("drops")

The second, optional null-byte-delimited segment has **no bearing on
decryption**. It is Libranet CAS placement data: a nonce appended so that the
resulting blob's content-address lands "close to" a target address for
distribution purposes. It is generated and consumed entirely by the CAS
layer and is discarded before any decryption logic runs.

### 6.5 Decoding (read path)

```Python
input = raw bytes at this address

if expected_to_be_targeted:
    idx = last_index_of(input, 0x00)
    if idx found:
        input = input[:idx]        # strip CAS placement data

if is_valid_json(input):
    return parse_json(input)       # plain, unprotected bundle

idx = last_index_of(input, 0x00)
if idx not found:
    fail("not the expected blob")

ciphertext = input[:idx]
descriptor = input[idx+1:]         # e.g. "PW-SHA256-AES256-CBC-IV:a1b2..."
parse descriptor -> hash_algorithm, cipher, mode, iv (all-zero if not specified)

key = hash_algorithm(password)
padded = cipher_decrypt(ciphertext, key, iv, cipher, mode)

if not is_valid_pkcs7(padded):
    fail("not the expected blob")  # most often, the wrong password

decrypted = pkcs7_unpad(padded)

if is_valid_json(decrypted):
    return parse_json(decrypted)   # encoder skipped compression

decompressed = zlib_decompress(decrypted)

if is_valid_json(decompressed):
    return parse_json(decompressed)

fail("not the expected blob")
```

Notes:

- JSON text can never contain a raw `0x00` byte (only the escape sequence
  `\u0000`), so the null-byte search is never ambiguous with legitimate
  unencrypted JSON content.
- Encoders always compress before encrypting; decoders additionally tolerate
  an encoder that skipped compression, by checking for valid JSON both
  before and after attempting decompression.

---

## 7. Per-Entry CAS Encryption

In addition to whole-bundle password protection (§6), individual CAS
references — anywhere a CAS path is used, including file `contents` parts,
extended-attribute parts (§2.4), `versions` entries, `extensions` paths, and
`signer` — may point at encrypted
data using an extended path scheme:

```text
{hash algorithm}/{encrypted data hash}/{encryption algorithm}/{encryption key}
```

This allows individual pieces of content to be encrypted while leaving the
surrounding bundle structure (directory listings, filenames, metadata) fully
readable — unlike §6, which encrypts the bundle structure but not the contents.

### 7.1 Fields

- **`{hash algorithm}`** / **`{encrypted data hash}`** — the algorithm and
  hash of the data **after encryption** (i.e., of the ciphertext actually
  stored at this address, not of the plaintext). This is critical for
  verification: nodes may pass encrypted data around without knowing its
  content, and must still be able to verify that the bytes they received
  match the requested address.
- **`{encryption algorithm}`** — the cipher/mode used, following the same
  convention as the password-protection descriptor in §6 (e.g.
  `AES256-CBC`), optionally including an IV suffix (`-IV:{hex}`). If no IV
  is specified, it defaults to all-zero, consistent with §6.3. A mode that
  encrypts whole blocks, such as CBC, pads the plaintext with **PKCS#7**
  first, exactly as §6.1 describes.
- **`{encryption key}`** — the decryption key, hex-encoded.

### 7.2 Convergent key derivation

The encryption key is derived from the **plaintext content** in a manner
specific to the encryption algorithm in use, so that encrypting identical
content always produces an identical key (and, combined with the default
all-zero IV and PKCS#7 padding, identical ciphertext and therefore an
identical CAS address — preserving deduplication, consistent with §6.3).

For AES256-based encryption, SHA256 is the standard key-derivation
candidate: `key = SHA256(plaintext)`.

Not every encryption algorithm may fit neatly into a convergent-key
paradigm. Key derivation is therefore defined **per algorithm**, not by a
single universal rule — when support for a new encryption algorithm is
added, its convergent-key derivation mechanism (if any) is determined at
that time.

### 7.3 Example

```json
"contents": [
  "sha256/9f86d0...",
  "sha256/1b4f0e.../AES256-CBC/a1b2c3d4e5f6..."
]
```

Here the second part is stored encrypted; its address hash is of the
ciphertext, and the trailing segments carry everything a holder of the full
path needs to decrypt it.

---

## 8. Open Items / Not Yet Specified

The following were identified during design discussion but not yet resolved:

- Full canonical list/registry of supported `algorithm` tokens (e.g.
  `sha256`, `sha1`, `blake3`) and `cipher`/`mode` tokens (e.g. `AES256-CBC`).
- Whether readers should validate/reject non-NFC path or symlink-target
  strings, or purely rely on producer conformance (current guidance:
  recommended, not enforced, no validation required).
- Formal JSON Schema for machine validation of bundle structure.
- The algorithm token, and the cost parameters it carries, for the costly
  key derivation §6.2 calls for when a password is chosen by a person. The
  token has to name enough for any decoder to reproduce the key, keep the
  derivation deterministic across nodes so §6.3 still holds, and fit the
  `PW-{hash algorithm}-{cipher}-{mode}` descriptor shape of §6.1.
