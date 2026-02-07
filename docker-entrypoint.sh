#!/bin/bash
set -e

# Default to UID/GID 568 (TrueNAS SCALE apps default)
PUID=${PUID:-568}
PGID=${PGID:-568}

# Only modify user/group if running as root
if [ "$(id -u)" = "0" ]; then
    echo "Configuring riparr user with PUID=$PUID and PGID=$PGID"

    # Modify group ID if different
    if [ "$(id -g riparr)" != "$PGID" ]; then
        groupmod -o -g "$PGID" riparr
    fi

    # Modify user ID if different
    if [ "$(id -u riparr)" != "$PUID" ]; then
        usermod -o -u "$PUID" riparr
    fi

    # Fix ownership of directories
    chown -R riparr:riparr /data /config /app 2>/dev/null || true

    # Fix ownership of MakeMKV config directory if it exists
    if [ -d "/home/riparr/.MakeMKV" ]; then
        chown -R riparr:riparr /home/riparr/.MakeMKV
    fi

    # Execute command as riparr user
    exec gosu riparr "$@"
else
    # Already running as non-root, just exec
    exec "$@"
fi
