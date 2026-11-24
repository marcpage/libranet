#pragma once

#include <cstddef>
#include <filesystem>
#include <string_view>
#include <vector>

namespace io {

// A file written whole or not at all. What is written to writable_path(), a
// file of its own beside the file, is renamed into place by commit(), so
// that no reader sees it half written, and is removed when this goes
// without commit(), as when writing it threw.
class SafeWriteFile {
public:
    // Makes the directories file is to be in. Throws std::runtime_error when
    // they cannot be made.
    explicit SafeWriteFile(std::filesystem::path file);
    ~SafeWriteFile();

    // Whether file is one written to before it is committed, or one a crash
    // left behind.
    [[nodiscard]] static bool is_partial(const std::filesystem::path& file);

    // Where to write the file before commit(): beside it, so that the rename
    // does not cross filesystems, and named with a random number, so that
    // two writes of the same file at once do not write the same one.
    [[nodiscard]] const std::filesystem::path& writable_path() const noexcept;

    // Writes contents to writable_path() and commit() unless exception thrown
    void write(std::string_view contents);
    void write(const std::vector<std::byte>& contents);

    // Renames what was written to writable_path() to the file, in place of
    // any there. Throws std::logic_error when it is already committed, and
    // std::runtime_error when it cannot be renamed, as when nothing was
    // written or a directory is in its place.
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
