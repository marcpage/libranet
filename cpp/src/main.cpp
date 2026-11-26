#include <cstddef>
#include <exception>
#include <filesystem>
#include <iostream>
#include <iterator>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "io/Directories.hpp"
#include "node/Node.hpp"
#include "node/SettingsFile.hpp"

using io::Directories;
using node::Node;
using node::SettingsFile;
using std::cerr;
using std::cout;
using std::exception;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::size_t;
using std::string;
using std::string_view;
using std::vector;
using std::filesystem::exists;
using std::filesystem::path;

namespace {

// The exit status when the node fails, and when what it is given cannot be
// used: the command line, the settings file, or a setting in it.
constexpr int failed = 1;
constexpr int unusable = 2;

// The settings file read when --config names none, where the Python node
// looks for its own; or none, if HOME is not set to find it.
optional<path> default_config() {
    const auto directory = Directories::user_config();

    if (!directory) {
        return nullopt;
    }

    return *directory / "libranet" / "libranet.yaml";
}

string usage() {
    const auto config = default_config();

    return "usage: libranet [--config PATH] [--seed URL[=ID]]...\n"
           "\n"
           "Runs a Libranet node until it is sent SIGINT or SIGTERM, with the\n"
           "settings in the YAML file at PATH. Without --config, they are in the\n"
           "settings file below, if it is there, and are the defaults if not.\n"
           "Each --seed names a node to dial while the node knows no other: its\n"
           "http URL, and its node id, if it is known.\n"
           "\n"
           "settings file: "
           + (config ? config->string() : string("none, as HOME is not set to find one"))
           + "\n";
}

// What the command line gives the node: the settings in the file --config
// or -c names, or else in the default one, if it is there, or none; and the
// seeds each --seed names.
struct CommandLine {
    SettingsFile settings;
    vector<string> seeds;
};

// Throws std::invalid_argument for a command line that is not as usage
// says, and what SettingsFile::load() throws.
CommandLine command_line(const vector<string_view>& arguments) {
    CommandLine given;
    bool has_config = false;

    // Each option is followed by its value.
    for (size_t option = 0; option < arguments.size(); option += 2) {
        const auto name = arguments.at(option);
        const auto is_config = "--config" == name || "-c" == name;
        const auto is_seed = "--seed" == name;

        if ((!is_config && !is_seed) || option + 1 == arguments.size()
            || (is_config && has_config)) {
            throw invalid_argument("unknown arguments\n" + usage());
        }

        const auto value = arguments.at(option + 1);

        if (is_config) {
            given.settings = SettingsFile::load(value);
            has_config = true;
        } else {
            given.seeds.emplace_back(value);
        }
    }

    if (const auto config = default_config(); !has_config && config && exists(*config)) {
        given.settings = SettingsFile::load(*config);
    }

    return given;
}

// Does what the command line asks, and gives the exit status.
int run(const vector<string_view>& arguments) {
    if (1 == arguments.size() && ("--help" == arguments.front() || "-h" == arguments.front())) {
        cout << usage();
        return 0;
    }

    CommandLine given;

    try {
        given = command_line(arguments);
    } catch (const exception& error) {
        cerr << "libranet: " << error.what() << '\n';
        return unusable;
    }

    try {
        Node::run(given.settings, given.seeds);
    } catch (const invalid_argument& error) {
        cerr << "libranet: " << error.what() << '\n';
        return unusable;
    }

    return 0;
}

} // namespace

// The standard names the types of main()'s parameters.
// cppcheck-suppress constParameter
int main(int argc, char* argv[]) {
    try {
        return run(vector<string_view>(std::next(argv), std::next(argv, argc)));
    } catch (const exception& error) {
        cerr << "libranet: " << error.what() << '\n';
        return failed;
    }
}
