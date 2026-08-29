#!/bin/sh
# The container's front door: process identity, the rendered crontab, and the scheduler.
#
# Two shapes are served from one entrypoint. With no arguments — or with `scheduler` — the
# container is the long-running deployment: it renders a crontab from the environment and
# hands PID over to supercronic. With any other arguments it is an ad-hoc command
# (`docker compose run --rm watcher doctor`), which runs against the same three roots as
# every scheduled run and reports the CLI's exit code unchanged, exit 3 included.
#
# Schedules live here, in the environment; windows live in config.toml. The two are asked
# different questions — how often to look, and how far back — and answering both in one file
# would make a change of cadence look like a change of coverage. The zone both are read in
# is neither's to declare: it is source.timezone, and TZ is exported from it below.
#
# Exit 2 is this script's only failure code, and it means what it means everywhere else in
# the project: the configuration it was handed is invalid. A container that starts on a
# misread environment would schedule something nobody asked for.
set -eu

log() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
fail() { printf 'error: %s\n' "$*" >&2; exit 2; }

boolean() {
    case "$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')" in
        true|yes|1) return 0 ;;
        false|no|0) return 1 ;;
        *) fail "$1 must be true or false, not '$2'" ;;
    esac
}

# --- process identity -------------------------------------------------------------------
#
# The archive is created with declared modes, so the umask of whatever started the container
# is already irrelevant — but ownership is not, and it is what decides whether the share can
# read the files at all. PUID/PGID name the identity; started as root, the entrypoint drops
# to it and re-enters, so nothing but this preamble ever runs privileged.
current_uid=$(id -u)
if [ "$current_uid" -eq 0 ]; then
    puid=${PUID:-1000}
    pgid=${PGID:-1000}
    case "$puid" in ''|*[!0-9]*) fail "PUID must be a numeric uid, not '$puid'" ;; esac
    case "$pgid" in ''|*[!0-9]*) fail "PGID must be a numeric gid, not '$pgid'" ;; esac
    [ "$puid" -ne 0 ] || fail "PUID=0: the watcher does not run as root"
    # HOME is set explicitly: the target uid need not exist in /etc/passwd, and a process
    # with a HOME it cannot write is a failure that surfaces far from its cause.
    exec setpriv --reuid "$puid" --regid "$pgid" --clear-groups \
        env HOME=/tmp "$0" "$@"
fi

# Already unprivileged — someone set `user:` in compose. That wins, and PUID/PGID become a
# statement about an identity nobody can grant, which is worth saying out loud.
if [ -n "${PUID:-}" ] && [ "${PUID}" != "$current_uid" ]; then
    warn "PUID=${PUID} ignored: the container was started as uid $current_uid"
fi
if [ -n "${PGID:-}" ] && [ "${PGID}" != "$(id -g)" ]; then
    warn "PGID=${PGID} ignored: the container was started as gid $(id -g)"
fi

# --- the configuration ------------------------------------------------------------------
#
# Checked here rather than left to the CLI because of how this one fails: Docker creates a
# *directory* where a bind mount's source is missing, so a first start without the copy step
# mounts an empty directory over the configuration file and every command below reports
# something other than the mistake that was made.
#
# One file, named by one variable. The image sets CO_WATCHER_CONFIG, and an empty one is
# refused rather than guessed at: resolving it here would mean a second copy of the CLI's
# discovery chain, and two copies of a rule are a way for it to differ.
config=${CO_WATCHER_CONFIG:-}
[ -n "$config" ] || fail "CO_WATCHER_CONFIG is empty: it must name the configuration file"
[ -f "$config" ] || \
    fail "$config is not a file: copy config.example.toml to config.toml before the first start"

# --- the clock --------------------------------------------------------------------------
#
# source.timezone is the project's only declaration of a timezone, and TZ is derived from it.
# The two answer one question — the zone the schedule fires in, and the zone every date the
# watcher writes is anchored on — and two independent answers to it drift silently: the
# archive would keep its dates while the crontab moved out from under them.
#
# Derived before the ad-hoc branch below, so that `doctor` reports the TZ the scheduler
# would run under rather than the absence of one, and after the privilege drop, so that a
# configuration the target uid cannot read fails here instead of somewhere further away.
config_timezone="$(dirname "$0")/config-timezone.py"
[ -x "$config_timezone" ] || fail "$config_timezone is missing or not executable"
declared=$("$config_timezone" "$config") || fail "cannot read source.timezone from $config"

# The Python zone database and the system's are separate packages, and supercronic reads the
# system's. A zone present only in the first would be accepted here and then silently
# resolved as UTC, which is the failure this whole derivation exists to remove.
[ -e "/usr/share/zoneinfo/$declared" ] || \
    fail "source.timezone is '$declared', which the system zone database does not have:" \
         "the scheduler would fall back to UTC. Is tzdata installed?"

# An explicit TZ is accepted only when it agrees. Orchestrators inject one unasked, so an
# identical value is redundant rather than wrong; a different one is two answers to one
# question, and the archive and the schedule would be written in different zones.
if [ -n "${TZ:-}" ] && [ "${TZ}" != "$declared" ]; then
    fail "TZ=${TZ} contradicts source.timezone=$declared in $config:" \
         "the schedule would fire in ${TZ} while the archive is written in $declared"
fi

TZ="$declared"
export TZ
log "timezone: $TZ (from source.timezone in $config)"

# --- ad-hoc commands --------------------------------------------------------------------
if [ "$#" -gt 0 ] && [ "$1" != "scheduler" ]; then
    exec co-docs-watcher "$@"
fi

# --- the schedule -----------------------------------------------------------------------
# Defaulted on unset and never on empty: `SWEEP_ENABLED=` in a .env file is a line someone
# wrote on purpose, and reading it as the default would turn a half-finished edit into a
# schedule nobody chose.
MONITOR_SCHEDULE=${MONITOR_SCHEDULE-0 0,9-23 * * *}
MONITOR_ENABLED=${MONITOR_ENABLED-true}
SWEEP_SCHEDULE=${SWEEP_SCHEDULE-0 8 * * *}
SWEEP_ENABLED=${SWEEP_ENABLED-true}
RUN_ON_START=${RUN_ON_START-sweep}

case "$RUN_ON_START" in
    none|sweep|monitor) ;;
    *) fail "RUN_ON_START must be sweep, monitor or none, not '$RUN_ON_START'" ;;
esac

run_profile="$(dirname "$0")/run-profile.sh"
[ -x "$run_profile" ] || fail "$run_profile is missing or not executable"

# mktemp rather than a fixed path: the crontab is rendered state, and a stale one left by an
# earlier start must never be what the scheduler reads. What the operator needs to see is
# logged below, where it is read without exec'ing into the container.
crontab="$(mktemp -d)/crontab"
: > "$crontab"

if boolean MONITOR_ENABLED "$MONITOR_ENABLED"; then
    [ -n "$MONITOR_SCHEDULE" ] || fail "MONITOR_ENABLED is true but MONITOR_SCHEDULE is empty"
    printf '%s %s monitor\n' "$MONITOR_SCHEDULE" "$run_profile" >> "$crontab"
fi
if boolean SWEEP_ENABLED "$SWEEP_ENABLED"; then
    [ -n "$SWEEP_SCHEDULE" ] || fail "SWEEP_ENABLED is true but SWEEP_SCHEDULE is empty"
    printf '%s %s sweep\n' "$SWEEP_SCHEDULE" "$run_profile" >> "$crontab"
fi
[ -s "$crontab" ] || fail "both profiles are disabled: this container would schedule nothing"

log "crontab (TZ=$TZ):"
while IFS= read -r line; do log "  $line"; done < "$crontab"

# --- the catch-up run -------------------------------------------------------------------
#
# A container start usually follows a restart, an image update or downtime — which is the
# gap case exactly, so the catch-up is the full sweep and never the monitor: the monitor's
# narrow window would re-observe the two days that were never in doubt and leave the gap
# unclosed. A failure here is reported and does not stop the scheduler: the next firing is
# the retry, and a container that refuses to start over one bad run stops monitoring
# entirely over a source that was briefly down.
if [ "$RUN_ON_START" != none ]; then
    log "run-on-start: $RUN_ON_START"
    "$run_profile" "$RUN_ON_START" || warn "run-on-start: $RUN_ON_START exited $?"
fi

# -passthrough-logs: the watcher already stamps every line with the source timezone and
# writes the same lines to its log file. Wrapping them in the scheduler's own format would
# give the same event two timestamps in two zones.
exec supercronic -passthrough-logs "$crontab"
