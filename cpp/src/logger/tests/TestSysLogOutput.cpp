#include <gtest/gtest.h>

#include "logger/SysLogOutput.hpp"

using logger::SysLogOutput;

// What the system log does with the line cannot be read back the same way on
// every system, so this checks only that sending it does not fail.
TEST(SysLogOutputTest, SendsToTheSystemLog) {
    SysLogOutput output;

    EXPECT_NO_THROW(output.log("SysLogOutputTest: a line from libranet's logger tests"));
}
