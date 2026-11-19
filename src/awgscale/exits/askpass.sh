#!/bin/sh
# What ssh runs to get the password of a new server (SSH_ASKPASS): it is handed
# over in the environment of that one ssh process and written nowhere.
printf '%s\n' "$EXITS_PW"
