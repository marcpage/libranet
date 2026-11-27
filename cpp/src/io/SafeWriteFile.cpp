#include "SafeWriteFile.hpp"

#include <cerrno>
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

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

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
using std::filesystem::create_hard_link;
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

// Made empty, with the mode only its owner may read or write it with, and
// refused when there is one there, so that it is no file another made.
void make_owner_only(const path& partial) {
    constexpr int flags = O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC;
    constexpr mode_t owner_read_write = S_IRUSR | S_IWUSR;

    // open() is the POSIX call that sets the mode of a file as it makes it,
    // and it takes the mode as C's variable arguments.
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
    const int descriptor = ::open(partial.c_str(), flags, owner_read_write);

    if (descriptor < 0) {
        throw runtime_error("Failed to create file: " + partial.string() + ": "
                            + std::generic_category().message(errno));
    }

    // Nothing was written to it to be lost.
    (void)::close(descriptor);
}

// Writes contents to partial, truncated, so that it keeps the mode it was
// made with for Access::Owner. Throws std::runtime_error when it cannot.
void write_partial(const path& partial, string_view contents) {
    std::ofstream out(partial, std::ios::out | std::ios::binary);

    if (!out.is_open()) {
        throw std::runtime_error("Failed to open file for writing: " + partial.string());
    }

    // Empty contents may be at no address, which is not to be copied from.
    if (!contents.empty()) {
        out.write(contents.data(), static_cast<std::streamsize>(contents.size()));
    }

    // Closed, so that all of it is in the file before it is renamed or linked
    // into place, and a failure to write the last of it is known.
    out.close();

    if (!out.good()) {
        throw std::runtime_error("Error occurred while writing to file: " + partial.string());
    }
}

} // namespace

namespace io {

SafeWriteFile::SafeWriteFile(path file, Access access)
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

    if (Access::Owner == access) {
        make_owner_only(writable_path());
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
    write_partial(writable_path(), contents);
    commit();
}

void SafeWriteFile::write(const vector<byte>& contents) {
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
    write(string_view(reinterpret_cast<const char*>(contents.data()), contents.size()));
}

bool SafeWriteFile::write_if_absent(string_view contents) {
    write_partial(writable_path(), contents);
    return commit_if_absent();
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

// A link fails when there is a file there, where a rename would replace it.
bool SafeWriteFile::commit_if_absent() {
    error_code error;

    if (m_committed) {
        throw logic_error("Cannot commit a file already committed: " + m_file.string());
    }

    create_hard_link(writable_path(), m_file, error);

    if (std::errc::file_exists == error) {
        return false;
    }

    if (error) {
        throw runtime_error("Failed to link " + writable_path().string() + " to "
                            + m_file.string() + ": " + error.message());
    }

    m_committed = true;

    // Nothing more can be done if it cannot be removed: it is the file now
    // too, and is_partial() tells it.
    (void)std::filesystem::remove(writable_path(), error);
    return true;
}

} // namespace io
