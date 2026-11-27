# C++ Coding Style in Libranet

Version 0.1 • November 2026

---

## 1. Purpose

The [C++ Coding Style](Coding%20Style.md) holds rules for writing C++ that
name nothing in this repository, so that they can be carried to another
project. This document holds what they leave to the project: how the tools
are run here, the code the rules lean on, and the places where the code
departs from the rules. It changes with the code, where the Coding Style
does not. A section number here with no document named is the Coding
Style's.

How to build the node, run its tests, and run each tool is in the
[README](../README.md). The Python node keeps its own pair of documents, in
[`python/docs/`](../../python/docs/).

## 2. The Tools

The commands are in the README's sections on
[Building](../README.md#building), [Testing](../README.md#testing),
[Coverage](../README.md#coverage), [Sanitizers](../README.md#sanitizers),
[Fuzzing](../README.md#fuzzing), and [Checking](../README.md#checking).

CI runs four C++ jobs, besides CodeQL, on every pull request that changes
`cpp/` or a workflow, and on each push to `main` that does. Each that builds
compiles through ccache, with each build's cache kept from one run to the
next:

- **`cpp`**, on Ubuntu with GCC and on macOS with Apple's Clang, builds for
  Debug, with coverage, and for Release, runs the tests in the Debug build,
  and reports coverage against the floor in [`gcovr.cfg`](../gcovr.cfg). On
  macOS it also runs each test program under `leaks`.
- **`cpp-sanitizers`**, on Ubuntu and on macOS, builds with AddressSanitizer
  and UndefinedBehaviorSanitizer, and with ThreadSanitizer, and runs the
  tests in both builds, each test program a few times, each time a share of
  its tests.
- **`cpp-fuzzers`**, on Ubuntu, builds the fuzzers with Clang,
  AddressSanitizer, and UndefinedBehaviorSanitizer, runs them all at once
  for two minutes, and uploads the input of any fault one finds.
- **`cpp-lint`**, on Ubuntu and on macOS, configures, with Clang and the
  fuzzers on Ubuntu, and with Apple's Clang and no fuzzers on macOS, and
  runs `clang-tidy` and `cppcheck` on what CMake writes. On a pull request,
  `clang-tidy` checks only the `.cpp` files that changed, or whose headers
  did, since an earlier run, as
  [`scripts/filter_compilation_db.py`](../../scripts/filter_compilation_db.py)
  finds them. On Ubuntu it runs `clang-format` as well. It runs LLVM 23.1
  and Cppcheck 2.22.0, whose findings change from one version to the next.

A job named `C++` passes when all four passed, or were not needed, and is
the check a pull request must pass.

Each lint run sees only what its platform builds: the code under
`#if __APPLE__`, which is the Apple classes in `crypto/` (`*Macos.hpp`,
`CFObject.hpp`, and `CFBytes.hpp`) and the macOS branches of
`net/SocketPosix.cpp` and `io/Directories.cpp`, only on macOS, and the
Linux branches only on Ubuntu. `cppcheck` is given `-D__linux__` on Ubuntu
and `-D__APPLE__` on macOS. It reads less of the code both platforms build
on macOS, without the fuzzers and with Apple's implementations chosen in
place of OpenSSL's, so there the suppressions in `types/Integer.hpp`,
`types/Json.hpp`, and `crypto/RsaOpenSsl.hpp` match nothing. The macOS run
passes `--suppress=unmatchedSuppression`, and only the Ubuntu run fails on
a suppression that matches nothing.

[`.clang-tidy`](../.clang-tidy) turns off, as unable to apply, the checks
for FPGA kernels (`altera-*`) and LLVM's C library (`llvmlibc-*`); two that
contradict others; `llvm-header-guard`, which names a guard from the
file's absolute path; `portability-avoid-pragma-once`; Fuchsia's bans on
default arguments and overloaded operators; three that ask for Abseil's,
Boost's, or LLVM's own functions; and `modernize-return-braced-init-list`,
whose fix makes `Json` an array. Its list of checks not yet met, each with a
count and an example, is the one place those are kept, and §4 does not
repeat it. Two `CheckOptions` lean on this code:
`misc-include-cleaner.IgnoreHeaders` leaves the POSIX headers as written,
and the unchecked-access check excludes `::types::Json` (§3).

[`cppcheck-suppressions.txt`](../cppcheck-suppressions.txt) holds only the
two entries that cannot apply. Every other suppression is written on the
line it is for (§2.1).

## 3. What the Rules Lean On

- **Libraries (§3.1).** `types`, `net`, `crypto`, `logger`, `db`, `msg`,
  `io`, `http`, `storage`, `identity`, `protocol`, `web`, `api`, `eviction`,
  `stats`, `peer`, `config`, and `node`, each in the namespace of its name,
  with the target `libranet_<name>` and the alias `libranet::<name>`, a test
  program `<name>_tests`, and fuzzers named `<parser>_fuzzer`, made by
  `libranet_add_fuzzer()` in the top `CMakeLists.txt`. `types` and `http`
  are `INTERFACE` libraries. The program, `libranet`, is `main.cpp`, linked
  to `libranet::node`. One source file is made by the build:
  `config/CMakeLists.txt` writes `applications/config/index.html` into
  `PageHtml.cpp`, in the build directory, from `config/PageHtml.cpp.in`, a
  line to a string, so that no string is longer than a compiler takes.
- **Implementations (§6.2).** In `crypto/`, `*Macos` is built on Apple's
  CommonCrypto and Security frameworks, and `*OpenSsl` on OpenSSL. The
  factories `IHasher::Sha256()`, `IEncryptKey::Aes()`, and
  `IMultiKey::Rsa()` and `from_pem()` make the Apple class under
  `#if __APPLE__` and the OpenSSL one elsewhere. `ISignKey::from_pem()`
  reads an RSA key in the same way, and an Ed25519 key with OpenSSL, and
  refuses any other.
  `ISignKey::Ed25519()` and `IPasswordHasher::Argon2id()` make OpenSSL's on
  both. In `net/`,
  `ISocket::bind()` and `connect()` make a `SocketPosix`, and `ISocket.cpp`
  stops the build with `#error` on any platform but Linux and macOS.
- **Resources (§6.6).** `OpenSslObject<Type, free>`, and `OpenSslKey` for
  `EVP_PKEY`, in `crypto/OpenSsl.hpp`, hold OpenSSL's objects.
  `CFObject<CFType>`, in `crypto/CFObject.hpp`, holds Core Foundation's,
  and its constructor takes `Policy::Retain` or `Policy::TakeOwnership`.
  `AddrInfoPtr`, in `net/SocketPosix.cpp`, holds what `getaddrinfo()`
  returns, and `AesMacos::Cryptor` a CommonCrypto cryptor. `AesOpenSsl`
  wipes its key with `OPENSSL_cleanse()`.
  `AsyncChannel`'s destructor stops and joins its worker, and ends the
  program if stopping throws. `db::Database` holds SQLite's connection, and
  a `Database::Transaction` rolls back what it was not told to commit.
  `db::Statement` finalizes the SQLite statement it prepared.
  `io::SafeWriteFile` removes what was written to it unless it was
  committed, and with `Access::Owner` makes it at once, so that only its
  owner may ever read it; `storage::DataDir`, `identity::NodeIdentity`, and
  `config::Credential` write with it, the last with `write_if_absent()`.
  `io::FileReader` closes the file it opened as it goes, and every file the
  node reads itself, SQLite's aside, is read with it.
  `http::BasicCredentials` wipes its password as it goes.
  `config::DialOutcomes` lets each request waiting on it go as it goes, and
  waits for each to leave.
  `peer::PeerConnection`'s destructor closes its connection, failing the
  requests still waiting, and joins its two threads, ending the program if
  closing throws. `StopSignals`, in `node/Node.cpp`, holds back SIGINT and
  SIGTERM from the threads the node starts, and lets them through again
  when it goes.
- **Casts (§10.4).** `OpenSslBytes::of()` is where bytes held as
  `std::byte` or `char` become the `unsigned char*` OpenSSL takes, and
  `OpenSslDer` is where OpenSSL's `d2i_*` and `i2d_*` functions are called.
  `CFBytes`, in `crypto/CFBytes.hpp`, is where they become Core
  Foundation's `UInt8` and back.
  `query_socket_address()`, in `net/SocketPosix.cpp`, casts a
  `sockaddr_storage` for the socket calls. `Statement::text()`, in
  `db/Statement.cpp`, reads the `unsigned char` text SQLite gives as `char`.
  `io::FileReader::read_some()` is where a file's `char`s become
  `std::byte`s.
- **Errors (§9.1).** `OpenSslError`, a `std::runtime_error`, adds the last
  error OpenSSL queued for the thread, and clears the queue. In
  `net/SocketPosix.cpp`, `assert_socket()` throws a `std::system_error`
  with `errno` and the call's name, and `assert_condition<E>()` throws `E`.
  `db::SqliteError`, a `std::runtime_error`, adds what SQLite said of the
  call that failed.
- **Reporting (§9.3).** `AsyncChannel::Logger` is the function an
  `AsyncChannel` reports with; given none, it reports nothing.
  `AsyncChannelOf` reports a message of a type it was not made for.
- **Implicit conversions (§6.4).** `Json` converts from each kind of value
  it holds, so that its initializer lists read as JSON does, and takes
  `long` and `long long` by name, since `std::int64_t` is `long` on Linux
  and `long long` on macOS. `Integer` converts from `std::int64_t`, and
  `ResponseCode` from `ResponseCode::Status`.
- **Unchecked access (§10.1).** `Json`'s `operator[]` grows an array or adds
  a member, or on a `const` `Json` throws, so `.clang-tidy` leaves it out of
  the check. `Integer`'s word arithmetic indexes without a check, for speed,
  in three `NOLINTBEGIN` blocks.
- **Untrusted input (§10.1, §12).** The README's
  [Fuzzing](../README.md#fuzzing) table names each parser and how it turns
  input down. `Json`'s parser nests no deeper than 128,
  `Headers::read_body()` reads a body 64 KiB at a time,
  `Zlib::decompress()` stops just past the limit it is given, making room
  64 KiB at a time, and a `StructuredField` holds no more members, inner
  list items, or parameters than RFC 9651 asks a parser to take.
  `config::Credential` reads no more than 1 KiB of its file, and
  `config::Guard` reads nothing of a request past its headers until the
  credential is checked.
- **Comments (§8).** What a class's comment leaves out is in the
  [Overview](Overview.md), for what each part does and how the parts fit
  together, and in [Messages](Messages.md), for what each posts and takes.
- **Tests (§11).** `crypto/tests/Fixtures.hpp` holds hex helpers, and an RSA
  key with what Python's `cryptography` package signed and encrypted with
  it, as the Python node does, so that the C++ node is checked against the
  Python node. The `crypto/` tests of an interface are instantiated as
  `Factory`, and on macOS as `OpenSsl` too. `crypto_tests` is discovered
  with `NO_PRETTY_VALUES`, so that the addresses of the functions the tests
  take are not in the tests' names. `config_tests` is built with
  `LIBRANET_CONFIG_PAGE`, the path of `applications/config/index.html`, so
  that its tests check the page built in against the file.

## 4. Where the Code Departs

These are in the code as of 2026-11-27. Paths are relative to `src/`. The
checks `.clang-tidy` lists as not yet met are not repeated here.

| Where | Departure |
| --- | --- |
| `types/Integer.hpp` `word_t`, `double_word_t` | A type alias in snake_case (§4) |
| The tests `ConfigNotOffered`, in `config/tests/TestNotOffered.cpp`, and `ConnectionsAddressDialed`, in `peer/tests/TestConnections.cpp` | A test suite not named for its class and `Test` (§4) |
| `http/Headers.hpp` `parse_header_line()`, `read_body()`; `http/Url.hpp` `_extract_fragment()`, `_extract_query()`, `_extract_scheme()`, `_parse_authority()`; `crypto/Argon2idOpenSsl.hpp` `PhcString::_read_number()`; `net/ISocket.hpp` `read_line(line, delimiter)`; `web/Server.cpp` `read_line()`, `body_length()` | A result written to a parameter (§6.5) |
| `node/Node.cpp` `Node::_settings_of()`, with 92 lines of code; `http/ResponseCode.hpp` `default_reason_phrase(Status)`, with 56; the test `NodeTest.RefusesSettingsItCannotUse`, with 64 | A function over 50 lines of code (§6.8) |
| `http/Url.hpp` `append()` (36) and `to_string()` (32); `stats/Recorder.cpp` `Recorder::_handle()` (35); `crypto/RsaPemMacos.hpp` `_create_sec_key_from_data()` (35); `web/Server.cpp` `read_request()` (34); `types/Json.hpp` `Parser::_parse_number()` (34); `http/Response.hpp` `read()` (32); `node/Node.cpp` `seeds_in()` (32); `peer/Connections.cpp` `Connections::_admit()` (31); `crypto/AesOpenSsl.hpp` `_process()` (31); tests: `IntegerTest.BitwiseNegativeOperands` (45), `UrlTest.PortValidationAndEffectivePorts` (40), `BlockingQueueTest.MultipleProducersConsumers` (39), `AsyncChannelTest.CallbackExceptionDoesNotStopProcessing` (33), `ConfigServiceTest.SaysWhatTheNodeDoesNotOfferYet` (33), `ConfigNotOffered.HasEachFeatureThePageUsesThatALaterPhaseBrings` (33), `JsonTest.ConstructorsAndTypes` (32), `GuardTest.RefusesTheApiToWhatIsNoPageOfConfig` (32), and `BlockingQueueTest.ProducerConsumer` (31) | A function over 30 lines of code, the most it should generally have (§6.8) |
