# C++ Coding Style

Version 0.1 • November 2026

---

## 1. Purpose

This document describes how C++ is written in a project that follows it: how
libraries and files are laid out, how things are named and typed, how
classes own what they hold, how errors are thrown and caught, and how tests
and fuzzers are written. It names nothing in any one project, so that it can
be carried to another. The code is in `src/`, a directory for each library,
with each library's tests in its `tests/` directory and its fuzzers in
`fuzz/`. A library is called `example` here, and so is its namespace.

The code is C++17, with the compilers' extensions turned off.

A rule here is one of three kinds:

- **Checked by a tool**: the compiler, `clang-format`, `clang-tidy`, or
  `cppcheck` fails the build (§2).
- **Checked as the tests run**: a sanitizer, the standard library's own
  checks, a fuzzer, or the coverage floor fails it (§2).
- **Convention**: nothing checks it; it is kept by hand and in review.

A rule is a convention unless its section says what checks it. Where no rule
covers a case, match the code around it.

What is particular to a project is kept in a document of the project's own:
the helpers these rules lean on, how its tools are run, and the places where
its code departs from the rules. New code follows the rules here. A
departure is not fixed in passing: a change set keeps to its task.

## 2. What the Tools Check

CI runs every tool below on every pull request that changes the C++ code, or
what it is built from. It builds with GCC and with Clang, on each operating
system the project supports, for Debug and for Release, since some warnings come
from only one compiler, or only with optimization.

| Tool | Settings | What it holds to |
| --- | --- | --- |
| The compiler | C++17, no extensions; `-Wall -Wextra -Wpedantic` and more (below); every warning an error | Conversions, shadowing, casts, overrides, fallthrough, and what else the warnings name |
| `clang-format` | `.clang-format`: the WebKit style, braces attached, includes sorted | Layout: indentation, spacing, braces, wrapping, and the order of includes |
| `clang-tidy` | `.clang-tidy`: every check, less those it turns off; every finding an error | Most of the rules below that name a check: casts, `const`, ownership, magic numbers, the length of names, including what is used, likely bugs |
| `cppcheck` | `--enable=all --inconclusive`, less a list of suppressions; any finding fails | Likely bugs, and code that could be simpler |
| Standard library checks | Debug builds | An index past a container's end, the front of an empty container, the value of an empty `optional` |
| Sanitizers | One build with AddressSanitizer and UndefinedBehaviorSanitizer, one with ThreadSanitizer | Memory errors, leaks, undefined behavior, and data races, as the tests run |
| libFuzzer | A fuzzer for each parser, with AddressSanitizer and UndefinedBehaviorSanitizer | That a parser turns down bad input only in its own way, and reads back what it writes (§12) |
| `gcovr` | A floor on the lines the tests run | That the tests reach the code |
| CodeQL | `security-and-quality` | Security and quality alerts, shown on each pull request |

The warnings, besides `-Wall -Wextra -Wpedantic`, are `-Wshadow`,
`-Wcast-align`, `-Wcast-qual`, `-Wnull-dereference`, `-Wdouble-promotion`,
`-Wformat=2`, `-Wnon-virtual-dtor`, `-Wold-style-cast`,
`-Woverloaded-virtual`, `-Wconversion`, `-Wsign-conversion`,
`-Wimplicit-fallthrough`, `-Wsuggest-override`, `-Wextra-semi`, and
`-Wzero-as-null-pointer-constant`; with GCC, also `-Wduplicated-cond`,
`-Wduplicated-branches`, and `-Wlogical-op`. No warning is turned off in the
code: there is no `#pragma` that silences one. When the compiler objects,
the code changes.

`clang-tidy` runs every check it has, to start as strict as can be.
`.clang-tidy` turns off two kinds, each with a comment:

- **Checks that cannot apply or cannot be met**: one that contradicts
  another, one that asks for a library the code does not use, or one that
  forbids what the design needs, such as overloaded operators.
- **Checks the code does not yet meet**, each with how many places need
  cleaning up and one of them. A check's lines are removed once its places
  are clean. New code meets these checks anyway: the list counts what is
  left to clean, and does not grow.

`cppcheck`'s suppressions file is kept the same way, and `cppcheck` fails on
a suppression that matches nothing, so none outlives its finding.

`readability-identifier-naming` is given one rule, the underscore that
starts a private method's name (§4); the other names are a convention.

### 2.1 Turning a Check Off

When `clang-tidy` or `cppcheck` objects, the code changes if the change
makes the code better. Where it would not, as where a C library takes a
pointer of another type, a type is built to convert implicitly, or the tool
is mistaken, the check is turned off as narrowly as it can be:

- **For the whole project**, in `.clang-tidy` or the suppressions file,
  where it cannot apply (§2).
- **For one class**, in an option of the check in `.clang-tidy`'s
  `CheckOptions`, where the check is right but for that class, as for a
  container whose `operator[]` checks its own bounds.
- **For a block of lines**, with `// NOLINTBEGIN(check)` and
  `// NOLINTEND(check)`, around definitions that share one reason, or
  around the tests in a file that all share one.
- **For one line**, with `// NOLINTNEXTLINE(check)` on the line above it.

Each names the check: a bare `NOLINT` is never written. A check that
`clang-tidy` reports under two names, as it does for aliases, is named under
both, or with a pattern (`*-explicit-constructor`). A comment above it says
why:

```cpp
// The socket calls take a sockaddr*, and a sockaddr_storage is made to be
// passed as one.
// NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
const auto* address = reinterpret_cast<const sockaddr*>(&storage);
```

`cppcheck` is turned off for one line with `// cppcheck-suppress <id>` on
the line before, or for a block with `// cppcheck-suppress-begin <id>` and
`// cppcheck-suppress-end <id>`. Where `cppcheck` is wrong, the comment
says so, starting "Mistaken:".

## 3. Layout of a File

### 3.1 Libraries and Files

- **A library is a directory under `src/`**, with a CMake target of its own
  and an alias for it, as `project::example`, which other targets link to.
  Its code is in one namespace, named for the directory.
- **A file is named for the class it holds**, in PascalCase: a header ends
  in `.hpp` (`RecordStore.hpp`), a source file in `.cpp`. A header holds one
  class, with the small types only it uses, nested in it or beside it. A
  header of helpers for one outside library holds several, and is named for
  the library.
- **Every header starts `#pragma once`**, in place of an include guard.
- **Most code is in headers.** A class is declared, and its members are
  defined below it in the same header, each marked `inline`. A `.cpp` file
  holds what a header must not: the definition of a static data member, code
  that includes a platform's system headers, such as sockets, and a factory
  that picks an implementation for the platform (§6.2). A library with no
  `.cpp` file is a CMake `INTERFACE` library.
- **Code built on one platform or library is in a file named for it**, as
  `ClockPosix.cpp`, `HashMacos.hpp`, or `HashOpenSsl.hpp`. A file that only
  one platform builds is wrapped whole in `#if` (§10.5).

### 3.2 Order

A header reads top to bottom in this order:

1. `#pragma once`.
2. Includes (§3.3).
3. The library's namespace, opened.
4. A comment saying what the class is (§8), then the class.
5. The definitions of the class's members.
6. The namespace, closed, with a comment naming it:
   `} // namespace example`. `clang-tidy` checks the comment.

A `.cpp` file reads:

1. Its own header, alone.
2. The other includes.
3. An anonymous namespace holding the file's private helpers: the steps
   pulled out of its class's methods (§6.8), the functions that wrap a C
   library's calls, deleters, and small types.
4. The library's namespace, with the definitions.

Within a class, `public:` comes first, then `protected:`, then `private:`.
The public section has, in order, its type aliases and nested types, its
static factories, its constructors and special members (§6.3), its
methods, and its operators. The private section has its type aliases and
nested types, then its constants (§7), then its data members, then its
methods.

### 3.3 Includes

```cpp
#include "RecordStore.hpp"

#include <cstddef>
#include <memory>
#include <string>

#include <sys/socket.h>
#include <unistd.h>

#include <sqlite3.h>

#include "Record.hpp"
#include "other/Clock.hpp"
```

- **A `.cpp` file includes its own header first**, alone.
- **Then blocks, a blank line between each**: the C++ standard library; the
  operating system's headers; each outside library's; and the project's
  own. `clang-format` sorts each block, in case-sensitive order, as
  `clang-tidy`'s `llvm-include-order` wants.
- **A test starts with GoogleTest's header**, in the block of the standard
  library's, where `llvm-include-order` puts it first.
- **A header in the same library is included by its name**
  (`"Record.hpp"`); one in another library by its path from `src/`
  (`"other/Clock.hpp"`). Tests and fuzzers include by the path from `src/`.
- **The standard library's C headers are its C++ ones**: `<cstdint>`, not
  `<stdint.h>`, which `modernize-deprecated-headers` checks.
- **A file includes every header it uses a name from, and no other.**
  `clang-tidy`'s `misc-include-cleaner` checks it, but for the operating
  system's own headers, which differ from one system to the next.
- **Library code spells out where each name comes from**: `std::string`,
  `other::Clock`. A test or fuzzer may bring a name in with a
  using-declaration, after its includes: `using example::RecordStore;`. A
  using-directive, `using namespace`, is only for a namespace of literals,
  `using namespace std::chrono_literals;`, which
  `google-build-using-namespace` checks.

### 3.4 Blank Lines

`clang-format` keeps at most two blank lines in a row, and otherwise leaves
them as they are. Inside a function, these are kept by hand:

- A compound statement (`if`, `for`, `while`, `do`, `switch`, `try`) has a
  blank line before it and after it, unless it opens or closes its block. A
  comment above it goes with it, after the blank line.
- `else` and `catch` go on the line of the closing brace before them,
  `} else {`, where `clang-format` puts them.
- Elsewhere, blank lines split a function into its steps, as paragraphs
  split prose. Most often a step's declarations come first, then a blank
  line, then what is done with them.

```cpp
inline std::vector<Record> Record::load_all(std::istream& in_stream) {
    std::vector<Record> records;
    std::string line;

    while (std::getline(in_stream, line)) {
        if (line.empty()) {
            continue;
        }

        records.push_back(Record::parse(line));
    }

    if (in_stream.bad()) {
        throw std::runtime_error("Cannot read the records");
    }

    return records;
}
```

Tests differ in one way: blank lines also separate setting up, acting, and
checking (§11).

### 3.5 Format and Line Length

`clang-format` sets the layout, from `.clang-format`, and CI fails on any
change it would make. The WebKit style it is based on indents by four
spaces, does not indent a namespace's contents, puts a constructor's
initializers one to a line with the comma leading, and indents `case`
labels. Braces are attached: a function's opening brace is on its line.

- **Every `if`, `for`, `while`, and `do` has braces**, even around one line.
  `readability-braces-around-statements` checks it.
- **`*` and `&` go with the type**: `const Record& record`,
  `const char* name`. `clang-format` puts them there.
- **Code runs to 100 columns**, kept by hand: WebKit sets no limit, so
  `clang-format` does not wrap lines. Prose is wrapped at 79 columns, in
  comments.

## 4. Names

| Thing | Named | Examples |
| --- | --- | --- |
| Namespace | For its library, one lower-case word | `storage`, `net` |
| Class, struct, enum, type alias | PascalCase | `RecordStore`, `Milliseconds`, `Ptr` |
| Interface | `I`, then what it is | `IStore`, `IClock` |
| Implementation | What it is, then what it is built on | `StoreSqlite`, `ClockPosix`, `HashOpenSsl` |
| Function and method | snake_case | `save_all()`, `local_address()` |
| Function returning a value | For the value, with no `get_` | `host()`, `size()`, `digest()` |
| Setter | `set_` and what it sets; its parameter `new_` and the same | `set_path(std::string new_path)` |
| Predicate | `is_…`, `has_…`, or as the standard library names it | `is_open()`, `has(key)`, `empty()` |
| Variable and parameter | snake_case, words spelled out | `record_id`, `socket_fd` |
| Private method | `_` and snake_case | `_parse_value()`, `_read_body()` |
| Data member | `m_` and snake_case | `m_records`, `m_socket_fd` |
| A struct's member | snake_case, with no `m_` | `begin`, `der_bytes` |
| Constant | snake_case | `max_depth`, `bits_per_byte` |
| Enumerator | PascalCase | `Direction::Send`, `Mode::Cbc` |
| Template parameter | PascalCase, for what it stands for | `Type`, `Predicate`, `Function` |
| Test suite and test | The class and `Test`; then PascalCase, for what it checks | `RecordTest`, `EmptyInputIsRefused` |

- **An acronym is written as a word**: `Json`, `Url`, `OpenSsl`, `RsaKey`,
  and in snake_case `from_pem`, `to_json`. A name an outside library sets
  keeps its spelling, as Apple's `CF` prefix does in a class that holds its
  objects.
- **Units go in the name** of a number that holds a quantity in a unit its
  type does not carry, `key_bits`, `memory_cost_kib`, but for bytes: a
  size, length, or count with no unit named is in bytes, as `size`,
  `digest_size()`, and `salt_length` are. A duration is a `std::chrono` type
  (§5), whose unit is its type.
- **Words are spelled out**: `position`, `element`, `buffer`, `result`,
  `object`, not `pos`, `elem`, `buf`, `res`, `obj`. A loop's index is
  `index`, or `…_index`. The two operands of an operator are `left` and
  `right`. `clang-tidy` holds a variable or parameter to three characters at
  least (`readability-identifier-length`).
- **A private method's name starts with an underscore**, `_parse_value()`,
  so that a call shows it is the class's own detail.
  `readability-identifier-naming` checks it. A constructor, a destructor,
  and an operator are named by the language, and a function in an
  anonymous namespace is no method, so none of them has one. A data member
  starts `m_`, and no name ends with an underscore.
- **A setter's parameter is `new_` and the member's name**, so that it does
  not share its name with the getter, which `cppcheck`'s `shadowFunction`
  reports.
- **A parameter that must be taken but is not used** is left unnamed, its
  name in a comment: `void ignore(const std::string& /*unused*/)`. In an
  override, where the name says what the base class passes, it is kept and
  marked `[[maybe_unused]]`. `misc-unused-parameters` checks every other
  parameter is used.

A static factory is named for what it builds from, or for what it makes:

| Name | Builds |
| --- | --- |
| `parse(text)` | From text, refusing what it cannot read (§10.1) |
| `from_pem(text)`, `from_der(bytes)` | From an encoding the name gives |
| The algorithm's name, as it is written: `Sha256()`, `Rsa(key_bits)` | A new instance of an implementation of an interface (§6.2) |
| The operation's verb: `bind(address)`, `connect(address)` | What the operation makes |

A type that has a text form gives it with `to_string()`, and reads it with
`parse()`, or with `operator>>` from a stream.

## 5. Types

- **Integers of a fixed width**, always with `std::`: `std::int64_t`,
  `std::uint16_t`, `std::size_t`. `long` and `long long` are not named,
  since their sizes differ by platform, which `google-runtime-int` checks.
  `int` is used where a C library's calls take it, as for a file descriptor.
- **Unsigned for bits**: a value that is shifted or masked is unsigned, and
  so are its literals, `0x3FU`. A literal's suffix is in upper case,
  `readability-uppercase-literal-suffix`.
- **Bytes are `std::byte`**, held in a `std::vector<std::byte>`. A class
  that uses one much names it, `using Data = std::vector<std::byte>;`. Text
  is `std::string`.
- **Text that is only read is a `std::string_view`** parameter. Text that is
  kept is taken by value and moved (§6.5).
- **`std::optional` for a value that may be missing**, never a value of the
  type set aside to mean none, unless a C library's calls already use one,
  as -1 for no file descriptor.
- **`std::chrono` for durations**: a parameter is `std::chrono::milliseconds`
  or a class's alias for it, `Milliseconds`, never a bare number.
- **`enum class`, with the smallest underlying type that holds it**:
  `enum class Direction : std::uint8_t`. `cppcoreguidelines-use-enum-class`
  and `performance-enum-size` check both.
- **`std::variant` for a value of one of several kinds**, read with
  `std::get_if` or `std::visit`.
- **`std::array` for an array of fixed size**, never a C array, which
  `modernize-avoid-c-arrays` checks.
- **Ownership is in the type.** `std::unique_ptr` has one owner, and
  `std::shared_ptr` is only for what owners share, such as a message
  delivered to several queues. A raw pointer or a reference never owns. An
  interface names its pointer `Ptr`: `using Ptr = std::unique_ptr<IStore>;`.
- **`auto` where the type is in view or does not matter**:
  `const auto record = Record::parse(text);`. A pointer is `auto*`, which
  `readability-qualified-auto` checks. A literal given a name keeps its type
  with `constexpr auto` (§7).
- **`const` wherever a value does not change**: locals, references,
  pointers to what is not changed, and methods that change nothing.
  `misc-const-correctness` and `readability-make-member-function-const`
  check it. `const` goes before the type: `const auto&`.
- **`nullptr`**, never `0` or `NULL`, which `-Wzero-as-null-pointer-constant`
  and `modernize-use-nullptr` check.
- **`using` for a type alias**, never `typedef`, which `modernize-use-using`
  checks.

## 6. Classes and Functions

### 6.1 Methods Over Functions

Behavior belongs on the type it is about. That includes factories: a parser
is `Endpoint::parse(text)`, not `parse_endpoint(text)` beside it.

A set of operations with no state of its own, such as an encoding, is a
class of static members whose six special members are deleted, so that it
is neither made nor copied:

```cpp
// Base32, as RFC 4648 §6 gives it, with padding.
class Base32 {
public:
    [[nodiscard]] static std::string encode(const std::vector<std::byte>& data);
    [[nodiscard]] static std::vector<std::byte> decode(std::string_view text);

    Base32() = delete;
    ~Base32() = delete;
    Base32(const Base32&) = delete;
    Base32& operator=(const Base32&) = delete;
    Base32(Base32&&) = delete;
    Base32& operator=(Base32&&) = delete;
};
```

So the only functions a library has outside a class are its operators,
`operator==` and `operator<<` among them, and the private helpers of a
`.cpp` file, which are in an anonymous namespace
(`misc-use-anonymous-namespace`, `misc-use-internal-linkage`). Most of those
wrap a C library's calls. An operator that reads both operands' private
members is declared a `friend` in the class.

### 6.2 Interfaces and Implementations

An interface is a class of pure virtual methods, named `I…`. It holds, in
order: its `Ptr`; its static factories, which return a `Ptr`; its methods;
a virtual destructor, defaulted; copying, deleted; moving, defaulted and
`noexcept`; and a protected default constructor, so that only an
implementation makes one.

```cpp
class IChecksum {
public:
    using Ptr = std::unique_ptr<IChecksum>;

    [[nodiscard]] static Ptr Crc32();

    virtual void update(std::string_view data) = 0;
    [[nodiscard]] virtual std::uint32_t value() const noexcept = 0;

    virtual ~IChecksum() = default;
    IChecksum(const IChecksum&) = delete;
    IChecksum& operator=(const IChecksum&) = delete;
    IChecksum(IChecksum&&) noexcept = default;
    IChecksum& operator=(IChecksum&&) noexcept = default;

protected:
    IChecksum() = default;
};
```

An implementation is named for what it is and what it is built on
(`Crc32Zlib`, `Crc32Macos`). It marks each method it overrides `override`,
and never repeats `virtual`, which `modernize-use-override` checks; its
destructor too, `~Crc32Zlib() override = default;`. Only an interface is
inherited from more than once, `fuchsia-multiple-inheritance`.

The factory is defined in the interface's `.cpp` file, which includes the
implementation the platform has and names it with an alias:

```cpp
#include "IChecksum.hpp"

#include <memory>

#if __APPLE__
#include "Crc32Macos.hpp"
using Crc32Type = example::Crc32Macos;
#else
#include "Crc32Zlib.hpp"
using Crc32Type = example::Crc32Zlib;
#endif

namespace example {

IChecksum::Ptr IChecksum::Crc32() {
    return std::make_unique<Crc32Type>();
}

} // namespace example
```

So code that uses an interface includes only the interface, and never names
an implementation. The interface's tests run on what the factory makes, and
on each other implementation the platform can build (§11).

### 6.3 Special Member Functions

The special members are the destructor, and the copy and move constructors
and assignments.

- **A class declares all five or none**, which
  `cppcoreguidelines-special-member-functions` checks. A value that needs
  none of its own declares none, and the compiler makes them.
- **Each one declared says what it is**: `= default`, `= delete`, or a
  definition. A deleted one is public, so that the compiler's error names
  the deletion rather than its access, which `modernize-use-equals-delete`
  checks.
- **A member that cannot be copied or moved**, such as a mutex, a thread, or
  a callback that holds `this`, makes its class delete the operations it
  stops, not default them: a defaulted one the compiler cannot make is
  deleted without a word. Where the reason is not plain, a comment says it.
- **A defaulted move is `noexcept`**, so that a container moves rather than
  copies, which `performance-noexcept-move-constructor` checks.
- **A base class that is copied only as its own type** has its copy and move
  protected, so that copying through the base cannot slice.
- **A data member is neither `const` nor a reference**, either of which
  would stop the class being assigned:
  `cppcoreguidelines-avoid-const-or-ref-data-members`.

### 6.4 A Type Checks Its Own Values

The rules on what a value may be are checked by its type, in its
constructor, so that no invalid object exists however it was built. A
constructor or factory refuses arguments it cannot work with by throwing
`std::invalid_argument` (§9.1). A parser checks only that the text has the
right form, then constructs.

```cpp
class Endpoint {
public:
    using Port = std::uint16_t;

    // Reads "host:port", or "[address]:port" for IPv6. Throws
    // std::invalid_argument for text that is not one.
    [[nodiscard]] static Endpoint parse(std::string_view text);

    Endpoint(std::string host, Port port);

    [[nodiscard]] const std::string& host() const noexcept;
    [[nodiscard]] Port port() const noexcept;
    [[nodiscard]] std::string to_string() const;

private:
    std::string m_host;
    Port m_port;
};

inline Endpoint::Endpoint(std::string host, Port port)
    : m_host(std::move(host))
    , m_port(port) {
    if (m_host.empty()) {
        throw std::invalid_argument("An endpoint needs a host");
    }
}
```

- **A constructor of one argument is `explicit`**, and so is a conversion
  operator, `explicit operator bool()`, which `google-explicit-constructor`
  checks. A type made to convert implicitly, as a number does from the
  built-in integers, marks each such constructor
  `NOLINTNEXTLINE(*-explicit-constructor)`, with a comment saying why.
- **A member is initialized where it is declared** when its first value is
  always the same, `std::size_t m_depth = 0;`
  (`modernize-use-default-member-init`), and otherwise in the constructor's
  initializer list, never assigned in its body
  (`cppcoreguidelines-prefer-member-initializer`). Every member and every
  local is initialized: `cppcoreguidelines-pro-type-member-init` and
  `cppcoreguidelines-init-variables`.
- **A plain aggregate is a `struct`**: public data, with no rule on its
  values and no methods. So is a function object with no state, such as a
  deleter (§6.6) or a comparison. Anything else is a `class`, with its data
  private, which `misc-non-private-member-variables-in-classes` checks.

### 6.5 Parameters and Results

- **What is passed in**: a built-in type or a small value by value; text
  that is only read as `std::string_view`; another object that is only read
  as `const T&`; and a value the function keeps by value, then moved into
  place. `modernize-pass-by-value` and `performance-unnecessary-value-param`
  check the last two. A forwarding reference is passed on with
  `std::forward` (`cppcoreguidelines-missing-std-forward`).
- **A parameter passed by reference to what is not `const`** is one the
  function changes on purpose: a cursor it moves past what it read, a stream,
  or a buffer it fills.
- **A result is returned**, not written to a parameter: a value, an
  `std::optional` where there may be none, or a struct where there are
  several.
- **Two parameters of one type side by side are easily swapped.** A struct
  or a distinct type keeps them apart, as
  `bugprone-easily-swappable-parameters` asks.
- **A default argument gives the common case**, as `timeout = WaitForever`.
- **`[[nodiscard]]` marks a function whose result is its point.**
  `modernize-use-nodiscard` asks it of a `const` method; a factory and a
  parser have it too.
- **`noexcept` marks what cannot throw**: an accessor, a move, a swap.
  `bugprone-exception-escape` checks that a `noexcept` function throws
  nothing.
- **A lambda names what it captures**, as `[this]` or `[&total]`. A test may
  capture `[&]`.

### 6.6 Resources

Each resource is owned by an object that releases it in its destructor.

- **Memory** is made with `std::make_unique` or `std::make_shared`
  (`modernize-make-unique`, `modernize-make-shared`). `new` is written only
  where those cannot reach a private constructor, and its result goes
  straight into a `std::unique_ptr`. There is no `delete`, `malloc`, or
  `free`: `cppcoreguidelines-owning-memory` and
  `cppcoreguidelines-no-malloc`.
- **A C library's object** is held in a `std::unique_ptr` whose deleter
  calls the library's own function to free it:

  ```cpp
  // Frees what getaddrinfo() returned.
  struct AddressInfoDeleter {
      void operator()(addrinfo* value) const noexcept {
          ::freeaddrinfo(value);
      }
  };

  using AddressInfo = std::unique_ptr<addrinfo, AddressInfoDeleter>;
  ```

- **An object with its own reference count** is held by a small class
  template that releases it, and whose constructor says whether it takes
  over the reference it is given or adds one.
- **A file descriptor** is closed by the destructor of the class that opened
  it. Its `close()` may be called twice.
- **A thread** is joined by the destructor of the object that started it,
  which stops it first.
- **A key or a password** is wiped from memory by the destructor of what
  holds it.

### 6.7 Shared State

- State that more than one thread touches is guarded by a `std::mutex`
  member, `mutable` where a `const` method locks it, and locked for a block
  with `const std::scoped_lock lock(m_mutex);`, or with a `std::unique_lock`
  where a condition variable waits on it.
- A condition variable always waits with a predicate,
  `m_not_empty.wait(lock, [this] { return !m_queue.empty(); });`. The lock is
  released before the condition is notified.
- A flag that one thread sets and another reads is a `std::atomic<bool>`.
- There is no global or static state that changes
  (`cppcoreguidelines-avoid-non-const-global-variables`), and no static
  object that must be built before `main` runs.

ThreadSanitizer checks what the tests run of this (§2).

### 6.8 Short Functions

A function is written so that someone who knows what the code is for, but
not C++, can read it, see what it does, and check that it is right. It reads
as the steps it takes, each named in the words of what the code is about,
and the detail of each step is in a function of its own.

```cpp
// Reads one request from the connection and answers it. Whether the
// connection is kept open for another comes back.
inline bool Connection::serve_one() {
    const auto request = _read_request();

    if (!request) {
        return false;
    }

    const auto response = m_handler.answer(*request);

    _send(response);
    return _keeps_alive(*request, response);
}
```

- **A function or method is generally under 30 lines of code, and never over
  50.** Its lines of code are the lines of its body, between its braces,
  that hold code: blank lines, and lines that hold only a comment, are not
  counted. Tests are held to this too.
- **A longer one is split into the logical parts it is made of.** A part
  that is one step, with a name of its own, is pulled out into a function or
  a method named for what it does, and the longer one calls it. Where the
  class has a `.cpp` file, the part is a function in its anonymous
  namespace, and is passed the members it uses, so that the header shows no
  more than it must. In a class with only a header, it is a private method,
  named with an underscore (§4).
- **A part is not pulled out only to make a function shorter.** Each has a
  name that says what it does, and its caller reads as well without its
  detail as with it.

Nothing checks the length. `clang-tidy`'s `readability-function-size`
counts blank lines and comments with the code, so it is not set to.
`readability-function-cognitive-complexity` holds how much a function
branches and loops, and how deeply, which a short function can still
overdo.

## 7. Constants

```cpp
// Lists may hold each other this deep, and no deeper: each level read takes
// stack, and the input may come from anyone.
static constexpr std::size_t max_depth = 128;
```

- A constant is `constexpr`, with its type, or with `constexpr auto` where
  the literal's own type is wanted.
- The comment above it says why it has that value, or what it means, and
  names the specification section that sets it.
- A number with a meaning gets a name, rather than appearing bare in a
  function. `readability-magic-numbers` checks it for every number but the
  smallest, 0 to 4.
- A constant is declared where it is used: in the function, where only one
  uses it; in the class's private section, where its methods share it; and
  public only where a caller needs it.
- Text with a meaning is a `static constexpr std::string_view`.

## 8. Comments

A comment says why, not what: the reason for a value, the case a branch
covers, the thing that would break if the line changed. Comments are wrapped
at 79 columns, above the code they are about, and need not be whole
sentences: a phrase that says it is enough. They are `//` comments. There is
no documentation generator, so they hold no markup: code is named as it is
spelled, a function with its parentheses, `parse()`.

```cpp
// Sorted, so that the same entries always hash alike, whatever order the
// directory listed them in.
std::sort(names.begin(), names.end());
```

- **A class has no comment above it unless the comment adds something.** A
  class should be clear from its name and its public interface. A comment is
  added where something more needs saying, or where something about how the
  class is built must be known to understand it: what an implementation is
  built on, a format it reads or writes, a limit it holds to.
- **A function whose name and types do not tell a caller all they need**
  has a comment, above its declaration or its definition, saying what it
  gives, as a noun phrase ("Whether password makes this hash, compared in
  constant time."), or what it does, with a verb ("Reads the digits at the
  start of text, and moves past them."), and what it throws.
- **A comment names the specification it follows**, and the section:
  "RFC 3986 §2.3".
- **A comment at the end of a line is short**: a label, a unit, a range
  (`// 4xx`), or a tool's pragma (`// IWYU pragma: keep`). Anything longer
  goes above the line.
- **A comment that turns a check off** gives its reason (§2.1).

**Commented-out code is never committed.** Code that is no longer wanted is
removed; version control keeps what it was.

Banners do not divide a file into sections. A particularly long file may
mark its sections instead, each with a comment that starts `MARK:`, as
`// MARK: Parsing`, which VS Code shows as a heading in the editor's
minimap.

There are no `TODO` or `FIXME` comments. Work left over is recorded in the
project's plan or an issue.

## 9. Errors

### 9.1 Throwing

An error is an exception. A function does not return an error code, except
where its result already says that it found nothing: an empty
`std::optional`, `false`, or a stream left failed, which is how a parser may
turn input down (§10.1). What is thrown is the standard type that fits:

| Type | When |
| --- | --- |
| `std::invalid_argument` | An argument or an input is not what it must be: text that does not parse, a size out of range |
| `std::domain_error` | A value is asked for as a kind it is not, as a string read as a number |
| `std::out_of_range` | An index or a key is past what there is |
| `std::logic_error` | The object's state does not allow the call, as sending on a closed socket |
| `std::runtime_error` | Something outside the program failed, as a library's call or a name lookup |
| `std::system_error` | A system call failed: with `errno`, and the call's name as its message |

- **A library that reports its own failures has an exception of its own**,
  derived from the type that fits, which adds what the library said went
  wrong.
- **A message starts with a capital and has no full stop**, and says what
  was expected or what failed: `"Cannot listen on a closed socket"`. A value
  from outside is added where it helps, after a colon:
  `"Missing colon in header line: " + std::string(line)`.
- **An exception is thrown by value and caught by reference**,
  `misc-throw-by-value-catch-by-reference`.
- **`assert` is not used.** Source code throws, since a Release build
  compiles an `assert` away; tests use GoogleTest's checks.
- **A destructor never throws.** One that calls what may throw catches it,
  and where nothing is left that is safe to do, as when a thread cannot be
  stopped, it calls `std::terminate()`. A failure it cannot report, such as
  closing a descriptor, it lets go, with the result cast to `void`.

### 9.2 Catching

Catch the narrowest type that can be thrown, by `const` reference, and name
it `error` where it is read: `catch (const std::invalid_argument&)`,
`catch (const std::exception& error)`. A handler does one of three things:

1. It turns the failure into the function's own way of refusing: it returns
   an empty `std::optional` or `false`, or fails the stream.
2. It throws, as its own type, saying what it caught.
3. It reports it (§9.3).

A handler that does the first and could look like it swallows an error has
a comment saying what it refuses. `catch (const std::exception& error)` is
only for a boundary that must survive what it runs, as a worker thread's
loop calling a callback, and reports what it caught. `catch (...)` is only
for ending the program, in a destructor.

### 9.3 Reporting

Library code does not print, and has no global logger. A class that must
report what it cannot throw, as a worker thread does, is given a function
to report it with, `std::function<void(std::string)>`, in its constructor,
which does nothing unless one is given. `std::cout` and `std::cerr` are
only for a program's `main` and for fuzzers.

Data that is not what it should be is always reported, never skipped in
silence.

## 10. Defensive Checks

### 10.1 Untrusted Input

Input from a peer, a client, or a file is read by a parser, which is held
to these:

- **It turns down what it cannot read in one way**, which the comment on its
  declaration names: one exception type, an empty `std::optional`, or a
  failed stream. Anything else it does with an input, such as crashing,
  hanging, or throwing another type, is a fault, which its fuzzer looks for
  (§12).
- **What it reads, it writes back the same**: a value read and written out
  again reads back as the same value, and the text as the same text.
- **It bounds what an input can make it spend**: how deep it may nest, and
  how much memory it may take, as by reading a body in pieces rather than
  making room first for the length the input claims.
- **It reads with checked access**: `.at()`, not `operator[]`, which
  `cppcoreguidelines-pro-bounds-avoid-unchecked-container-access` checks.
  A loop whose index its own condition bounds, and that must run fast, may
  turn the check off for its block (§2.1). The Debug build's standard
  library checks, and the sanitizers, check those as the tests run.
- **A number it reads is range-checked before it is narrowed**, against
  `std::numeric_limits` of the type it is narrowed to.

### 10.2 Every Kind Is Checked

Code that branches on which kind a value is tests each kind by name:
`std::holds_alternative` or `if constexpr` for a `std::variant`,
`dynamic_cast` for what is behind an interface. The last branch reports or
throws; it never takes "none of the others" to mean the last kind:

```cpp
if (const auto* opened = dynamic_cast<const Opened*>(event.get())) {
    _open(*opened);
    return;
}

if (const auto* closed = dynamic_cast<const Closed*>(event.get())) {
    _close(*closed);
    return;
}

m_report("Cannot handle an event of an unknown kind: " + event->to_string());
```

A `switch` on an `enum class` names each enumerator and has no `default`, so
that `-Wswitch` reports one added later. A `default` is only for values
outside the enumerators that can still arrive, as a number cast from what a
peer sent.

### 10.3 Stop Conditions Are Inclusive

A check that ends a loop or a count compares with `<`, `<=`, `>=`, or `>`,
not `==` or `!=`, so that a value already past the limit still stops it:
`while (pending_bits >= bits_per_character)`. Validation can be bypassed,
and `==` would then never be true. An iterator is compared with `!=` against
its `end()`, which it cannot pass.

### 10.4 Casts and Raw Memory

- **`static_cast` converts.** There are no C-style or functional casts,
  which `-Wold-style-cast` and `google-readability-casting` check, and every
  narrowing is written out, which `-Wconversion`, `-Wsign-conversion`, and
  `bugprone-narrowing-conversions` check.
- **`dynamic_cast` tells what is behind an interface**; a `static_cast` is
  never used to cast down to a derived class.
- **`reinterpret_cast`, `const_cast`, pointer arithmetic, and C's variable
  arguments** are used only where a C library's calls take them, each
  turned off for its line or block with its reason (§2.1). They are kept in
  one small helper where they can be, as in a class that hands bytes to a C
  library as `unsigned char*`.
- **A result thrown away on purpose is cast to `void`**: `(void)::close(fd);`.
- **A pointer is compared with `nullptr`**, which goes on the left (§10.6):
  `if (nullptr == value)`. `readability-implicit-bool-conversion` checks
  that it is compared, outside templates. A smart pointer or an
  `std::optional` is tested as a condition: `if (!key)`.

### 10.5 Platforms

- **A platform is chosen with the compiler's own macro**: `#if __APPLE__`,
  or `#if defined(__linux__) || defined(__APPLE__)` for more than one. Its
  `#endif` names what it closes, `#endif // __APPLE__`, where it is far
  from its `#if`.
- **A platform the code does not support stops the build**:
  `#error "Unsupported platform"` after the last `#elif`.
- **As little is chosen by the preprocessor as can be**: an include and an
  alias, as in a factory (§6.2), or a whole file. There are no other macros:
  a constant is `constexpr`, and a function is a function, which
  `cppcoreguidelines-macro-usage` checks.

### 10.6 Constants on the Left

In a comparison with `==`, a constant goes on the left, and what varies on
the right: `if (nullptr == value)`, `if (0 == count)`,
`if (Mode::Cbc == m_mode)`. A constant is a literal, `nullptr`, an
enumerator, or a named constant. If `=` is typed for `==` by mistake,
`if (0 = count)` does not compile, where `if (count = 0)` would assign. A
comparison with `!=` cannot be mistaken for an assignment, and may be
written either way.

## 11. Tests

- **GoogleTest, a file per class**: the class in `src/{library}/{Class}.hpp`
  is tested by `src/{library}/tests/Test{Class}.cpp`, and a library's test
  files build into one test program. A class tested only through another,
  as an implementation through its interface, has no file of its own.
- **A test is `TEST({Class}Test, {WhatItChecks})`**, the name in PascalCase,
  saying what is checked: `EmptyInput`, `DigestResets`,
  `MissingPortIsRefused`.
- **`EXPECT_…` checks; `ASSERT_…` is only for what the rest of the test
  cannot do without**, as before reading through a pointer or an
  `std::optional`.
- **A file's helpers and data are at the top**, in an anonymous namespace,
  each with a comment, after the using-declarations.
- **An interface's tests are `TEST_P`**, on a `testing::TestWithParam` of a
  function that makes the implementation. They are instantiated once with
  the interface's factory, as `Factory`, and once for each other
  implementation the platform can build:

  ```cpp
  INSTANTIATE_TEST_SUITE_P(Factory, IChecksumTest, testing::Values(&IChecksum::Crc32));
  #if __APPLE__
  INSTANTIATE_TEST_SUITE_P(Zlib, IChecksumTest, testing::Values(&make_zlib));
  #endif
  ```

- **Inputs are named, expected answers are literal.** A number the test
  passes in is a named constant, `constexpr auto port = 8080;`, as
  `readability-magic-numbers` asks, and an answer that must equal it is
  checked against the name. A value the test only expects back is written
  as a literal in the `EXPECT_…`, where the check does not look.
- **Known answers come from outside**: test vectors from the specification
  that gives them, or what another implementation wrote, with its source in
  the comment above.
- **A test that waits on another thread waits on a condition**, with a
  generous timeout that ends as soon as the condition holds. A fixed sleep
  never decides whether the test passes, but to show that something does
  not happen in that time.
- **Many inputs that should each be refused** are checked with a helper
  that says whether one was, `EXPECT_TRUE(refused(text))`, rather than
  `EXPECT_THROW` in a loop, which makes the test too complex for
  `readability-function-cognitive-complexity`.
- **A network test uses the loopback address and port 0**, so the system
  picks a free port.
- **There are no mocks.** A stand-in is a small class written in the test
  file.
- **A test reaches only what is public.**

```cpp
#include <gtest/gtest.h>
#include <stdexcept>
#include <string>

#include "example/Endpoint.hpp"

using example::Endpoint;

namespace {

// Whether Endpoint::parse() turns down text as invalid.
bool refused(const std::string& text) {
    try {
        (void)Endpoint::parse(text);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

TEST(EndpointTest, HostAndPort) {
    constexpr auto port = 8080;

    const auto endpoint = Endpoint::parse("example.com:" + std::to_string(port));

    EXPECT_EQ(endpoint.host(), "example.com");
    EXPECT_EQ(endpoint.port(), port);
}

TEST(EndpointTest, MissingPortIsRefused) {
    EXPECT_TRUE(refused("example.com"));
    EXPECT_TRUE(refused("example.com:"));
}
```

Tests are held to every check that source code is. Where every test in a
file trips one for the same reason, as reading an `std::optional` after
`ASSERT_TRUE(value.has_value())`, which `bugprone-unchecked-optional-access`
does not follow, the check is turned off around the file's tests, with the
reason above (§2.1).

The tests run in the Debug build, with the standard library's checks, and in
both sanitizer builds, and new code comes with tests that hold coverage above
its floor.

## 12. Fuzzers

- **Each parser of text from outside has a fuzzer**:
  `src/{library}/fuzz/Fuzz{Class}.cpp`, built only with LLVM's Clang, with
  AddressSanitizer and UndefinedBehaviorSanitizer.
- **Its file opens with a comment saying how the parser turns down input it
  cannot read.** That, and reading the input, are the only outcomes the
  fuzzer allows.
- **It copies the input into a string**, parses it inside `try`, and
  returns 0 when the parser refuses it in its own way.
- **What parses, it writes back out and reads again**, outside the `try`,
  and checks that both the value and the text come back the same. If not, it
  prints what it wrote to `std::cerr` and calls `std::abort()`, which
  libFuzzer reports as a fault.
- **A fault a fuzzer finds becomes a test** that fails without the fix.

```cpp
// libFuzzer calls this with inputs it makes up. Endpoint::parse() turns down
// text it cannot read with std::invalid_argument; anything else it does with
// an input, such as crash, hang, or throw something else, is a fault. What
// it reads, it must write as text it reads back as the same endpoint.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#include "example/Endpoint.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    std::string text;

    try {
        text = example::Endpoint::parse(input).to_string();
    } catch (const std::invalid_argument&) {
        return 0;
    }

    if (example::Endpoint::parse(text).to_string() != text) {
        std::cerr << "Endpoint did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
```
