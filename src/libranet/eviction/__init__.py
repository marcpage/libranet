"""Storage-pressure hand-off and deletion (Phase 1 Step 15).

Reacts to "new data stored" messages, checks free space, and hands off
low-priority content to closer nodes before deleting the local copy.
"""
