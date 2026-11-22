#!/usr/bin/env bash
# Runs every GoogleTest program of a CMake build, each split into as many
# shards as there are processors, that many shards at a time. ctest starts a
# program once for each test, and under ThreadSanitizer on a macOS runner
# starting one takes over half a second, most of the time the tests take.
# Each shard runs in its program's directory, as ctest runs it, and writes
# its output next to the program. The output of each shard that fails is
# shown once all have finished, and then the status is 1.
#
# Usage: run_test_shards.sh <build directory>

set -eu

build=$1
shards=$(getconf _NPROCESSORS_ONLN)
export GTEST_TOTAL_SHARDS="$shards"

rm -f "$build"/src/*/*_tests-*.log "$build"/src/*/*_tests-*.status

for tests in "$build"/src/*/*_tests; do
    shard=0
    while [ "$shard" -lt "$shards" ]; do
        printf '%s\0%s\0' "$tests" "$shard"
        shard=$((shard + 1))
    done
done | xargs -0 -n 2 -P "$shards" sh -c '
    cd "$(dirname "$0")"
    name=$(basename "$0")
    status=0
    GTEST_SHARD_INDEX=$1 "./$name" > "$name-$1.log" 2>&1 || status=$?
    echo "$status" > "$name-$1.status"
'

failed=0
for status_file in "$build"/src/*/*_tests-*.status; do
    shard=$(basename "${status_file%.status}")
    log="${status_file%.status}.log"
    status=$(cat "$status_file")
    if [ "$status" = 0 ]; then
        echo "$shard: $(grep '^\[  PASSED  \]' "$log" || echo 'no summary')"
    else
        echo "::error::$shard failed (status $status)"
        cat "$log"
        failed=1
    fi
done
exit "$failed"
