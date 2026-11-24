#include "SafeWriteFile.hpp"

#include <cstddef>
#include <filesystem>
#include <fstream>
#include <ios>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

using io::SafeWriteFile;
using std::byte;
using std::error_code;
using std::logic_error;
using std::random_device;
using std::runtime_error;
using std::string;
using std::string_view;
using std::to_string;
using std::vector;
using std::filesystem::create_directories;
using std::filesystem::path;

namespace {

// A file is written to one whose name starts with this, so that it is not
// taken for the file while it is written, and ends with that, so that one a
// crash left behind can be told.
constexpr string_view partial_start = ".";
constexpr string_view partial_end = ".partial";

// Where file is written before it is renamed into place.
path partial_path(const path& file) {
    random_device random;

    return file.parent_path()
           / (string(partial_start) + file.filename().string() + "." + to_string(random())
              + string(partial_end));
}

} // namespace

namespace io {

SafeWriteFile::SafeWriteFile(path file)
    : m_file(std::move(file))
    , m_partial(partial_path(m_file)) {
    const auto directory = m_file.parent_path();
    error_code error;

    // A file named with no directory is in the current one, which is there,
    // and which create_directories() refuses to make.
    if (!directory.empty()) {
        create_directories(directory, error);
    }

    if (error) {
        throw runtime_error("Failed to create directory: " + directory.string() + ": "
                            + error.message());
    }
}

// Nothing more can be done if what was written cannot be removed.
SafeWriteFile::~SafeWriteFile() {
    error_code ignored;

    if (!m_committed) {
        (void)std::filesystem::remove(writable_path(), ignored);
    }
}

bool SafeWriteFile::is_partial(const path& file) {
    const auto name = file.filename().string();

    return name.size() >= partial_start.size() + partial_end.size()
           && 0 == name.compare(0, partial_start.size(), partial_start)
           && 0 == name.compare(name.size() - partial_end.size(), partial_end.size(), partial_end);
}

const path& SafeWriteFile::writable_path() const noexcept {
    return m_partial;
}

void SafeWriteFile::write(std::string_view contents) {
    std::ofstream out(writable_path(), std::ios::out | std::ios::binary);

    if (!out.is_open()) {
        throw std::runtime_error("Failed to open file for writing: " + writable_path().string());
    }

    // Empty contents may be at no address, which is not to be copied from.
    if (!contents.empty()) {
        out.write(contents.data(), static_cast<std::streamsize>(contents.size()));
    }

    // Closed, so that all of it is in the file before it is renamed into
    // place, and a failure to write the last of it is known.
    out.close();

    if (!out.good()) {
        throw std::runtime_error("Error occurred while writing to file: "
                                 + writable_path().string());
    }

    commit();
}

void SafeWriteFile::write(const vector<byte>& contents) {
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
    write(string_view(reinterpret_cast<const char*>(contents.data()), contents.size()));
}

void SafeWriteFile::commit() {
    error_code error;

    if (m_committed) {
        throw logic_error("Cannot commit a file already committed: " + m_file.string());
    }

    std::filesystem::rename(writable_path(), m_file, error);

    if (error) {
        throw runtime_error("Failed to rename " + writable_path().string() + " to "
                            + m_file.string() + ": " + error.message());
    }

    m_committed = true;
}

} // namespace io
