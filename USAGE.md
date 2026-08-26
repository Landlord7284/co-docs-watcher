# co-docs-watcher — command reference

Monitors documents published on RAD/CVM for a watch list of companies, downloads new
deliveries, organizes them by delivery date, and maintains a local reading queue under
`documents_root/_inbox/`.

The canonical mode is **one-shot**: `run` does one complete pass and exits. There is no
daemon; schedule the one-shot with whatever your platform provides (cron, launchd, a shell
loop) if you want periodic runs. [Deployment](#deployment) below is one such schedule,
packaged: a container that carries its own.

```bash
python -m co_docs_watcher doctor          # check config, roots, timezone, windows, source
python -m co_docs_watcher add --ticker PETR
python -m co_docs_watcher run             # the canonical mode: sweeps discovery.days
python -m co_docs_watcher run --monitor   # the frequent profile: sweeps discovery.monitor_days
```

`run` sweeps the discovery window (`discovery.days`, by default the whole retention
window); `run --monitor` sweeps the narrow `discovery.monitor_days` window (default 2) and
differs in nothing else. The typical schedule is a frequent `run --monitor` plus one daily
`run`: at the source, a superseded or cancelled document keeps its original delivery date,
so supersessions of older documents are only visible to the sweep that re-queries older
days. The flag is a profile, never a number — how many days each profile sweeps is settled
in the configuration file.

## Configuration

The configuration file is discovered in this order — first hit wins:

1. `--config PATH` (valid before or after the subcommand)
2. `$CO_WATCHER_CONFIG`
3. `./config.toml`
4. `./co-docs-watcher.toml`
5. `~/.config/co-docs-watcher/config.toml`
6. built-in defaults (logs a warning: the defaults point at `./var/…` relative to the
   current directory)

A path named by `--config` or `$CO_WATCHER_CONFIG` that does not exist refuses to start.
Unknown sections and unknown keys are rejected.

`data_root`, `documents_root` and `logs_root` may be absolute, or relative to the
**directory of the configuration file** — not to the directory you happen to run from. So a project-local
install is a checkout with a `config.toml` beside it, archiving into its own `var/`,
and it points at the same archive whether you run it by hand or from cron.

`config.example.toml` at the repository root is a commented copy of the file below:

```bash
cp config.example.toml config.toml
```

```toml
[paths]
data_root = "var/data"           # private: watch list, manifest, lock, cache
documents_root = "var/documents" # the shareable archive
logs_root = "var/logs"           # holds co-docs-watcher.log

[logging]
max_bytes = 5242880            # bytes before the log file rotates
backups = 5                    # rotations kept

[retention]
days = 7                       # retained dates, including today

[discovery]
days = 7                       # swept by `run`; defaults to retention.days, never exceeds it
monitor_days = 2               # swept by `run --monitor`; never exceeds discovery.days

[files]
directory_mode = 0o755         # every directory created under documents_root
file_mode = 0o644              # every document, member and inbox index placed there

[registry]
max_age_days = 7               # days a cached FCA package is used without re-downloading

[source]
timezone = "America/Sao_Paulo" # anchors dates, directory names, log timestamps
min_request_interval = 15.0    # seconds between requests; the backend is fragile
max_requests_per_run = 200     # safety fuse for a single run
base_url = "https://www.rad.cvm.gov.br/ENETWeb/"  # override only for a test server or mirror

[prefix_overrides]
# Folder names, keyed by CVM code, for when the resolver's pick is humanly wrong.
"003549" = "SCHLOSSER"
```

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | clean |
| `1` | ran with isolated failures (a document, a company, the registry refresh) |
| `2` | invalid configuration (also: unreadable watch list, manifest newer than this build) |
| `3` | another instance holds the lock |
| `4` | the source demanded a captcha — do not retry; reduce frequency |

Exit code `4` is not a transient failure: retrying makes it worse.

## Subcommands

### `doctor`

Checks everything a run depends on and prints one line per finding: the configuration file
in use, the roots (created if missing, probed for writability), the timezone, both
resolved discovery windows — first date, last date, day count, and which of `run` and
`run --monitor` sweeps each, so the configuration is verifiable without spending a sweep
on it — the watch list, the registry cache age, and the source (one real listing request
for today). Exits
`0` when everything passed, `1` when something failed, `4` when the source demanded a
captcha.

### `add QUERY` / `add --ticker T | --cvm-code C | --cnpj N | --name TEXT`

Resolves a company against the FCA registry and appends it to the watch list. A bare
`QUERY` is tried as ticker, CNPJ, CVM code, and legal-name substring, in that order; a
typed flag additionally refuses a match found by a different stage than the one named.
Narrowing an ambiguous query down to one company is your decision, never the watcher's.
On a terminal the candidates are numbered and `add` asks which one you meant; Enter — or
Ctrl-D, or Ctrl-C — cancels and writes nothing (exit `0`, because declining is an answer).
Anything that is not one of the numbers is asked again. Adding a company that is already
watched changes nothing and says so.

```
$ co-docs-watcher add bradesco
'bradesco' matches 2 companies by legal_name:

  1  019640  BRADESCO LEASING S.A. ARREND MERCANTIL  (no trading code)
  2  000906  BCO BRADESCO S.A.  (BBDC3, BBDC4)

choose 1-2, or Enter to cancel: 2
added: 000906  BCO BRADESCO S.A.  -> BBDC/
```

With input or output redirected — from cron, in a pipeline — there is nobody to answer, so
an ambiguous query is refused with the candidates listed (exit `1`) instead of prompting at
a terminal that is not there.

Requires a usable registry: if the FCA package cannot be fetched or read, `add` fails
(exit `1`) — monitoring by `run` is unaffected.

### `list [QUERY]`

Prints the watch list, one company per line (`cvm_code  prefix  legal_name`). `QUERY`
narrows by prefix, CVM code, or legal-name substring.

### `rm QUERY`

Removes one company from the watch list, matching `QUERY` by prefix, CVM code, or
legal-name substring — against the watch list, not the registry. Ambiguous queries are
refused with the candidates. Files already in the archive are not touched; the company
just stops being monitored.

### `resolve QUERY` (same flags as `add`)

Shows exactly what `add` would write — CVM code, folder prefix and how it was chosen,
match stage, legal name — without writing anything.

### `run [--monitor]`

One complete pass: lock, reconcile what an interrupted run left, refresh the FCA cache if
stale, sweep every day of the discovery window, enact supersessions and cancellations,
download the queue, purge what aged out of the retention window, and regenerate the inbox
index of every day in it. Progress goes to stdout, warnings and errors to stderr.

`--monitor` sweeps `discovery.monitor_days` instead of `discovery.days` and changes
nothing else — purge, inbox, and the registry refresh are identical between the profiles.
Only a sweep that covered the whole retention window advances the last-completed-sweep
watermark; a monitor run leaves it alone, and warns when it has fallen behind the
retention window. Stop scheduling the full sweep and that warning fires on every run:
give up the older days deliberately or keep the daily `run`.

A registry that cannot be refreshed, or a document that cannot be fetched, is reported
and skipped: the run finishes and exits `1`.

### `reconcile`

Startup reconciliation, on demand: resolves downloads a dead run left in flight, enacts
pending supersessions and cancellations, empties `.tmp/`, and regenerates the inbox.
`run` does all of this by itself; this exists for repairing without sweeping.

### `purge`

Deletes everything older than the retention window — date directories, manifest rows,
inbox indexes — and regenerates the remaining indexes. `run` does this by itself; this
exists for shrinking the window without sweeping.

### `status`

Prints the configuration in use, the retention window and both discovery windows, the
number of watched companies, the document counts per state, and the date of the last
completed sweep. Touches nothing and talks to no one.

Anything the archive still owes — queued, interrupted mid-download, or given up on — is
listed one per line with the last failure recorded against it, verbatim:

```
documents: 41 (1 discovered, 1 downloading, 39 available)
pending (2):
  (161009, 6) discovered 020257 FRE - Formulário de Referência delivered 2026-08-20
    2 failed attempt(s), last 2026-08-25 14:53:06-03:00: document (161009, 6): member
    '020257FRE31-12-2026v6.xml' is not well-formed XML: not well-formed (invalid token)
```

A document is retried for three failed attempts, one per run, and then stays `failed`.

## The watch list

`data_root/companies.yaml` is yours. The watcher appends entries (`add`) and removes them
(`rm`), and never reorders the file; comments and formatting survive its rewrites. An edit
you make while the watcher is writing wins — the watcher abandons its own write rather
than overwrite yours. Editing entries by hand is fine; an entry that fails to parse aborts
the load rather than being silently skipped.

## The archive

```
documents_root/
├── _inbox/2026-08-24.md       # the reading queue: regenerated every run, edits are lost
├── 2026-08-24/PETR/…          # delivery date, then company folder
└── .tmp/                      # download staging; emptied on every start
```

The inbox index of a day lists what each watched company delivered, with links into the
archive — and mentions documents that were cancelled at the source or could not be
downloaded, because silence reads exactly like nothing having been published.

## Deployment

A long-running container that schedules itself. The image is a deployment of the package,
never a dependency of it: the command it fires is the same one-shot a shell runs, and
nothing in `src/` knows the container exists.

```bash
cp config.example.toml config.toml   # windows, retention, modes — the same file a hand-run reads
cp .env.example .env                 # schedules, identity, the host paths
docker compose up -d --build
```

### The two profiles, and why both

`run` sweeps `discovery.days` (7 by default); `run --monitor` sweeps
`discovery.monitor_days` (2). They differ in nothing else: the same queue is drained, the
same retention frontier is purged against, the same indexes are regenerated.

The cadence the environment ships with is the monitor hourly from 07:00 to 23:00 every day,
and the full sweep once a day at 05:10 — seventeen firings of two listing requests, plus one
of seven: about **41 listing requests a day**, against a floor of 15 s between requests and a
per-run fuse of 200.

The daily sweep stays even though the monitor runs seventeen times a day, because frequency
is not coverage. A 2-day window observes today and yesterday however often it fires. What
the sweep alone buys is two things: gaps longer than two days — an outage, an update, a NAS
that was down over a weekend — and the supersessions and cancellations of documents already
archived, which keep their original delivery date at the source and are therefore visible
only to a query of the older day. Raising `monitor_days` to 7 instead of keeping the sweep
would cost about 119 listings a day against 41.

Only the full sweep advances the last-completed-sweep watermark. Turn the sweep off with
`SWEEP_ENABLED=false` and the staleness warning fires on every run — losing the older days is
allowed, losing them quietly is not.

### The container shape

`restart: unless-stopped`, `init: true`, and the scheduler inside the image —
[supercronic](https://github.com/aptible/supercronic), pinned by version and verified by
digest, rendering its crontab from the environment at every start. So a schedule change is an
edit to `.env` and a `docker compose up -d`, and nothing else.

No host cron, no `docker exec`, no Docker socket. A scheduler outside the container has to
reach in, and both ways of reaching in fail quietly: the socket is permissioned by the host,
and a host that tightens it turns every scheduled run into a `permission denied` nobody is
watching for; `docker exec` runs whatever code the *running* container holds, so an image
pulled but never brought up keeps executing the old build indefinitely, with nothing
anywhere saying so.

Ad-hoc commands are `docker compose run --rm watcher …`, against the same three roots:

```bash
docker compose run --rm watcher doctor
docker compose run --rm watcher add --ticker PETR
docker compose run --rm watcher status
docker compose run --rm watcher run --monitor
```

An ad-hoc run that hits the flock and exits `3` is correct behaviour, not a fault: a
scheduled run was already in flight. The scheduled path maps that one code to `0` — and only
that one, so `1`, `2` and `4` still reach whoever watches the container.

### The environment

| Variable | Default | Meaning |
|---|---|---|
| `TZ` | unset (UTC) | the zone the **crontab** is read in |
| `MONITOR_SCHEDULE` | `0 7-23 * * *` | when `run --monitor` fires |
| `MONITOR_ENABLED` | `true` | `false` omits the monitor's crontab line |
| `SWEEP_SCHEDULE` | `10 5 * * *` | when `run` fires |
| `SWEEP_ENABLED` | `true` | `false` omits the sweep's line — and leaves the warning firing |
| `RUN_ON_START` | `sweep` | `sweep`, `monitor` or `none`: what a container start runs |
| `PUID` / `PGID` | `1000` | the identity the watcher runs as, and owns its files as |
| `DATA_ROOT` | `./var/data` | host path mounted at `/watcher/var/data` — must be a **local** filesystem |
| `DOCUMENTS_ROOT` | `./var/documents` | host path mounted at `/watcher/var/documents` — the share |
| `LOGS_ROOT` | `./var/logs` | host path mounted at `/watcher/var/logs` |

Every variable is defaulted only when **unset**. `SWEEP_ENABLED=` is a line someone wrote on
purpose, and the container refuses to start rather than read it as `true`. So does a
container with both profiles disabled, which would schedule nothing at all.

`TZ` exists for cron, not for the application. The watcher anchors every date it writes —
directory names, the retention frontier, the inbox, the watermark, log timestamps — on
`source.timezone`, and is immune to the host zone. The crontab is not: **without `TZ` the
container runs UTC**, and `0 7-23` fires from 04:00 to 20:00 in São Paulo, stopping the
monitor four hours before the source does. An unset `TZ` is warned about at start.

`RUN_ON_START` is the full sweep because a container start usually follows a restart, an
update or downtime — the gap case exactly, which the monitor's two days cannot see. A
catch-up that fails is reported and does not cost the schedule; the next firing is the retry.

`PUID`/`PGID` decide who owns the archive. The modes are declared in `[files]`, so the umask
of whatever started the container is irrelevant; ownership is not, and it is what decides
whether the share can read the files at all. The entrypoint drops from root to that identity
before it does anything else, and never chowns a mount: a mount whose owner is wrong is a
decision you have to see.

### One configuration file

There is no container configuration. `config.toml` — the file described under
[Configuration](#configuration), the one a hand-run reads — is mounted into the container, and
the container is shaped like a checkout so that it fits: the file at `/watcher/config.toml`,
and the three host directories mounted at `/watcher/var/data`, `/watcher/var/documents` and
`/watcher/var/logs`, which is exactly where its relative roots resolve.

A second file would have duplicated everything *except* the paths — `retention`, `discovery`,
`files`, `source` — and two copies of a value that must not differ is a way for it to differ.
So the split is by question rather than by copy: `.env` names where the directories live on
the **host** and when the profiles fire; `config.toml` names how far back to look, how long to
keep, and which modes to write with, on either side of the mount.

Copy it before the first start. Docker creates a *directory* where a bind mount's source is
missing, so a container started without the copy would mount an empty directory over its own
configuration file; the entrypoint refuses to start on that rather than letting every command
report something other than the mistake that was made.

The container filesystem is read-only apart from `/tmp` and the three mounts. That is what
keeps a root the container cannot reach loud: roots written **absolute** — valid, and what a
system-wide installation uses — do not land under `/watcher/var/`, and without the read-only
filesystem the watcher would create them inside the container's own layer, archive into them,
and lose the lot at the next `up --force-recreate`, with nothing to distinguish the result
from a quiet market. Keep the roots relative, or mount them where the file says they are.

`data_root` must be a filesystem local to the host: it holds the SQLite manifest, and SQLite
locking over SMB/NFS is unreliable. `documents_root` is the one that belongs on the share.
