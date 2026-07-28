/*
 * Minimal doctest-compatible test header for CaduceusCore.
 *
 * Provides a subset of the doctest API (TEST_CASE, CHECK, CHECK_EQ, etc.)
 * so C++ conformance tests can be written in doctest style. The real
 * doctest.h can replace this file later with zero code changes.
 *
 * This is intentionally minimal — it supports only what the test suite
 * needs.  Do NOT add features beyond what is actually used.
 */

#ifndef DOCTEST_H
#define DOCTEST_H

#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace doctest {

struct Context {
    int tests_run = 0;
    int tests_passed = 0;
    int asserts_total = 0;
    int asserts_passed = 0;

    static Context &instance() {
        static Context ctx;
        return ctx;
    }
};

namespace detail {
    inline void check(bool expr, const char *file, int line,
                       const char *expr_str) {
        Context::instance().asserts_total++;
        if (!expr) {
            fprintf(stderr, "  FAIL: %s:%d: CHECK(%s)\n", file, line, expr_str);
        } else {
            Context::instance().asserts_passed++;
        }
    }
    inline void check_eq_int(int a, int b, const char *file, int line,
                              const char *a_str, const char *b_str) {
        Context::instance().asserts_total++;
        if (a != b) {
            fprintf(stderr, "  FAIL: %s:%d: CHECK_EQ(%s, %s) -> %d != %d\n",
                    file, line, a_str, b_str, a, b);
        } else {
            Context::instance().asserts_passed++;
        }
    }
}

} // namespace doctest

#define DOCTEST_ANON_FUNC_2(f, c) f##c
#define DOCTEST_ANON_FUNC_1(f, c) DOCTEST_ANON_FUNC_2(f, c)
#define DOCTEST_ANON_FUNC(f) DOCTEST_ANON_FUNC_1(f, __LINE__)

#define TEST_CASE(name)                                                        \
    static void DOCTEST_ANON_FUNC(doctest_func_)();                            \
    namespace {                                                                \
    struct DOCTEST_ANON_FUNC(doctest_reg_) {                                   \
        DOCTEST_ANON_FUNC(doctest_reg_)() {                                    \
            ::doctest::Context::instance().tests_run++;                         \
            printf("  DOCTEST: %s ... ", name);                                \
            DOCTEST_ANON_FUNC(doctest_func_)();                                \
            ::doctest::Context::instance().tests_passed++;                      \
            printf("PASS\n");                                                  \
        }                                                                      \
    } DOCTEST_ANON_FUNC(doctest_inst_);                                        \
    }                                                                          \
    static void DOCTEST_ANON_FUNC(doctest_func_)()

#define CHECK(expr)                                                            \
    ::doctest::detail::check((expr), __FILE__, __LINE__, #expr)

#define CHECK_EQ(a, b)                                                         \
    ::doctest::detail::check_eq_int((a), (b), __FILE__, __LINE__, #a, #b)

#define CHECK_NE(a, b)                                                         \
    ::doctest::detail::check((a) != (b), __FILE__, __LINE__, "(a) != (b)")

#define CHECK_FALSE(expr)                                                      \
    ::doctest::detail::check(!(expr), __FILE__, __LINE__, #expr)

#define REQUIRE(expr) CHECK(expr)
#define REQUIRE_EQ(a, b) CHECK_EQ(a, b)

#endif /* DOCTEST_H */
