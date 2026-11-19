"""What changes without anybody asking the agent: a user removed by a term (the
users service's own job), an exit going down and up. Every look at the state
compares with what was seen last and writes the difference into the journal.

What was seen last is kept in the database (table meta, key "seen"). With the
database down nothing is compared at all: "cannot read" is never "nothing was
seen", which would start from scratch or report everybody at once.
"""
