#!/usr/bin/env python3
"""Assert the Dockerfile ENV defaults match the mimir-config.yml placeholders.

The sibling containers-log-aggregator repo carries exactly this bug: the
Dockerfile declares LOKI_INGESTION_BURST_SIZE_MB=50 while loki-config.yml
defaults it to 75, and two more knobs appear in the YAML with no ENV at all.
For Mimir the equivalent drift is worse than cosmetic, so it is a test.

Also rejects the ${VAR:-default} form. Mimir splits its placeholder on the
FIRST colon only, so a bash-style ":-" leaves the hyphen inside the value:
${MIMIR_RETENTION_PERIOD:-336h} silently yields "-336h", a valid negative
duration. Loki's expander accepts ":-"; Mimir's does not.

Usage: python3 tests/check_env_defaults.py   (exit 0 = clean)
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "files" / "mimir-config.yml"
DOCKERFILE = ROOT / "docker" / "Dockerfile"

# ${VAR}  or  ${VAR:default}
PLACEHOLDER = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::([^}]*))?\}")
ENV_LINE = re.compile(r"^ENV\s+([A-Z_][A-Z0-9_]*)=(.*)$")


def strip_comments(text: str) -> str:
    """Drop full-line comments so the header's syntax examples are not scanned."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def main() -> int:
    config = strip_comments(CONFIG.read_text())
    dockerfile = DOCKERFILE.read_text()
    errors = []

    # 1. No Loki-style ":-" defaults.
    for bad in re.finditer(r"\$\{([A-Z_][A-Z0-9_]*):-", config):
        errors.append(
            f"{CONFIG.name}: ${{{bad.group(1)}:-...}} uses Loki's ':-' syntax; "
            f"Mimir needs a single colon (${{{bad.group(1)}:...}})"
        )

    # 2. Collect placeholders from the config, and ENVs from the Dockerfile.
    placeholders = {}
    for m in PLACEHOLDER.finditer(config):
        name, default = m.group(1), m.group(2)
        # A var may appear more than once; every occurrence must agree.
        if name in placeholders and placeholders[name] != default:
            errors.append(
                f"{CONFIG.name}: {name} has conflicting defaults "
                f"{placeholders[name]!r} and {default!r}"
            )
        placeholders[name] = default

    envs = {}
    for line in dockerfile.splitlines():
        m = ENV_LINE.match(line.strip())
        if m:
            envs[m.group(1)] = m.group(2).strip().strip('"')

    # 3. Cross-check both directions.
    for name, default in sorted(placeholders.items()):
        expected = "" if default is None else default
        if name not in envs:
            # A bare ${VAR} with no default is inert when unset, so declaring
            # it is optional. Credentials are deliberately left undeclared so
            # the image ships no credential-shaped ENV at all.
            if default is None:
                continue
            errors.append(
                f"{DOCKERFILE.name}: missing 'ENV {name}={expected}' for the "
                f"{name} placeholder in {CONFIG.name}"
            )
        elif envs[name] != expected:
            errors.append(
                f"drift: {name} is {expected!r} in {CONFIG.name} but "
                f"{envs[name]!r} in {DOCKERFILE.name}"
            )

    for name in sorted(envs):
        if name.startswith("MIMIR_") and name not in placeholders:
            errors.append(
                f"{DOCKERFILE.name}: ENV {name} is declared but never used in "
                f"{CONFIG.name}"
            )

    if errors:
        print(f"FAIL ({len(errors)} problem(s)):")
        for e in errors:
            print("  -", e)
        return 1

    print(f"OK: {len(placeholders)} placeholders, all matched by Dockerfile ENV defaults")
    return 0


if __name__ == "__main__":
    sys.exit(main())
