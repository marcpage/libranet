#include <cstddef>
#include <exception>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "node/Node.hpp"
#include "node/SettingsFile.hpp"

using node::Node;
using node::SettingsFile;
using std::cerr;
using std::cout;
using std::exception;
using std::invalid_argument;
using std::size_t;
using std::string;
using std::string_view;
using std::vector;

namespace {

// The exit status when the node fails, and when what it is given cannot be
// used: the command line, the settings file, or a setting in it.
constexpr int failed = 1;
constexpr int unusable = 2;

constexpr auto usage = "usage: libranet [--config PATH] [--seed URL[=ID]]...\n"
                       "\n"
                       "Runs a Libranet node until it is sent SIGINT or SIGTERM, with the\n"
                       "settings in the YAML file at PATH, if one is given. Each --seed\n"
                       "names a node to dial while the node knows no other: its http URL,\n"
                       "and its node id, if it is known.\n";

// What the command line gives the node: the settings in the file --config
// or -c names, or none, and the seeds each --seed names.
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
            throw invalid_argument("unknown arguments\n" + string(usage));
        }

        const auto value = arguments.at(option + 1);

        if (is_config) {
            given.settings = SettingsFile::load(value);
            has_config = true;
        } else {
            given.seeds.emplace_back(value);
        }
    }

    return given;
}

// Does what the command line asks, and gives the exit status.
int run(const vector<string_view>& arguments) {
    if (1 == arguments.size() && ("--help" == arguments.front() || "-h" == arguments.front())) {
        cout << usage;
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
