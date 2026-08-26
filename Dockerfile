# syntax=docker/dockerfile:1
#
# The image is a deployment of the package, never a dependency of it: nothing under src/
# knows this file exists, and the same one-shot command a shell runs is what the scheduler
# fires. What the image adds is a process that stays up and a clock to fire it with.

# --- the scheduler binary ----------------------------------------------------------------
#
# supercronic rather than cron: a static Go binary that runs unprivileged, logs its jobs to
# stdout instead of mailing them to a local MTA, and has none of cron's PID-1 quirks. It is
# pinned by version and verified by digest — an unpinned scheduler is a silent change of
# behaviour in a container whose whole purpose is to run unattended.
FROM python:3.12-slim AS scheduler
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.49
# sha256 of the published release binaries, measured 2026-08-26.
ARG SUPERCRONIC_SHA256_AMD64=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1
ARG SUPERCRONIC_SHA256_ARM64=02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5
RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    rm -rf /var/lib/apt/lists/*; \
    case "${TARGETARCH}" in \
        amd64) sha256="${SUPERCRONIC_SHA256_AMD64}" ;; \
        arm64) sha256="${SUPERCRONIC_SHA256_ARM64}" ;; \
        *) echo "unsupported architecture: '${TARGETARCH}'" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}"; \
    echo "${sha256}  /usr/local/bin/supercronic" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/supercronic

# --- the image ---------------------------------------------------------------------------
FROM python:3.12-slim

# tzdata for the scheduler's TZ — the watcher carries its own through the dependency of the
# same name, but cron's idea of 07:00 comes from the system zone database.
RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --system --create-home --home-dir /home/watcher --user-group --uid 1000 watcher

COPY --from=scheduler /usr/local/bin/supercronic /usr/local/bin/supercronic

WORKDIR /app
COPY pyproject.toml LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY docker/entrypoint.sh docker/run-profile.sh /usr/local/bin/

# The three roots are mount points, absolute and named by the mounted configuration file.
# data_root is a mount of its own because SQLite locking over SMB/NFS is unreliable: it is
# the one root that must land on a filesystem local to the host.
ENV CO_WATCHER_CONFIG=/config/config.toml \
    PYTHONUNBUFFERED=1

# Root at rest, unprivileged at work: the entrypoint drops to PUID/PGID before it does
# anything, and re-enters itself as that user. Dropping is what makes PUID/PGID mean
# something; a USER here would freeze the identity at build time, and the archive would be
# owned by whoever the image was built for rather than by whoever the share expects.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["scheduler"]
