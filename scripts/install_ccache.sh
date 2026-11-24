#!/usr/bin/env bash
# Installs ccache, which CMake runs the compiler through when it finds it,
# from ccache's release on GitHub. The archive must match the SHA-256 named
# here for the platform. The program is put in the directory given, which
# is made if it is missing.
#
# Usage: install_ccache.sh <directory>

set -eu

version=4.14.3
case "$(uname -s)-$(uname -m)" in
    Darwin-*)
        name=ccache-$version-darwin
        archive=$name.tar.gz
        sha256=764dd2a8100ff83e6bbd7b9a1e2016cc401be34bee8a70ea99698186622c34d5
        ;;
    Linux-x86_64)
        name=ccache-$version-linux-x86_64-glibc
        archive=$name.tar.xz
        sha256=c94a5688546883eee4c87764b34fa2eb18a4879421a51589c37538aed6312dfc
        ;;
    *)
        echo "No ccache release is named for $(uname -s) $(uname -m)" >&2
        exit 1
        ;;
esac

directory=$1
mkdir -p "$directory"
download="$directory/$archive"
curl -fsSL -o "$download" \
    "https://github.com/ccache/ccache/releases/download/v$version/$archive"
echo "$sha256  $download" | shasum -a 256 -c -
tar -xf "$download" -C "$directory" --strip-components=1 "$name/ccache"
rm "$download"
"$directory/ccache" --version | head -n 1
