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
