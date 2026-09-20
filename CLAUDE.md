# Instructions for Claude Code

## Coding Conventions

All function parameters and class members should have correct type annotations.

PEP 8 should be followed.

Each session should make minimal changes to existing code to accomplish the
task.

All python imports should import each symbol from the module instead of
importing entire modules.

## Interacting with GitHub and Git

Do not directly interact with GitHub. Just inform me what needs to be done and I
will do it.

Never commit changes. I will review changes and commit them myself.

If the changes proposed will be more than 1,000 new lines (including changed
lines) of Python (not counting tests), then propose logical steps that can be
progressively be committed that allows the PRs to have less than 1,000 new lines
per change set.
