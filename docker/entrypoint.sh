#!/bin/sh
set -eu
case "${1:-ferris}" in
  ferris)
    shift
    exec python -m ferris.server --host 0.0.0.0 --loopback-published --data /app/data --models /app/models "$@"
    ;;
  voice)
    shift
    python -m tools.models.setup_voice
    exec python -m ferris.voice_worker --host 0.0.0.0 --port 8770 --data /app/data "$@"
    ;;
  *) exec "$@" ;;
esac
