"""Conan recipe for building the C++ Libranet node with CMake and Ninja."""

from conan import ConanFile
from conan.tools.build import check_min_cppstd
from conan.tools.cmake import CMake, CMakeToolchain, cmake_layout


class LibranetConan(ConanFile):
    """The settings, layout, and generated CMake files for the C++ node."""

    settings: tuple[str, ...] = ("os", "compiler", "build_type", "arch")
    generators: str = "CMakeDeps"

    def layout(self) -> None:
        """Put the build in build/<build type>, with presets for CMake."""
        cmake_layout(self)

    def validate(self) -> None:
        """Refuse a profile whose C++ standard is older than C++17."""
        check_min_cppstd(self, "17")

    def generate(self) -> None:
        """Write a CMake toolchain that builds with Ninja."""
        toolchain = CMakeToolchain(self, generator="Ninja")
        toolchain.generate()

    def build(self) -> None:
        """Configure and build with CMake, for `conan build`."""
        cmake = CMake(self)
        cmake.configure()
        cmake.build()
