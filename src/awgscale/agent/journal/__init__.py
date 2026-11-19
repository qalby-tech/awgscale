"""The journal: what was changed and when.

One line per change, written when a changing command answers. The lines are
not kept by the agent: each goes to the host's journald under its own
identifier, Alloy ships those to Loki, and Loki is what is read back and what
decides how long they live.
"""
