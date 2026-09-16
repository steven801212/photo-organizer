#!/bin/sh
set -eu
if [ -n "${PUID:-}" ] && [ -n "${PGID:-}" ]; then
  exec gosu "${PUID}:${PGID}" "$@"
fi
exec "$@"
