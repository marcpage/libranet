# Instructions for Claude Code

## Coding instructors for all languages

Each session should make minimal changes to existing code to accomplish the
task.

Do not update README.md when changing code. Only update it when explicitly asked to.
Updating the README.md is a top reason for merge conflicts when working on multiple
steps simultaneously.

## Coding Conventions for Python

All function parameters and class members should have correct type annotations.

PEP 8 should be followed.

All python imports should import each symbol from the module instead of
importing entire modules.

Prefer methods on objects as opposed to functions.
This includes class factory methods.
Do not be extreme in this, but when it makes sense, use methods over functions.

If the changes proposed will be more than 500 new lines (including changed
lines) of Python (not counting tests), then propose logical steps that can be
progressively committed that allows the PRs to have less than 1,000 new lines
per change set.

## Coding Conventions for C++

Prefer helper functions to be in an anonymous namespace in the .cpp file.
This minimizes the exposure of implementation in the header.

Each git change should minimal. It should be the creation or modification of
a single class with minimal adjustments to other files to adapt to that change
or a general simple refactor across many files. Modifications in tests are not
counted towards these limits as a change in once class could affect multiple tests.

Do not put comments in CMakeLists.txt. Do not update existing comments.

## Interacting with GitHub and Git

Do not directly interact with GitHub. Just inform me what needs to be done and I
will do it.

Never commit changes unless I explicitly ask you to. I will review changes and commit them myself.

