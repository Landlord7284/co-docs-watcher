# co-docs-watcher

[![ci](https://github.com/Landlord7284/co-docs-watcher/actions/workflows/ci.yml/badge.svg)](https://github.com/Landlord7284/co-docs-watcher/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A reading queue for documents published on [RAD/CVM](https://www.rad.cvm.gov.br/ENETWeb/),
the Brazilian securities regulator's filing system. It watches a list of companies, downloads
what they deliver, files it by delivery date, and writes a daily index you can actually read.

```
documents_root/
├── _inbox/2026-08-24.md       # the day's reading queue
├── 2026-08-24/
│   ├── PETR/Fato-Relevante_160310_V01.pdf
│   └── VALE/Aviso-aos-Acionistas_160295_V01.pdf
└── .tmp/
```

## What it does

- **Watch list by ticker, CNPJ, CVM code or legal name**, resolved against the CVM's own FCA
  registry; company folders are named by ticker root (`PETR4 → PETR/`).
- **One listing request per day of the window**, market-wide, filtered locally — a watch list
  of any size costs the same. The source is fragile, so requests are spaced and capped.
- **Supersessions and cancellations come from the source**, never guessed: a document that
  goes `Inativo` or `Cancelado` loses its file, and the day's index says so.
- **A sliding retention window** (7 days by default) — this is a reading queue, not an archive
  for posterity.
- **PDFs and structured packages both**: ZIP containers are validated and unwrapped, and the
  file extension is decided by content, never by what the source claims.

## Install

```bash
python -m venv .venv && .venv/bin/pip install -e .
cp config.example.toml config.toml
```

## Use

```bash
co-docs-watcher doctor            # config, roots, timezone, windows, source
co-docs-watcher add --ticker PETR
co-docs-watcher run               # one full pass over the discovery window
co-docs-watcher run --monitor     # the frequent profile: a narrower window
co-docs-watcher status            # what is on disk, and what is still owed
```

`run` is one-shot: it does a complete pass and exits. Schedule it with whatever you already
have — cron, launchd, a shell loop — or use the container below, which carries its own
scheduler. The usual pairing is a frequent `run --monitor` plus one daily `run`.

Roots written relative in `config.toml` resolve against the configuration file's own
directory, so a checkout with a `config.toml` beside it archives into its own `var/` whether
you run it by hand or from cron.

## Docker

```bash
cp config.example.toml config.toml   # windows, retention, modes
cp .env.example .env                 # image tag, schedules, identity, host paths
docker compose up -d
```

The image is published to `ghcr.io/landlord7284/co-docs-watcher` — `latest` from `main`, and
`X.Y.Z` plus `X.Y` from every `v*` tag. `IMAGE_TAG` in `.env` chooses which, and updating is
`docker compose pull && docker compose up -d`.

Inside, [supercronic](https://github.com/aptible/supercronic) runs a crontab rendered from
`.env` and fires the same one-shot a shell would. `config.toml` is mounted, not duplicated.

## Documentation

| File | Contents |
|---|---|
| [USAGE.md](USAGE.md) | every subcommand, every configuration key, the deployment in full |
| [CLAUDE.md](CLAUDE.md) | scope, conventions, architecture, invariants |
| [docs/fonte-rad.md](docs/fonte-rad.md) | the observed contract of the RAD/ENETWeb source (pt-BR) |

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                  # unit, contract, integration
.venv/bin/pytest -m live          # re-measures the real source; needs network
.venv/bin/ruff check src tests
```

The CVM publishes no API contract. Every number this repository states about the source
carries the date it was measured, and the `live` tests are how they are re-measured.

## License

MIT — see [LICENSE](LICENSE).
