#!/usr/bin/env python3
"""Print the timezone declared in a configuration file, or say why it cannot be read.

The entrypoint exports ``TZ`` from this, so that the zone the scheduler fires in and the zone
the archive is written in are one declaration rather than two that can drift. It parses the
file rather than importing the package: the shell layer is tested without an installed
package, and reaching into ``co_docs_watcher.config`` would make that impossible.

There is no default here. ``America/Sao_Paulo`` is a value ``config.example.toml`` ships, not
a rule this script re-implements, so an undeclared timezone is refused rather than guessed.
"""

from __future__ import annotations

import sys
import tomllib


def declared_timezone(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
    except OSError as exc:
        raise SystemExit(f"{path}: cannot be read: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path}: invalid TOML: {exc}") from exc

    source = raw.get("source")
    zone = source.get("timezone") if isinstance(source, dict) else None
    if not isinstance(zone, str) or not zone.strip():
        raise SystemExit(
            f"{path}: [source] declares no timezone, and the scheduler needs one: "
            'add `timezone = "America/Sao_Paulo"` under [source]'
        )
    return zone.strip()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: config-timezone.py CONFIG")
    print(declared_timezone(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
