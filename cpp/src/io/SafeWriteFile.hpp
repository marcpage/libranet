#pragma once

#include <cstddef>
#include <filesystem>
#include <string_view>
#include <vector>

namespace io {

// A file written whole or not at all: what is written to writable_path() is
// renamed into place by commit(), and removed when this goes without it.
class SafeWriteFile {
public:
    // Makes the directories file is to be in. Throws std::runtime_error when
    // they cannot be made.
    explicit SafeWriteFile(std::filesystem::path file);
    ~SafeWriteFile();

    // Or one a crash left behind.
    [[nodiscard]] static bool is_partial(const std::filesystem::path& file);

    // Beside the file, so that the rename does not cross filesystems, and
    // named at random, so that two writes of the same file do not collide.
    [[nodiscard]] const std::filesystem::path& writable_path() const noexcept;

    // And commit()s it.
    void write(std::string_view contents);
    void write(const std::vector<std::byte>& contents);

    // Throws std::logic_error when it is already committed, and
    // std::runtime_error when it cannot be renamed.
    void commit();

    // Each would leave two to remove the one file written.
    SafeWriteFile(const SafeWriteFile&) = delete;
    SafeWriteFile& operator=(const SafeWriteFile&) = delete;
    SafeWriteFile(SafeWriteFile&&) = delete;
    SafeWriteFile& operator=(SafeWriteFile&&) = delete;

private:
    std::filesystem::path m_file;
    std::filesystem::path m_partial;
    bool m_committed{false};
};

} // namespace io
