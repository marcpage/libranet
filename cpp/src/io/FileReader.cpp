#include "FileReader.hpp"

#include <cstddef>
#include <filesystem>
#include <fstream>
#include <ios>
#include <optional>
#include <stdexcept>
#include <string>
#include <system_error>
#include <utility>

#include "types/Constants.hpp"

using io::FileReader;
using std::byte;
using std::error_code;
using std::ifstream;
using std::nullopt;
using std::optional;
using std::runtime_error;
using std::size_t;
using std::streamsize;
using std::string;
using std::filesystem::path;
using types::piece_size;

namespace {

// Reads into data up to size of what is left of the stream file is open in,
// which is less only at its end. Throws std::runtime_error when it cannot.
size_t read_into(ifstream& stream, const path& file, char* data, size_t size) {
    stream.read(data, static_cast<streamsize>(size));

    if (stream.bad()) {
        throw runtime_error("Cannot read the file: " + file.string());
    }

    return static_cast<size_t>(stream.gcount());
}

} // namespace

namespace io {

FileReader::FileReader(path file)
    : m_file(std::move(file)) {
    error_code error;

    // One that cannot be looked at cannot be opened either, which is found
    // below.
    if (std::filesystem::is_directory(m_file, error)) {
        throw runtime_error("Cannot read a directory as a file: " + m_file.string());
    }

    m_stream.open(m_file, std::ios::binary);

    if (!m_stream.is_open()) {
        throw runtime_error("Cannot open the file: " + m_file.string());
    }
}

string FileReader::read_all() {
    string contents;
    string piece(piece_size, '\0');

    while (true) {
        const auto read = read_into(m_stream, m_file, piece.data(), piece.size());

        contents.append(piece, 0, read);

        if (read < piece.size()) {
            return contents;
        }
    }
}

optional<size_t> FileReader::read_some(byte* data, size_t size) {
    if (0 == size) {
        return size_t{0};
    }

    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
    const auto read = read_into(m_stream, m_file, reinterpret_cast<char*>(data), size);

    if (0 == read) {
        return nullopt;
    }

    return read;
}

} // namespace io
