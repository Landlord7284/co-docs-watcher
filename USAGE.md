# co-docs-watcher — command reference

Monitors documents published on RAD/CVM for a watch list of companies, downloads new
deliveries, organizes them by delivery date, and maintains a local reading queue under
`documents_root/_inbox/`.

The canonical mode is **one-shot**: `run` does one complete pass and exits. There is no
daemon; schedule the one-shot with whatever your platform provides (cron, launchd, a shell
loop) if you want periodic runs.

```bash
python -m co_docs_watcher doctor     # check config, roots, timezone, source
python -m co_docs_watcher add --ticker PETR
python -m co_docs_watcher run        # the canonical mode
```

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
Unknown sections and unknown keys are rejected. `data_root` and `documents_root` must be
absolute paths.

```toml
[paths]
data_root = "/home/user/watcher/data"           # private: watch list, manifest, lock, cache
documents_root = "/home/user/watcher/documents" # the shareable archive

[retention]
days = 7                       # retained dates, including today

[registry]
max_age_days = 7               # days a cached FCA package is used without re-downloading

[source]
timezone = "America/Sao_Paulo" # anchors dates, directory names, log timestamps
min_request_interval = 5.0     # seconds between requests; the backend is fragile
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
in use, both roots (created if missing, probed for writability), the timezone, the watch
list, the registry cache age, and the source (one real listing request for today). Exits
`0` when everything passed, `1` when something failed, `4` when the source demanded a
captcha.

### `add QUERY` / `add --ticker T | --cvm-code C | --cnpj N | --name TEXT`

Resolves a company against the FCA registry and appends it to the watch list. A bare
`QUERY` is tried as ticker, CNPJ, CVM code, and legal-name substring, in that order; a
typed flag additionally refuses a match found by a different stage than the one named.
Ambiguous queries are refused and the candidates listed — narrowing is your decision, not
the watcher's. Adding a company that is already watched changes nothing and says so.

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

### `run`

One complete pass: lock, reconcile what an interrupted run left, refresh the FCA cache if
stale, sweep every day of the retention window, enact supersessions and cancellations,
download the queue, purge what aged out, and regenerate the inbox index of every day in
the window. Progress goes to stdout, warnings and errors to stderr.

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

Prints the configuration in use, the current window, the number of watched companies, the
document counts per state, and the date of the last completed sweep. Touches nothing and
talks to no one.

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
