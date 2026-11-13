#include <iostream>
#include <cassert>
#include <string>
#include <thread>
#include <chrono>
#include <vector>
#include <atomic>

#include "Channel.hpp"
#include "Message.hpp"

using namespace msg;
using namespace std::chrono_literals;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static void sleep_ms(int ms) {
    std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

// ---------------------------------------------------------------------------
// Test cases
// ---------------------------------------------------------------------------

void test_construction_and_basic_operations() {
    std::cout << "-> Testing construction, empty/size, post/next... " << std::flush;

    Channel ch("test.basic");

    assert(ch.empty());
    assert(ch.size() == 0);

    Channel::post("test.basic", 42);
    assert(!ch.empty());
    assert(ch.size() == 1);

    auto msg = ch.next_message();
    assert(msg.has_value());
    assert(ch.empty());
    assert(ch.size() == 0);

    // Typed path
    Channel::post("test.basic", std::string("hello"));
    auto value = ch.next<std::string>();
    assert(value.has_value());
    assert(*value == "hello");
    assert(ch.empty());

    std::cout << "PASSED\n";
}

void test_timeout_behaviour() {
    std::cout << "-> Testing timeout behaviour (nullopt)... " << std::flush;

    Channel ch("test.timeout");

    // Immediate timeout
    auto timed_out = ch.next_message(0ms);
    assert(!timed_out.has_value());

    auto timed_out_typed = ch.next<int>(0ms);
    assert(!timed_out_typed.has_value());

    // Short timeout with no message
    auto still_timeout = ch.next_message(20ms);
    assert(!still_timeout.has_value());

    // Post then consume with timeout
    Channel::post("test.timeout", 99);
    auto got = ch.next_message(50ms);
    assert(got.has_value());

    auto typed = ch.next<int>(50ms);   // queue is empty again
    assert(!typed.has_value());

    std::cout << "PASSED\n";
}

void test_type_mismatch() {
    std::cout << "-> Testing type mismatch returns nullopt... " << std::flush;

    Channel ch("test.types");

    Channel::post("test.types", 12345);

    // Correct type
    auto ok = ch.next<int>(10ms);
    assert(ok.has_value());
    assert(*ok == 12345);

    // Wrong type after another post
    Channel::post("test.types", std::string("not an int"));
    auto mismatch = ch.next<int>(10ms);
    assert(!mismatch.has_value());

    // The message is still consumed by next_message / next
    // (we chose to treat type mismatch as "no usable value")
    assert(ch.empty());

    std::cout << "PASSED\n";
}

void test_broadcast_to_multiple_subscribers() {
    std::cout << "-> Testing broadcast to multiple channels... " << std::flush;

    Channel a("test.broadcast");
    Channel b("test.broadcast");
    Channel c("test.other");          // different name – should not receive

    Channel::post("test.broadcast", 7);

    assert(a.size() == 1);
    assert(b.size() == 1);
    assert(c.empty());

    auto va = a.next<int>();
    auto vb = b.next<int>();
    assert(va.has_value() && *va == 7);
    assert(vb.has_value() && *vb == 7);
    assert(c.empty());

    std::cout << "PASSED\n";
}

void test_destructor_removes_subscriber() {
    std::cout << "-> Testing destructor unregisters the channel... " << std::flush;

    {
        Channel temporary("test.lifetime");
        Channel::post("test.lifetime", 1);
        assert(temporary.size() == 1);
    } // temporary destroyed here

    // Posting after destruction must not crash and must not deliver anywhere
    Channel::post("test.lifetime", 2);

    // A new channel with the same name starts empty
    Channel fresh("test.lifetime");
    assert(fresh.empty());

    std::cout << "PASSED\n";
}

void test_wait_forever_and_concurrent_producer() {
    std::cout << "-> Testing WaitForever with a concurrent producer... " << std::flush;

    Channel ch("test.concurrent");
    std::atomic<bool> producer_done{false};

    std::thread producer([&] {
        sleep_ms(30);                       // give the consumer a moment to block
        Channel::post("test.concurrent", 55);
        producer_done = true;
    });

    // This blocks until the producer posts
    auto value = ch.next<int>(Channel::WaitForever);
    assert(value.has_value());
    assert(*value == 55);
    assert(producer_done);

    producer.join();

    std::cout << "PASSED\n";
}

void test_multiple_messages_fifo_order() {
    std::cout << "-> Testing FIFO ordering of multiple messages... " << std::flush;

    Channel ch("test.fifo");

    Channel::post("test.fifo", 10);
    Channel::post("test.fifo", 20);
    Channel::post("test.fifo", 30);

    assert(ch.size() == 3);

    assert(*ch.next<int>() == 10);
    assert(*ch.next<int>() == 20);
    assert(*ch.next<int>() == 30);
    assert(ch.empty());

    std::cout << "PASSED\n";
}

void test_mixed_types_and_to_string() {
    std::cout << "-> Testing mixed message types and to_string()... " << std::flush;

    Channel ch("test.mixed");

    Channel::post("test.mixed", 42);
    Channel::post("test.mixed", std::string("forty-two"));

    auto raw1 = ch.next_message();
    assert(raw1.has_value());
    assert(raw1.value()->to_string() == "42");

    auto raw2 = ch.next_message();
    assert(raw2.has_value());
    assert(raw2.value()->to_string() == "forty-two");

    std::cout << "PASSED\n";
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

int main() {
    std::cout << "       Running Channel Test Suite   \n";

    test_construction_and_basic_operations();
    test_timeout_behaviour();
    test_type_mismatch();
    test_broadcast_to_multiple_subscribers();
    test_destructor_removes_subscriber();
    test_wait_forever_and_concurrent_producer();
    test_multiple_messages_fifo_order();
    test_mixed_types_and_to_string();

    std::cout << "   All Tests Passed Successfully!   \n";

    return 0;
}
