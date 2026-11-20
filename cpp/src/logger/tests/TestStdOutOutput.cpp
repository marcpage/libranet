#include <gtest/gtest.h>

#include "logger/StdOutOutput.hpp"

using logger::StdOutOutput;

TEST(StdOutOutputTest, WritesToStdout) {
    StdOutOutput output;

    testing::internal::CaptureStdout();
    output.log("to stdout\n");

    EXPECT_EQ(testing::internal::GetCapturedStdout(), "to stdout\n");
}
