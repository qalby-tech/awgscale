"""The cascade's own settings: the exits, their shares, what goes through them.

Kept in the database (table meta, key "cascade") as one document. The file
cascade.conf stays on the host as a copy COMPILED from it, because the rules
at boot are installed before Docker and the database are up, and the watch
service reads that file. The agent is its only writer: the file is replaced
only by text that reads back as the very document it was made from, the
previous one is kept beside it (.prev), and with the database down it is not
touched.
"""
