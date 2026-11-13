# Libranet in C++

A reference node of [Libranet](../README.md), written in C++17. It is only
begun: for now it builds a program that prints a greeting. What every node
must do is in the specifications, in [`docs/specs/`](../docs/specs/).

---

## Building

The build uses [Conan](https://conan.io/) 2, [CMake](https://cmake.org/)
3.23 or newer, [Ninja](https://ninja-build.org/), and a C++17 compiler. On
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
and presets, and runs CMake. After that first run, CMake can build on its
own:

```bash
cmake --build --preset conan-release
```

For a debug build, pass `-s build_type=Debug` to `conan build`, and use the
`conan-debug` preset. The build goes in `build/Release/` or `build/Debug/`,
and `conan install` writes `CMakeUserPresets.json` here; git ignores both.

CI builds the node and runs it on Ubuntu and macOS.

---

## Checking

clang-format keeps the code in one layout, set in
[`.clang-format`](.clang-format): the WebKit style, with a function's
opening brace on the same line, for now. clang-tidy checks the code with
nearly every check it has; [`.clang-tidy`](.clang-tidy) names the few that
are off, and why. Every finding is an error.

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

In CI, clang-tidy reads a Clang build on Ubuntu, because it cannot read the
GCC-only warning options in a GCC build's compile commands.

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
