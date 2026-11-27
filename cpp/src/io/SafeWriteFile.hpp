#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <string_view>
#include <vector>

namespace io {

// A file written whole or not at all: what is written to writable_path() is
// renamed into place by commit(), or linked into place by commit_if_absent(),
// and removed when this goes without either.
class SafeWriteFile {
public:
    // Who may read and write the file.
    enum class Access : std::uint8_t {
        // Whoever the umask lets.
        Umask,
        // Only its owner, from the first byte written.
        Owner,
    };

    // Makes the directories file is to be in. Throws std::runtime_error when
    // they cannot be made, or when writable_path() cannot be made for
    // Access::Owner.
    explicit SafeWriteFile(std::filesystem::path file, Access access = Access::Umask);
    ~SafeWriteFile();

    // Or one a crash left behind.
    [[nodiscard]] static bool is_partial(const std::filesystem::path& file);

    // Beside the file, named at random.
    [[nodiscard]] const std::filesystem::path& writable_path() const noexcept;

    // And commit()s it.
    void write(std::string_view contents);
    void write(const std::vector<std::byte>& contents);

    // And commit_if_absent()s it.
    [[nodiscard]] bool write_if_absent(std::string_view contents);

    // Throws std::logic_error when it is already committed, and
    // std::runtime_error when it cannot be renamed.
    void commit();

    // Whether it is now the file: not when one is there already, which is left
    // as it is. Throws std::logic_error when it is already committed, and
    // std::runtime_error when it cannot be linked.
    [[nodiscard]] bool commit_if_absent();

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
