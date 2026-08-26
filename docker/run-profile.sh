#!/bin/sh
# One scheduled run of one profile, and the only place the exit-code contract is bent.
#
# The scheduler fires this; the crontab carries a profile name and never a window, so
# retuning how many days a profile sweeps stays a configuration edit and nothing else.
#
# Exit 3 — another run holds the flock — is mapped to 0. A monitor firing while the daily
# sweep is still working is the design working, not a fault, and a scheduler that reports it
# as a failure trains its reader to ignore failures. Every other code passes through
# untouched: `|| true` would swallow 1, 2 and 4 with it, and 4 in particular is the one code
# that must reach whoever watches the container.
set -u

case "${1:-}" in
    sweep)   profile=sweep;   set -- run ;;
    monitor) profile=monitor; set -- run --monitor ;;
    *)
        printf 'error: usage: run-profile.sh sweep|monitor\n' >&2
        exit 2
        ;;
esac

co-docs-watcher "$@"
code=$?

if [ "$code" -eq 3 ]; then
    printf '%s: another run holds the lock; leaving it to finish\n' "$profile"
    exit 0
fi

exit "$code"
