# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`co-docs-watcher` monitors documents published on RAD/CVM (the Brazilian securities regulator's filing system) for a watch list of companies, downloads new deliveries, organizes them by delivery date, and maintains a local reading queue.

The product is a **reading queue for people**. Success is measured by opening the day's folder and seeing what was published — not by historical completeness. The focus is material facts, notices, and general filings. DFP and ITR are archived because they arrive in the same flow and serve release-day checking.

Out of scope: parsing document content, long-term preservation, and any heuristic correlation between versions — the source provides supersession ready-made.

## Documentation

| File | Role |
|---|---|
| `CLAUDE.md` | this file — scope, conventions, architecture, invariants |
| `README.md` | the public front page: what the project is, install, the five commands, links |
| `docs/fonte-rad.md` | full observed contract of the RAD/ENETWeb source, with measurement dates and payload examples |
| `USAGE.md` | user-facing command reference and deployment guide, kept in step with the CLI and the container |

Where documentation and code diverge, the code wins and the document is corrected — silent divergence is forbidden. Violating an invariant requires explicit declaration and justification here. Every claim about the source carries the date it was measured: the CVM publishes no API contract, and an undated number ages silently. Re-verify; never trust indefinitely.

## Language rule (strict)

All code, configuration, file names, database schema, log output, comments, docstrings, commit messages, and technical documentation in this repository are in **English**. `docs/fonte-rad.md` and day-to-day conversation might be in **pt-BR**. This never crosses into the code.

Conversation names concepts in Portuguese; the English vocabulary is **fixed** so that different sessions do not invent competing translations. Translate, never transliterate:

The only exception is data that is not ours: RAD wire-format names (`temErro`, `SolicitarCaptcha`, `numSequencia`, `numVersao`, `numProtocolo`) are quoted literally when describing the wire, and take English names as model attributes — `numSequencia` → `document_id`, `numVersao` → `version`, `numProtocolo` → `protocol`. `CNPJ` keeps its regulatory name.

## Commands

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                  # unit + contract + integration
.venv/bin/pytest -m live          # re-measures the real source (slow, needs network)
.venv/bin/ruff check src tests
```

Run a single test with `pytest path/to/test_file.py::test_name`. The `live` marker is deselected by default.

### CLI

The canonical mode is **one-shot**. No daemon inside the package: a periodic mode, if it ever exists, is built on top of the one-shot, outside the package.

```bash
python -m co_docs_watcher doctor     # config, roots, timezone, source
python -m co_docs_watcher add --ticker PETR
python -m co_docs_watcher add --cvm-code 009512
python -m co_docs_watcher run             # the canonical mode: sweeps discovery.days
python -m co_docs_watcher run --monitor   # the frequent profile: sweeps discovery.monitor_days
```

Subcommands: `doctor`, `add`, `list [QUERY]`, `rm QUERY`, `resolve`, `run [--monitor]`, `reconcile`, `purge`, `status`. `run` takes a profile flag and never a number — the cron line carries only which profile, so retuning a window is a configuration edit and nothing else. `doctor` prints both resolved discovery windows and which profile sweeps each; `status` reports them beside the retention window.

| Exit code | Meaning |
|---:|---|
| `0` | clean |
| `1` | ran with isolated failures |
| `2` | invalid configuration |
| `3` | another instance holds the lock |
| `4` | the source demanded a captcha — reduce frequency |

Exit code `4` exists because `SolicitarCaptcha: "S"` is not a transient failure: retrying makes it worse.

Flag names are English, always. `--config` is valid before or after the subcommand. Any flag whose destination differs from its option string needs an explicit `metavar`, or `argparse` leaks the internal name into the help text.

Config discovery chain, in order: `--config` → `$CO_WATCHER_CONFIG` → `./config.toml` → `./co-docs-watcher.toml` → `~/.config/co-docs-watcher/config.toml` → built-in defaults. Falling back to the defaults **logs a deliberate warning**: they point at `./var/…`, and a silent fallback means operating on a different archive than intended. `data_root`, `documents_root` and `logs_root` may be written relative, and are then resolved against the **directory of the configuration file**, never the working directory: a project-local installation is a checkout with a `config.toml` naming `var/data`, `var/documents` and `var/logs`, and it archives in the same place whether it is run by hand or from cron. Anchoring on the working directory would let one file mean a different archive per caller — the same silent-second-archive failure the built-in defaults warn about. Unknown sections and unknown keys are rejected rather than ignored: a typo that silently keeps a default is a configuration that lies. A path named by `--config` or `$CO_WATCHER_CONFIG` that does not exist refuses to start instead of falling through to the next candidate — both are explicit requests.

| Key | Default | Meaning |
|---|---|---|
| `paths.data_root` | `./var/data` (fallback only) — relative to the config file | private root: YAML, manifest, lock, FCA cache |
| `paths.documents_root` | `./var/documents` (fallback only) — relative to the config file | the shareable archive, and `.tmp/` |
| `paths.logs_root` | `./var/logs` (fallback only) — relative to the config file | holds `co-docs-watcher.log` |
| `logging.max_bytes` | `5242880` | bytes the log file reaches before it rotates |
| `logging.backups` | `5` | rotated log files kept |
| `retention.days` | `7` | `N`, retained dates **including today** |
| `discovery.days` | `retention.days` | dates swept by `run`, ending today; capped by retention |
| `discovery.monitor_days` | `2` | dates swept by `run --monitor`; capped by `discovery.days` |
| `files.directory_mode` | `0o755` | mode of every directory created under `documents_root` |
| `files.file_mode` | `0o644` | mode of every file placed under `documents_root` |
| `registry.max_age_days` | `7` | days a cached FCA package is used without re-downloading |
| `source.timezone` | `America/Sao_Paulo` | anchors dates, directory names, and log timestamps |
| `source.min_request_interval` | `15.0` | seconds between requests; the backend is fragile |
| `source.max_requests_per_run` | `200` | safety fuse for a single run |
| `source.max_listing_bytes` | `67108864` | what one listing answer may weigh, counted as it streams |
| `source.max_download_bytes` | `268435456` | what one download may weigh, counted as it streams |
| `source.max_extracted_bytes` | `1073741824` | what one container may inflate to, uncompressed |
| `source.retries` | `3` | attempts after a transient failure; `0` disables them |
| `source.backoff_initial` | `15.0` | first wait between attempts; refused below `min_request_interval` |
| `source.backoff_factor` | `4.0` | what the wait is multiplied by each attempt |
| `source.base_url` | `https://www.rad.cvm.gov.br/ENETWeb/` | overridden only to point a test server or a mirror |

`[prefix_overrides]` is a section of its own, keyed by CVM code — `"003549" = "SCHLOSSER"` — and settles a folder name the resolver got typographically right and humanly wrong. Its keys are data rather than schema: this is the single place where an unknown key is not a typo — but it is still a CVM code, *read* as one and never distilled out of something else, since a key that merely contains digits aims the override at a company nobody named. Keys and values are validated, never sanitized, because an override is a deliberate act and repairing one quietly would name a folder after something nobody wrote.

## Deployment

Unattended operation is a container that schedules itself, and it lives beside the package rather than inside it: `Dockerfile`, `compose.yaml`, `docker/entrypoint.sh`, `docker/run-profile.sh`, `docker/config-timezone.py`, `.env.example` and the `docker-publish` workflow. Nothing under `src/` knows any of it exists, and what the scheduler fires is the same one-shot a shell runs.

- **The scheduler is in the image.** `supercronic` — a static binary that runs unprivileged and logs its jobs to stdout — pinned by version and verified by digest, taking a crontab rendered from the environment at every start. No host cron, no `docker exec`, no Docker socket: a scheduler outside the container has to reach in, and both ways of reaching in fail quietly. The socket is permissioned by the host, so a host that tightens it turns every scheduled run into a `permission denied` nobody is watching for; `docker exec` runs the code of the *running* container, so an image pulled but never brought up keeps executing the old build indefinitely. Silent divergence is what this project forbids everywhere else.
- **The image is published, and the compose pulls it rather than building it.** `ghcr.io/landlord7284/co-docs-watcher`, built for `linux/amd64` and `linux/arm64` by `.github/workflows/docker-publish.yml`, which calls the CI workflow and publishes nothing from a tree that does not lint and test. A push to `main` moves `latest`, a `v*` tag publishes the version and its major.minor, and every build also carries `sha-<short>` so a rollback has a name. `IMAGE_TAG` in `.env` decides which of those a host follows, and an update is `docker compose pull` **and** `docker compose up -d`: a pull that is never brought up leaves the container on the old image, the same silence `docker exec` is refused for. The git tag and `version` in `pyproject.toml` are one number — `--version` reports what the package declares, so a tag ahead of it publishes an image that introduces itself as the version it is not.
- **Schedules live in the environment, windows live in the configuration file.** They answer different questions — how often to look, and how far back — and a change of cadence must not read like a change of coverage. The crontab carries a profile name and never a number, exactly as the `run` flag does. The cadence shipped is the monitor hourly from 07:00 to 23:00 and the full sweep daily at 05:10: about 41 listing requests a day, which neither the image nor the entrypoint may bring closer to `source.min_request_interval` or `source.max_requests_per_run`.
- **Exit `3` is mapped to `0` on the scheduled path, and there alone.** A monitor firing while the daily sweep still holds the flock did nothing wrong, and a scheduler that reports it as a failure trains its reader to ignore failures. Every other code passes through — `|| true` would swallow `1`, `2` and `4` with it, and `4` is the one code that must reach whoever watches the container. An ad-hoc `docker compose run --rm watcher …` is not the scheduled path: it reports `3` unchanged, because someone typed it and is owed the truth.
- **`RUN_ON_START` is the full sweep.** A container start usually follows a restart, an update or downtime — the gap case exactly, which the monitor's two days cannot see. A catch-up that fails is reported and never costs the schedule: a container that refuses to start over one bad run stops monitoring entirely over a source that was briefly down.
- **`TZ` is derived from `source.timezone`, never declared beside it.** Every date the watcher writes is anchored on `source.timezone` and is immune to the host zone; the crontab is not, and left to itself the container runs UTC and `0 7-23` fires four hours early. The entrypoint reads the mounted configuration and exports `TZ` from it — before the ad-hoc branch, so `doctor` reports the zone the scheduler would run under, and after the privilege drop, so a configuration the target identity cannot read fails at the derivation instead of somewhere further away. Four things are refused with exit `2` rather than guessed at: a `CO_WATCHER_CONFIG` that is empty or names something that is not a file, since resolving it would be a second copy of the discovery chain; a `[source]` that declares no `timezone`, since the shell carries no default and `config.example.toml` ships the value; a zone the system zone database does not have, since the scheduler resolves the name there and falls back to UTC in silence; and a `TZ` in the environment that contradicts the declaration. A `TZ` that agrees is redundant and accepted — orchestrators inject one unasked, and it is not a second answer.
- **`PUID`/`PGID` are the identity, `[files]` the modes.** The entrypoint drops from root to that identity before anything else runs, so the archive is owned by whoever the share expects rather than by whoever built the image. A mount is never chowned: an ownership that is wrong is a decision the operator has to see. Every variable is defaulted on unset and never on empty, and a container that would schedule nothing refuses to start.

**There is one configuration file, and the container mounts it.** `config.toml` is the project's, not the host's: the container is shaped like a checkout — the file at `/watcher/config.toml` and the three host directories mounted at `/watcher/var/data`, `/watcher/var/documents` and `/watcher/var/logs`, which is where its relative roots resolve. Everything but the paths — `retention`, `discovery`, `files`, `source` — is read identically on both sides of the mount, because two copies of a value that must not differ are a way for it to differ. The split is by question: `.env` answers where the directories are on the host and when each profile fires, `config.toml` answers how far back, how long, and with which modes.

The container filesystem is read-only apart from `/tmp` and those three mounts, which is what keeps the one remaining way to get this wrong loud. Absolute roots stay valid — they are what a system-wide installation uses — but they do not land under `/watcher/var/`, and on a writable filesystem the watcher would create them inside the container's own layer, archive into them, and lose them at the next recreation: the silent second archive the built-in defaults already warn about. `data_root` is a mount of its own because SQLite locking over SMB/NFS is unreliable; `documents_root` is the one that goes on the share.

## Architecture

Python 3.12+. Dependency ceiling: `httpx`, `ruamel.yaml`, `tzdata`, and nothing else beyond the standard library. `ruamel.yaml` because comments must survive YAML rewrites; `tzdata` because a minimal Linux image ships no IANA database — which would be a crash before any error handling runs.

```
src/co_docs_watcher/
├── cli.py            entry point, subcommands, exit codes
├── archive_modes.py  the declared creation modes, and the three ways they are applied
├── clock.py          source timezone, window, directory names
├── config.py         TOML discovery and loading
├── errors.py         exception hierarchy
├── lock.py           flock
├── logging_setup.py  formatTime anchored on the source timezone, and the log file
├── models.py         the neutral core: SourceDocument, LocalState, Delivery
├── run.py            orchestration of one run
├── source.py         the Source protocol the pipeline depends on
├── text.py           identifier normalization and folder-safe names
├── cvm/              FCA registry: who the companies are, independently of what they publish
│   ├── registry.py   records, package parsing, the 1:1 guard
│   ├── cache.py      yearly packages, staleness, refresh that cannot poison the cache
│   ├── search.py     ticker -> CNPJ -> CD_CVM -> legal name
│   └── ticker.py     the root rule and the fallback chain
├── rad/              the source — nothing outside imports from here
│   ├── client.py     POST, backoff, temErro translation
│   ├── listing.py    window sweep -> list[SourceDocument]
│   ├── schema.py     the 12 fields -> SourceDocument
│   ├── download.py   GET, content sniffing, ZIP extraction
│   └── vocabulary.py category table copied from cboDocumentos
├── manifest/         db.py (connection, pragmas, migrations) + repo.py (repositories)
├── scope/            the watch list: which companies this archive is about
│   ├── models.py     what an entry stores, and why it stores it
│   ├── store.py      round-trip YAML, hash guard, atomic rewrite
│   └── resolver.py   a query -> an entry
└── pipeline/         discover, fetch, reconcile, purge, inbox
```

**The seam has one rule, worth a CI-failing architecture test: no module outside `rad/` imports `rad/`.** The single exception is `run.py`, the composition root: something has to build the adapter and hand it to the pipeline as a `Source`. The architecture test carries that one-item allowlist and nothing else; test modules are exempt, since contract tests exist precisely to import `rad/`. The pipeline depends on the `Source` protocol in `source.py`, never on the adapter, and the manifest stores `SourceDocument`, a neutral dataclass — never the source row.

### One run

1. **lock** — `flock` on `data_root`.
2. **reconcile** — intermediate states left by an interrupted run.
3. **registry** — refresh the FCA if stale. Failure here blocks new registrations, never monitoring.
4. **discover** — one sweep per day of the discovery window, **most recent day first**, filtered locally against the watched CVM codes; `Ativo` goes to the queue, `Inativo`/`Cancelado` reconcile what is already on disk: the sweep flags the state and the enactment — the same one step 2 performs — runs immediately after it, so a cancellation observed today takes the file with it today.
5. **fetch** — download to `.tmp/`, validate, extract if ZIP, atomic `rename`. The queue drains **most recent delivery date first**, publication order kept within a day.
6. **purge** — whatever aged out of the window.
7. **inbox** — regenerate the index of *every* day in the window, not just today's.

Step 7 is not zeal: a document downloaded on Monday can be deactivated on Wednesday, and Monday's index would keep pointing at a file that no longer exists. A past day is *rewritten*, never invented — the first run does not fabricate indexes for days the watcher was not there.

### SQLite

`PRAGMA journal_mode = WAL`, `synchronous = NORMAL`, `foreign_keys = ON`, `busy_timeout = 30000`. Migrations versioned by `PRAGMA user_version`. A schema newer than the build understands **refuses to open**, never degrades. No HTTP request happens inside an open transaction: pages are collected in memory first, and every write goes through an explicit `BEGIN IMMEDIATE`.

Four tables: `documents` (keyed `(document_id, version)`, carrying the source's `status` and our `local_state`), `document_files` (one row per file, with `sha256`, size, and the stability marker), `download_attempts` (what the retry budget is spent against, and where the reason a document is still owed is read back from — `status` lists every non-terminal document with its last failure, verbatim, so that "why is this missing?" is answered without opening SQLite), and `sync_state` (the watermark). The file rows cascade on delete, which is why `foreign_keys = ON` is not decoration.

## Archive layout

Delivery date is the axis; within it, the company is the folder. A day without publications from a company creates no empty folder.

```
documents_root/
├── _inbox/
│   ├── 2026-08-24.md
│   └── 2026-08-21.md
├── 2026-08-24/
│   ├── PETR/
│   │   ├── Fato-Relevante_160310_V01.pdf
│   │   └── ITR/
│   │       ├── ITR_160282_V01.pdf
│   │       ├── 009512ITR30-06-2026v1.xml
│   │       └── ...
│   └── VALE/
│       └── Aviso-aos-Acionistas_160295_V01.pdf
└── .tmp/
```

**A subfolder is for structured packages only.** What decides the layout is the shape of the delivery, not the shape of the response: a delivery that is one filing lands as one named PDF in the company's folder, whether the source answered a bare PDF or a container the adapter unwrapped. The day's directory listing has to read as the day's publications, not as a row of folders to open one by one.

Four rules that look like detail and are not:

- **An IPE container is unwrapped at the boundary and never reaches the archive.** An eventual filing delivered through the IPE module arrives as a ZIP with exactly two members: `InformacoesPeriodicasEventuais.xml`, an envelope carrying metadata the listing already gave us, and the filing itself under a name that runs CVM code, dates and protocol together with an invented `.ipe` extension. The envelope is validated, read for the extension it declares, and discarded; the attachment leaves as the single file of the delivery, named in the usual convention. An envelope with any number of attachments other than one is an unmeasured shape and is archived whole — discarding the envelope is the one irreversible move here, and it is not made on a hunch. So is an attachment that is itself a container: what an eventual filing turns out to be is a PDF behind an invented name, and reducing a container would file it as the delivery's single document with none of the checks a container gets — no member names, no inflation cap, no XML looked at.
- **The generated PDF inside a structured ZIP is renamed.** It arrives with the generation instant in its name — two downloads produce two names. The on-disk name is imposed by the watcher, in the same convention as the rest. Other members of a structured package keep their origin names, which are stable.
- **A category subfolder carries identity in the PDF name, not the directory.** Two structured deliveries of the same category on the same day must not collide; if they would, the subfolder gains a `_V{version}` suffix.
- **`.tmp/` lives under `documents_root`**, so `rename` stays atomic within a single filesystem.

The inbox index includes the subject (listing field 11). A cancelled document does not become a file, but is mentioned in the inbox of the day it was observed, and so is one the watcher could not fetch — silence about a document reads exactly like nothing having been published.

## Company identity

Folders are named by the ticker root from the FCA registry, with fallbacks. The root rule:

```
^([A-Z][A-Z0-9]{3,}?)(\d{1,2}[A-Z]?)$
```

Group 1 is the root and is **lazy**: it stops at the root and hands every class digit to group 2 — `PETR4 → PETR`, `POMO3/POMO4 → POMO`, `EQMA3B → EQMA`, `B3SA3 → B3SA`, `KLBN11 → KLBN`, `GOLL54 → GOLL`. A greedy group 1 would keep the first digit of a two-digit class and name a folder `KLBN1`, which is what nobody calls the company; 5 of the companies with a valid root list nothing but such a class (2026-08-26). When a company still carries more than one root the codes are simply unrelated — `SC303`/`SCL04` — so **the shorter root wins** as a first cut, remaining ties break alphabetically for stability rather than for correctness, and `[prefix_overrides]` is where an operator disagrees. Measured on the 2026 FCA (2026-08-26): 346 of 675 companies carry at least one valid root, 2 carry more than one, and after the tie-break there are **zero root collisions** between companies; `CNPJ → CD_CVM` is strictly 1:1 in both directions.

`Codigo_Negociacao` is free text and must be distrusted: dozens of companies fill it with junk (`'NÃO HÁ'`, `'B3'`, bare numbers). The resolver **validates the root against the rule above** and falls back when it fails. Legitimate class-digit-less codes matching `^[A-Z]{4,5}$` (e.g. `LMED`, `TEGA`) already *are* the root and are accepted as such.

Fallback chain: **1)** validated ticker root (`PETR`) → **2)** reduced, sanitized legal name (`PLASCAR-PARTICIPACOES`) → **3)** zero-padded CVM code (`009512`).

The folder name is a **snapshot, not identity**: a folder once created is never renamed. Identity lives in the manifest and in `(id, version)` inside the file name. If a company lists on the exchange after monitoring began, older days stay under the old name — that is correct, not a bug.

Registry search resolves **ticker → CNPJ → CD_CVM**, with fallback to a numeric CVM code and to a normalized substring of `Nome_Empresarial` **and** `Nome_Empresarial_Anterior` — the previous legal name matters more than it seems: 495 of the 675 companies in the 2026 FCA have one (2026-08-24).

### The registry (condensed contract)

The FCA (*Formulário Cadastral*) is the annual registration form, published as CVM open data at `https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{year}.zip` — public, unauthenticated, one ZIP per year of ISO-8859-1 CSVs delimited by `;`. Measured 2026-08-24: the 2026 package is 359 387 bytes and holds 675 companies.

- **Two members are read**: `fca_cia_aberta_geral_{year}.csv` for identity (`CNPJ_Companhia`, `Codigo_CVM`, `Nome_Empresarial`, `Nome_Empresarial_Anterior`, `Situacao_Registro_CVM`) and `fca_cia_aberta_valor_mobiliario_{year}.csv` for trading codes. A missing member or a missing column aborts the parse: an empty registry is indistinguishable from a company that is not listed.
- **Two years are always read**, the previous one as the base and the current one on top. The yearly package holds only companies that filed *that* year, so in February the current year alone would be a registry of a few dozen companies. A year not published yet (every January) is expected, not a failure.
- **The latest version per company is re-derived** from `(Versao, ID_Documento)`, and trading codes are joined on the selected version's `ID_Documento`. The published general member already arrives reduced to one row per company — a promise nobody made, so it is not relied on.
- **Only codes with an empty `Data_Fim_Negociacao` are active** (55 of 963 rows carried an end date on 2026-08-24). `Codigo_Negociacao` is free text: it arrives in lower case (`tgma3`), as junk (`B3`, `NÃO HÁ`, bare numbers), or empty for debentures and commercial notes.
- **The cache is under `data_root/cvm-cache/`**, one file per year, refreshed only when older than `registry.max_age_days`. A download that fails, arrives corrupt, or exceeds the size cap **never replaces the cached snapshot**: the previous one stays and the run continues on it, loudly. Only the absence of any usable snapshot raises — and that blocks `add`, never `run`.

### The watch list

`data_root/companies.yaml` is a file the operator owns; the watcher appends to it and removes from it, and never reorganizes it. One key, `companies`, holding one entry per company:

```yaml
companies:
  - cvm_code: '009512'      # what the sweep is filtered against
    prefix: PETR            # the folder name — a snapshot, never renamed
    prefix_source: ticker   # override | ticker | legal_name | cvm_code
    matched_by: ticker      # ticker | cnpj | cvm_code | legal_name | previous_legal_name
    legal_name: PETROLEO BRASILEIRO S.A. PETROBRAS
```

`prefix_source` and `matched_by` are recorded because months later "why is this folder called `009512`?" and "why is this company here at all?" must be answerable without re-running anything. Entries are **appended, never sorted** — the order of the file is the human's. An entry that fails to parse aborts the load instead of being skipped: an entry dropped in silence is a company that stops being monitored in silence. `cvm_code` and `prefix` are checked for their shape on every load and never repaired: a code is *read*, never distilled out of free text, because a value that merely contains digits reduces to a valid code for another company and the sweep would go on monitoring someone nobody asked for; and `prefix` satisfies the same `PREFIX_RULE` an entry in `[prefix_overrides]` is held to, because it is joined to `documents_root` to build the company's folder and it is the same operator naming the same folder. The regulator's own spelling of a code, `00951-2`, is a spelling and not a second code. `add` never chooses between candidates itself: with a terminal on both stdin and stdout it numbers them and asks which one, and an empty answer cancels without writing; with either stream redirected there is nobody to answer, so the query is refused and the candidates are handed back. The prompt lives in `cli.py` alone — the resolver takes a `Chooser` and nothing below the CLI ever reads from stdin.

## Document identity and states

Publication identity is `(num_sequencia, num_versao)`. It is not lineage identity: **every resubmission gets a new `num_sequencia`**, so "same id, higher version" never fires. The source marks exactly one `Ativo` per lineage and demotes the rest to `Inativo`; the listing preserves history, so supersession is a column, not a heuristic. Lineage, when grouping is needed, comes from `(cvm_code, category, reference_date)` — structurally confirmed by `num_protocolo`.

`num_protocolo` is **persisted, not derived**: it is a required download argument, and without it a document discovered today cannot be downloaded tomorrow without re-listing.

| State | Meaning |
|---|---|
| `discovered` | Seen in the listing as `Ativo`, not yet downloaded. |
| `downloading` | In flight. Reconciled on the next start. |
| `available` | On disk, validated. |
| `skipped` | Seen, but outside current criteria. Re-evaluated every run. |
| `failed` | Failed after retries. Does not block the batch. |
| `deactivated` | Was `available`, went back to `Inativo`: file removed, row stays. |
| `cancelled` | Went `Cancelado`: file removed, and the day's inbox mentions it. |
| `purged` | Aged out of the window. Nothing more. |

`deactivated` and `cancelled` exist separately from `purged` so that `purged` keeps meaning "aged out" and nothing else — otherwise the archive loses the ability to explain why a file disappeared.

**Content hash**: structured-document ZIPs are generated on demand — two downloads of the same ITR differ, and entry-by-entry comparison shows only the generated PDF changes; standalone PDFs are stable. The hash is therefore recorded **per file, with a stability marker**, serving integrity and auditing — never deduplication, which is and remains `(num_sequencia, num_versao)`.

## The source (condensed contract)

Full contract, with measurement dates and payload examples, in `docs/fonte-rad.md`. The operational facts:

- **One PageMethod for search**: `POST …/frmConsultaExternaCVM.aspx/ListarDocumentos`, JSON in, no session, no cookies, no `__VIEWSTATE`. `empresa` is a comma-separated list of six-digit zero-padded CVM codes **with a leading comma**; empty means the whole market. `dataDe`/`dataAte` (`dd/MM/yyyy`, both inclusive, only read with `periodo: "2"`) filter by **delivery date**.
- **Discovery is a global sweep**: one request per day of the window with `empresa` empty, filtered locally against the watch list. No pagination, no truncation observed; the CVM code arrives in field 0 of every row, so routing is exact and one request per day serves a watch list of any size. There are no per-company queries. Reference volume: ~450 documents/day market-wide.
- **The envelope is JSON, the content is not**: rows come in a single string, `$&&*` between rows, `$&` between fields, no escaping. The trailing row separator leaves an empty last element — discard it. **Validate exactly 12 fields per row and abort the collection on divergence**: a subject containing `$&` would corrupt the parse silently. The count is not the shape: eight of the twelve refuse a value that is not theirs, and the four that carry prose — legal name, category, type, subject — are checked for the markers of the fields that are not prose, `<spanOrder>` and the download call. A column redefined in place leaves twelve fields with eight of them still valid, and that is the drift a count cannot see. What no structural check reaches is an exact swap of two prose fields for each other; it is stated in `rad/schema.py` rather than argued away.
- **HTTP is always 200.** Business errors and backend failures arrive as `temErro: true` with text in `msgErro` — that is a retryable `TransientSourceError`, never an empty result. A robot that only checks status codes records silence as "nothing new".
- **Fields to parse**: 0 CVM code (hyphen-formatted, `00951-2`), 1 legal name, 2 category, 3 type, 4 species, 5 reference date, 6 delivery date, 7 status (`Ativo`/`Inativo`/`Cancelado`), 8 version, 9 modality (`AP`/`RE`/`RC`, the third observed 2026-08-25), 10 action-icons HTML (carries the download arguments), 11 **subject**. Fields 4–6 embed a normalized sort key in `<spanOrder>` tags (`20260804`) — parse that, not the display format.
- **Download** is a single GET (`frmDownloadDocumento.aspx?Tela=ext&numSequencia=…&numVersao=…&numProtocolo=…&descTipo=&CodigoInstituicao=1`) for every category; only the content differs (PDF or ZIP). The four arguments come from `OpenDownloadDocumentos(...)` in field 10. Category does not determine the type, not even for eventual filings: a Fato Relevante measured 2026-08-24 arrived as a bare PDF and a Comunicado ao Mercado measured 2026-08-25 arrived as an IPE container. Only the content signature decides.
- **`Content-Type` always lies** (`text/html` for PDFs and ZIPs alike) and `Content-Disposition` names are useless. The real type comes from the content signature (`%PDF-`, `PK\x03\x04`); the on-disk name is built by the watcher.
- **Status and modality are not server filters** — the API always returns `Ativo`, `Inativo`, and `Cancelado` together. Filtering is the watcher's responsibility, and a cancellation arriving for free is news, not noise.
- **Category filtering on the server is a trap**: a wrong category code returns zero rows with no error, indistinguishable from a quiet company. The watcher always requests `EST_-1,IPE_-1_-1_-1` (everything) and filters locally, which makes the trap unreachable. If server-side filtering is ever added, the code table must be copied from `cboDocumentos` (in `rad/vocabulary.py`), never deduced.
- **`SolicitarCaptcha: "S"` ends the run** with exit code `4`. It is not backoff material: there is no legitimate workaround, and insisting aggravates the trigger. Reduce frequency instead. The field is read as a vocabulary of two, `"S"` and `"N"`, and a third spelling is contract divergence rather than a quiet no: the reaction to a demand this build fails to recognize would be to carry on requesting, which is the one reaction that makes the answer worse.
- **The backend is fragile**: the WCF service behind the page drops under load (observed after ~a dozen calls in a few minutes, staying down for about an hour). Exponential backoff, a per-run request cap, and a minimum interval of **15 s** between requests. That figure is chosen with its eyes open: a dozen calls in a few minutes is roughly one every 15 s, so the floor sits *at* the estimated spacing of the only failure ever observed, not beyond it — and the threshold itself is unknown and can only be learned by provoking it, which costs an hour of the source each time. What makes it acceptable is the ordering: the window is swept and the queue drained most recent day first, so a run the source cuts short has already spent itself on the days a reader opens. `max_requests_per_run` is a fuse and never a working figure — a run sweeps the window in 7 requests and downloads one per new document.
- **The wire format is young** — the system has been live since 06/07/2026 — so divergence is expected rather than impossible: when the field count or the envelope format changes, fail loudly and alert, never degrade silently.

## Invariants

Violating any of these requires explicit, justified declaration in this document — silent divergence is what must never happen.

- Identity/dedupe key is `(document_id, version)`; the content hash never dedupes.
- Every run queries its whole discovery window. There is no incremental interval. Two windows exist, both ending today: purge, the inbox, and the retention frontier keep the retention window `[today - (N-1), today]`; discovery sweeps a window of its own — `discovery.days` under `run`, `discovery.monitor_days` under `run --monitor` — constrained at configuration load never to exceed retention. The bound is not symmetric because the failures are not: a discovery window wider than retention re-downloads what purge deletes, so it is refused; a discovery window narrower than retention is coverage deliberately traded for frequency, so it is allowed — and it is why the watermark refuses to advance for it. The window is swept and the queue drained **most recent day first**: a run is cut short by a captcha, by the request budget, or by the backend going down, and what it managed to do should be the days a reader opens first, not the days purge is about to reach. Order is a policy of the sweep and the queue, never a property of the window — the inbox and the retention frontier read their window as a set of days.
- The watermark records completed progress and raises alerts, never feeds the interval. Only a sweep that covered the retention window advances it; a narrower sweep reads it and may warn, and never writes it — recording a narrow sweep's last day would assert "everything through today has been observed" over days never asked about, greening the gap alarm over a rotting window. Staleness is always compared against the **retention** frontier, never the discovery window's: retention is what the archive promises to hold. The intended consequence: stop running the full sweep and the warning fires every day, making the loss of coverage a visible decision instead of a silent omission.
- A rediscovered document updates mutable fields and never triggers a new download. `status` in the manifest means "last state observed within the window".
- Written file extension is decided by the actual content, never by the name it arrived under. For a **response** the content signature is the whole rule and there is no second hint: `Content-Type` says `text/html` for PDFs and ZIPs alike and `Content-Disposition` names nothing usable, so a body whose signature this build does not recognize is refused rather than stored on the word of the one part of the answer the source is measured getting wrong — and no header travels past the transport, so no later decision can be tempted by one. Inside a **container** there is a second hint, the envelope's `ExtensaoArquivo`, because it is there that this source hides a PDF behind an invented extension: signature first, declaration after, and a declared extension is validated before it may name a file. An attachment that satisfies neither keeps its origin name and its container.
- Validate content; a successful parse is not enough. Reject HTML bodies even when well-formed (an error page arrives with HTTP 200), reject empty ZIPs or entries containing `../`, require a plausible XML root, parse with external entity resolution disabled, cap response sizes.
- A declared **encoding** is a hint like any other the source writes about itself. An XML member is parsed under the encoding it declares and, failing that, under ISO-8859-1 before it may be refused: the retry is narrow because ISO-8859-1 decodes every byte, so a member that still fails is malformed in structure rather than mislabelled, and the error reported is the first one, under the declaration the document itself made. What is stored are the delivered bytes, wrong declaration included — validation decides whether a member may be archived, never what it says.
- The archive is handed to other people, so the modes it is created with are **declared, never inherited**: `files.directory_mode` and `files.file_mode` apply to the date directory, the company directory, a category subfolder, every document and container member, and the inbox indexes. An explicit `chmod` follows every creation, because a `mode=` argument alone is not enough for three separate reasons: the kernel writes `mode & ~umask`, `parents=True` creates the parents from `0o777` instead of the mode asked for, and `exist_ok=True` reapplies nothing to a directory an earlier run created — so a directory is re-stamped on every placement into it. Modes are set on the staging file, or the whole staging tree, **before** the `os.replace` that places it: placement is one rename, and nothing may be visible in the archive under a mode that is about to be corrected. Applied in the pipeline and never in `rad/`, which knows nothing about configuration.
- Three roots: `data_root` (private — YAML, SQLite manifest, lock, FCA cache; must live on a filesystem local to the process, since SQLite over SMB/NFS has unreliable locking), `documents_root` (the shareable archive), and `logs_root` (the log file). They are separate because they are given away separately: one is state the watcher reads back, one is what people are handed, one is what an operator tails. All three are absolute by the time anything downstream sees them; a relative value in the configuration file is anchored on that file's own directory. Download temporaries (`.part`) go in `documents_root/.tmp/` so `rename` is atomic.
- Date directories are `yyyy-mm-dd`, zero-padded, keyed on **delivery date** — lexicographic order must equal chronological order.
- `N` is the number of retained dates including today: `first_retained_date = today - (N-1)`. Purge and inbox use this same frontier, and the discovery window is capped by it, or discovery re-downloads what purge deletes. Retention is a single global sliding window, configurable; per-category retention is backlog.
- The lock is `flock`, not a pidfile: the kernel releases it when the owner dies, so there is no stale lock to detect and a crash never leaves the watcher stuck.
- Download state machine `discovered → downloading → available`, with startup reconciliation. Filesystem and SQLite do not form an atomic transaction: idempotency rests on the manifest plus reconciliation, never on file existence.
- YAML write protection: atomic temp file + `rename`, **and** a hash comparison of the on-disk content against what was loaded before renaming — if it changed, do not overwrite; record a visible conflict and preserve the human's edit. `mtime` is not enough. Comments must survive the rewrite (hence `ruamel.yaml`).
- Timezone is anchored on the source, never the host or container — for "today", directory names, the retention frontier, the index, the watermark, and **log timestamps** (stdlib `logging` uses libc localtime and would stamp an event under one date while archiving it under another). Installed once at config load; an invalid zone name refuses to start rather than falling back.
- **`source.timezone` is the only declaration of a timezone in the project.** No other file declares one; anything that needs a zone derives it from there. The container's `TZ` is exported from the mounted configuration by the entrypoint, so the hours the schedule fires in and the dates the archive is written under cannot part ways — two independent declarations of one fact are a way for it to differ, and the drift between them shows up as a shifted collection window rather than as an error. The derivation is refused rather than guessed at, always with exit `2`: an unnamed or unreadable configuration, an undeclared `timezone`, a zone the system zone database does not have (the scheduler resolves the name there and falls back to UTC in silence), or an environment `TZ` that contradicts the declaration. This is the one place the container is stricter than the CLI: the CLI supplies `America/Sao_Paulo` for an absent key, because a hand-run has no schedule to misfire, and the shell carries no default of its own because `config.example.toml` ships the value. `doctor` prints the process zone beside `source.timezone` and fails on a contradiction, which is what makes the derivation verifiable without exec'ing into the container.
- Portability: nothing depends on Docker, systemd, cron, or any orchestrator. Running once from a shell with a config file must work. The container of the Deployment section sits beside the package and never below it: it is one scheduler among the possible ones, and the package neither imports it nor is configured by it. No embedded paths, CVM codes, or personal preferences. No credentials — the source is public and unauthenticated.
- Logging goes to the streams and to one file, always both: progress on stdout, `WARNING` and above on stderr, and every line of both to `logs_root/co-docs-watcher.log`, rotated by size. The streams are what a supervisor reads while a run happens; the file is what answers a question asked afterwards, which is when the single warning of a long run has already scrolled away. The file never carries less than the streams, so the two never have to be reconciled, and a log file that cannot be opened is reported on stderr and costs the copy, never the run.
- An isolated failure never kills the batch: a bad company or document is recorded and skipped. Severity ladder: `WARNING` transient and retryable, `ERROR` needs human action, `CRITICAL` the source contract probably changed.

## Tests

- **unit** — pure, no network, no disk beyond `tmp_path`.
- **contract** — pins the source wire format against recorded samples; this is what detects the CVM changing something.
- **integration** — the full flow against a fake server.
- **live** — marked, deselected by default; re-measures the real source. The numbers in this repository are dated measurements, not permanent truths.
- **shell** — the container's entrypoint and its exit-code mapping, run against stubbed binaries on `PATH`. Written in POSIX shell so that the contract the image depends on is testable without building an image to reach it.

An architecture test guarantees that nothing outside `rad/` imports `rad/`.

## Deferred

Per-category retention (the FRE is resubmitted several times per quarter at 8 MB each), alerts for publications by unwatched companies (the global sweep already sees them for free), and server-side category filtering — only after the MVP is validated by manual runs.
