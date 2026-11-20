# Libranet in C++

A reference node of [Libranet](../README.md), written in C++17. It is only
begun: five libraries, their tests, and a program that prints a greeting.
What every node must do is in the specifications, in
[`docs/specs/`](../docs/specs/).

---

## Building

The build uses [Conan](https://conan.io/) 2, [CMake](https://cmake.org/)
3.24 or newer, [Ninja](https://ninja-build.org/), and a C++17 compiler. On
macOS, CMake and Ninja come from Homebrew, and Conan from uv:

```bash
brew install cmake ninja
uv tool install conan
```

To build and run, from this directory:

```bash
conan profile detect --exist-ok
conan build . --build=missing
./build/Release/libranet
```

Conan needs a default profile, which names your compiler and its settings.
`conan profile detect` writes one the first time, and with `--exist-ok`
leaves it alone after that. The profile's `compiler.cppstd` must be 17 or
newer, and Conan refuses one that is older. The node itself is always
compiled as C++17, whatever the profile names.

`conan build` installs what the node depends on, writes the CMake toolchain
and presets, runs CMake, and runs the tests. After that first run, CMake can
build on its own:

```bash
cmake --build --preset conan-release
```

For a debug build, pass `-s build_type=Debug` to `conan build`, and use the
`conan-debug` preset. The build goes in `build/Release/` or `build/Debug/`,
and `conan install` writes `CMakeUserPresets.json` here; git ignores both.

When a pull request changes this directory, CI builds the node and its tests on
Ubuntu and macOS, for Debug and for Release, runs the Debug build's tests, and
measures how much of the code they run; see [Coverage](#coverage). It also runs
the tests with sanitizers, and on macOS looks for leaks; see
[Sanitizers](#sanitizers) and [Leaks on macOS](#leaks-on-macos).

---

## Layout

The code is in libraries, one for each directory under [`src/`](src/), with
each library's tests in a `tests/` directory within it, and fuzzers for its
parsers in `fuzz/`:

| Directory | Library | What it holds |
| ------------------------- | ---------------- | ------------------------------------------------------------- |
| [`src/types/`](src/types/) | `libranet::types` | Data types: `Integer`, `Json`, and `BlockingQueue` |
| [`src/http/`](src/http/) | `libranet::http` | Parsing and generating HTTP: URLs, queries, headers, requests, and responses |
| [`src/msg/`](src/msg/) | `libranet::msg` | Messages, and the channels that carry them; uses `types` |
| [`src/net/`](src/net/) | `libranet::net` | Addresses and sockets, on POSIX systems |
| [`src/crypto/`](src/crypto/) | `libranet::crypto` | Hashing, encrypting, and signing: SHA-256, AES, RSA, Ed25519, and Argon2id with OpenSSL; on macOS, SHA-256, AES, and RSA with Apple's frameworks instead |
| [`src/node/`](src/node/) | `libranet::node` | The node's settings file, in YAML, read with yaml-cpp |

Headers are included from `src/`, as in
`#include "types/BlockingQueue.hpp"`. Each directory's `CMakeLists.txt`
names its library's files and the libraries it uses. A library with only
headers is an `INTERFACE` library, and becomes `STATIC` once it has a `.cpp`
file. Every target is compiled with the same warnings, and any warning fails
the build.

---

## Testing

The tests use [GoogleTest](https://google.github.io/googletest/), which
Conan provides. Each library's `tests/Test*.cpp` files build into one test
program for that library, such as `types_tests`, and `conan build` runs them
all.
To run them again after a build, from this directory:

```bash
ctest --test-dir build/Release --output-on-failure
```

A test program can also be run on its own, with `--gtest_filter` to pick
tests by name:

```bash
./build/Release/src/types/types_tests --gtest_filter='JsonTest.*'
```

To build without the tests, configure with `-DBUILD_TESTING=OFF`.

In Debug builds, the C++ standard library checks what it is asked to do: an
index past a container's size, the front of an empty container, or the
value of an empty `optional` stops the program, where a Release build would
go on with whatever memory is there. CMake turns this on with
`_GLIBCXX_ASSERTIONS` for GCC's library, and
`_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_EXTENSIVE` for LLVM's, which
Apple's Clang uses. CI runs the tests in Debug builds.

---

## Coverage

Coverage shows how much of the code the tests run. Configuring with
`-DLIBRANET_COVERAGE=ON` builds every target with `--coverage`, so that
running the tests writes the counts, and
[gcovr](https://gcovr.com/) reports them. Install it with uv:

```bash
uv tool install gcovr
```

From this directory, build for Debug, so that the counts follow the code as
written, then run the tests and report:

```bash
conan install . -s build_type=Debug --build=missing
cmake --preset conan-debug -DLIBRANET_COVERAGE=ON
cmake --build --preset conan-debug
ctest --preset conan-debug
gcovr
```

gcovr reads [`gcovr.cfg`](gcovr.cfg): it counts the code under `src/` but
not the tests, lists the lines no test runs in each file, and exits with
status 2 when the tests run less than 90% of the lines. Counts add up over
runs; to start again, delete them with
`find build/Debug -name '*.gcda' -delete`. CMake remembers the option, so
the Debug build keeps measuring until configured with
`-DLIBRANET_COVERAGE=OFF`.

gcovr runs `gcov`, which must match the compiler. With GCC that is the
default. With Clang, name LLVM's in its place, `llvm-cov gcov`; on macOS,
with Apple's Clang:

```bash
GCOV="xcrun llvm-cov gcov" gcovr
```

For a page per file that marks each line:

```bash
mkdir -p build/coverage
gcovr --html-details build/coverage/index.html
```

CI measures coverage in its Debug builds, with GCC on Ubuntu and Apple's
Clang on macOS. Each fails when coverage falls below the floor, and shows
its totals in the run's summary. The two compilers count the same code and
tests differently, by a little for lines and by a lot for branches, which
is why only lines have a floor.

---

## Sanitizers

Sanitizers check the code as the tests run it, and fail a test that does
something the code must never do. Configuring with `LIBRANET_SANITIZERS`
set to what `-fsanitize=` takes builds every target with them. Two builds
cover all the sanitizers that can run here, since AddressSanitizer and
ThreadSanitizer cannot share one:

| Build | Finds |
| ----- | ----- |
| `address,undefined` | Reading or writing outside a buffer, use after free or after a variable's scope ends, double free; leaks, on Linux only (see [Leaks on macOS](#leaks-on-macos)); and undefined behavior, such as signed overflow, shifts out of range, and misaligned or null pointers |
| `thread` | Data races, and locks taken in orders that can deadlock |

From this directory, each in a directory of its own beside the Debug
build, whose Conan files it uses:

```bash
conan install . -s build_type=Debug --build=missing
cmake -S . -B build/address -G Ninja -DCMAKE_BUILD_TYPE=Debug \
  --toolchain "$PWD/build/Debug/generators/conan_toolchain.cmake" \
  -DLIBRANET_SANITIZERS=address,undefined
cmake --build build/address
ASAN_OPTIONS=detect_stack_use_after_return=1 \
  ctest --test-dir build/address --output-on-failure
cmake -S . -B build/thread -G Ninja -DCMAKE_BUILD_TYPE=Debug \
  --toolchain "$PWD/build/Debug/generators/conan_toolchain.cmake" \
  -DLIBRANET_SANITIZERS=thread
cmake --build build/thread
ctest --test-dir build/thread --output-on-failure
```

`detect_stack_use_after_return=1` has AddressSanitizer also find a pointer
to a function's local variable used after the function returns. The build
stops a test at the first undefined behavior, as AddressSanitizer does at
its first finding; ThreadSanitizer reports every race it sees, then fails
the test.

UndefinedBehaviorSanitizer runs with AddressSanitizer, not with
ThreadSanitizer. With GCC, its checks of an object's type probe memory
through a pipe it opens and closes, and ThreadSanitizer sees the pipe
opened but not closed. When another thread's pipe gets the same file
descriptors, ThreadSanitizer reports a race in a test that has none.

CI runs both builds' tests on Ubuntu, with GCC, and on macOS, with Apple's
Clang. Others are left out:

- MemorySanitizer, which finds reads of memory never set, needs every
  library the tests use built with it too, the C++ standard library and
  GoogleTest among them, and reports false findings otherwise. It also
  needs Clang on Linux.
- HWAddressSanitizer runs only on 64-bit ARM Linux.
- Apple's Clang has no LeakSanitizer, so AddressSanitizer finds leaks
  only on Linux. On macOS, `leaks` finds them instead.

### Leaks on macOS

Apple's `leaks` runs a program and, when it ends, lists any memory that
nothing points to any more, with the stack that allocated each block. After
a Debug build, from this directory:

```bash
leaks --atExit -- build/Debug/src/crypto/crypto_tests
```

It exits with status 0 when it finds no leaks, 1 when it finds some, and
above 1 when it cannot look, as with a program built with AddressSanitizer,
which has its own allocator. So it takes the plain Debug build.

CI runs each test program under `leaks` on macOS, after the Debug build's
tests, and fails on any leak, showing the start of the report.

---

## Fuzzing

A fuzzer gives a parser inputs that
[libFuzzer](https://llvm.org/docs/LibFuzzer.html) makes up, keeps each one
that reaches code no earlier input reached, and makes more from those. Each parser of text from outside has one, in its
library's `fuzz/` directory, which knows how the parser turns down input it
cannot read:

| Fuzzer | Parser | Turns down bad input with |
| ------ | ------ | ------------------------- |
| `json_fuzzer` | `Json::parse` | `std::invalid_argument` |
| `integer_fuzzer` | `Integer` from text | `std::invalid_argument` |
| `url_fuzzer` | `Url::parse` | an empty `optional` |
| `query_fuzzer` | `Query::parse` | nothing: it takes any text |
| `headers_fuzzer` | reading `Headers` | `std::runtime_error` or `std::invalid_argument` |
| `request_fuzzer` | reading a `Request` | a failed stream, or as `Headers` does |
| `response_fuzzer` | reading a `Response` | a failed stream, or as `Headers` does |
| `address_fuzzer` | `Address::parse` | `std::invalid_argument` |
| `rsa_der_fuzzer` | `RsaDer::pkcs1_from_spki` and `pkcs1_from_pkcs8` | `std::invalid_argument` |
| `settings_file_fuzzer` | `SettingsFile::parse` | `std::invalid_argument` |

Anything else a parser does with an input is a fault: crashing, hanging,
leaking, a finding of AddressSanitizer or UndefinedBehaviorSanitizer, a
failed standard library check, using gigabytes of memory, or throwing
anything but its own error.

libFuzzer comes with LLVM's Clang, but not with Apple's. On macOS, install
LLVM with `brew install llvm` and build with its Clang; on Linux, use
`clang` and `clang++`. The fuzzers build without the tests, but the crypto
library needs OpenSSL 3.2 or newer, for Argon2id, so the build takes it from
the Debug build's Conan files. From this directory:

```bash
conan install . -s build_type=Debug --build=missing
CC=/opt/homebrew/opt/llvm/bin/clang CXX=/opt/homebrew/opt/llvm/bin/clang++ \
  cmake -S . -B build/fuzz -G Ninja -DCMAKE_BUILD_TYPE=Debug \
  --toolchain "$PWD/build/Debug/generators/conan_toolchain.cmake" \
  -DBUILD_TESTING=OFF -DLIBRANET_FUZZERS=ON \
  -DLIBRANET_SANITIZERS=address,undefined
cmake --build build/fuzz
./build/fuzz/src/types/json_fuzzer -max_total_time=60
```

A fuzzer that finds a fault stops, and saves the input that caused it in
the current directory, in a file named for the kind of fault, such as
`crash-` and a hash. To run the parser on that input again, give the file to
the fuzzer in place of `-max_total_time=60`.

CI fuzzes on Ubuntu: all the fuzzers run at once, for two minutes, and the
job fails on any fault, with the inputs that caused it uploaded as
`fuzz-findings`. Each run starts afresh; no inputs are kept from one run to
the next.

---

## Checking

How the code is written, and which of those rules the tools below check, is
in the [Coding Style](docs/Coding%20Style.md). Where the code departs from
it, and what its rules lean on here, is in
[Coding Style in Libranet](docs/Coding%20Style%20in%20Libranet.md).

clang-format keeps the code in one layout, set in
[`.clang-format`](.clang-format): the WebKit style, adjusted to change as
little as it can of the code as first written, for now. It also sorts each
block of includes into the order clang-tidy's `llvm-include-order` check
wants, so formatting a file clears that check. clang-tidy checks
the code with every check it has, less those [`.clang-tidy`](.clang-tidy)
turns off: a few that cannot apply here, and those the code does not yet
meet, each with how many places need cleaning up and one of them. Every
finding is an error.

CI runs both, with LLVM 23.1, and fails on any finding. Their results change
from one LLVM version to the next, so install the same version:

```bash
uv tool install "clang-format==23.1.*"
uv tool install "clang-tidy==23.1.*"
```

After a build, from this directory, to format every file and then check
them:

```bash
git ls-files -z '*.cpp' '*.h' '*.hpp' | xargs -0 clang-format -i
run-clang-tidy.py -p build/Release -quiet
```

[cppcheck](https://cppcheck.sourceforge.io/) checks the code as well, with
every check it has, less those
[`cppcheck-suppressions.txt`](cppcheck-suppressions.txt) turns off, in the
same way as `.clang-tidy`. Some of those findings are on purpose or
mistaken, and the file says which. A line there that matches no finding
fails the check, so remove a check's lines once its places are clean.

CI runs Cppcheck 2.22.0, which the PyPI package `cppcheck` 1.5.3 carries.
Install the same:

```bash
uv tool install "cppcheck==1.5.3"
```

After a build, from this directory:

```bash
cppcheck --project=build/Release/compile_commands.json -D__linux__ \
  --enable=all --inconclusive --check-level=exhaustive \
  --library=googletest --inline-suppr \
  --suppressions-list=cppcheck-suppressions.txt \
  --error-exitcode=1 --quiet
```

cppcheck does not know the compiler's own macros, so `-D__linux__` names a
platform, on macOS too; without one, `src/net/ISocket.cpp` stops at its
`#error`. The POSIX code is the same on both. To check the code only macOS
builds, the classes in `src/crypto/` built on Apple's frameworks, name
`-D__APPLE__` in its place.

A suppression that matches no finding fails the check, and which findings
cppcheck reports depends on the code it reads. Some are only in the fuzzers,
as those the suppressions in `src/types/Integer.hpp` and
`src/types/Json.hpp` match, and some only in the implementations Linux
chooses. So after the build above, which has no fuzzers, or with
`-D__APPLE__`, add `--suppress=unmatchedSuppression`. CI's Ubuntu run, which
reads the most code, is the one that holds every suppression to a finding.

In CI, the lint job builds nothing. On Ubuntu it configures with Clang,
because clang-tidy cannot read the GCC-only warning options in a GCC
configuration's compile commands, and with the fuzzers, so their code is
checked too. On macOS it configures with Apple's Clang, without the
fuzzers, so that the code only macOS builds is checked as well. Each runs
clang-tidy and cppcheck on what CMake writes, and the Ubuntu run checks the
format. The other C++ jobs build and run the tests.

CI also runs [CodeQL](https://codeql.github.com/), GitHub's code scanning,
on the C++ code, without building it, and on the rest of the repository. Its
alerts are in the repository's Security tab, and in a CodeQL check on each
pull request.

---

## Editing in VS Code

The workspace settings use [clangd](https://clangd.llvm.org/) for C++. It
runs the compiler and clang-tidy on the code as you type, with the build's
own compile commands, so the editor shows what the build and CI will
report. It also formats each C++ file when it is saved. To set it up:

1. Install LLVM, which has clangd, with `brew install llvm`.
2. Install the clangd extension (`llvm-vs-code-extensions.vscode-clangd`)
   and CMake Tools (`ms-vscode.cmake-tools`).
3. In your user settings, set `clangd.path` to
   `/opt/homebrew/opt/llvm/bin/clangd`. Homebrew does not put LLVM on the
   `PATH`.
4. Build once, so that `build/Release/compile_commands.json` exists.
   [`.clangd`](.clangd) points clangd at it.

The workspace turns off the Microsoft C/C++ extension's own code checking,
which would compete with clangd; that extension still runs the debugger.
CMake Tools builds with the `conan-release` and `conan-debug` presets that
`conan install` writes.
