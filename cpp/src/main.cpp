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
using std::string;
using std::string_view;
using std::vector;

namespace {

// The exit status when the node fails, and when what it is given cannot be
// used: the command line, the settings file, or a setting in it.
constexpr int failed = 1;
constexpr int unusable = 2;

constexpr auto usage = "usage: libranet [--config PATH]\n"
                       "\n"
                       "Runs a Libranet node until it is sent SIGINT or SIGTERM, with the\n"
                       "settings in the YAML file at PATH, if one is given.\n";

// The settings the command line names: those in the file --config or -c
// names, or none. Throws std::invalid_argument for any other command line,
// and what SettingsFile::load() throws.
SettingsFile settings_from(const vector<string_view>& arguments) {
    if (arguments.empty()) {
        return {};
    }

    if (2 == arguments.size() && ("--config" == arguments.front() || "-c" == arguments.front())) {
        return SettingsFile::load(arguments.back());
    }

    throw invalid_argument("unknown arguments\n" + string(usage));
}

// Does what the command line asks, and gives the exit status.
int run(const vector<string_view>& arguments) {
    if (1 == arguments.size() && ("--help" == arguments.front() || "-h" == arguments.front())) {
        cout << usage;
        return 0;
    }

    SettingsFile settings;

    try {
        settings = settings_from(arguments);
    } catch (const exception& error) {
        cerr << "libranet: " << error.what() << '\n';
        return unusable;
    }

    try {
        Node::run(settings);
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
