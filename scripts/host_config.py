"""Generate and validate private single-host configuration without printing secrets.

Uses only the Python standard library. Never provisions or starts containers.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / "deploy" / ".env"
CORE_SECRETS = (
    "POSTGRES_PASSWORD",
    "LEDGER_SERVICE_TOKEN",
    "PORTAL_PASSWORD",
    "PORTAL_SESSION_SECRET",
    "WEBHOOK_SECRET",
)
OPTIONAL = ("OPENAI_API_KEY", "RAG_SERVICE_KEY", "RAG_API_KEYS", "RAG_ADMIN_KEY")
FIELDS = (
    "DOMAIN",
    "PORTAL_MERCHANT_ID",
    *CORE_SECRETS,
    "API_KEYS",
    "WEBHOOK_CLEARING_ACCOUNTS",
    *OPTIONAL,
)
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,512}\Z")
MERCHANT = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")


class ConfigError(ValueError):
    """Messages contain field names, never supplied values."""


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key not in FIELDS or key in values:
            raise ConfigError(
                "Configuration contains an unknown, duplicate or malformed field"
            )
        # Keep a deliberately small dotenv subset shared with Compose.
        # JSON webhook maps are the sole exception for double quotes.
        if any(char in value for char in ("$", "'", '"', "\\", "\x00")) and (
            key != "WEBHOOK_CLEARING_ACCOUNTS"
            or any(char in value for char in ("$", "'", "\\", "\x00"))
        ):
            raise ConfigError(f"{key} contains unsupported dotenv characters")
        if value != value.strip() or "#" in value:
            raise ConfigError(f"{key} contains whitespace or dotenv comment syntax")
        values[key] = value
    return values


def domain_ok(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return False
    except ValueError:
        pass
    labels = value.split(".")
    return (
        len(value) <= 253
        and len(labels) >= 2
        and labels[-1].isalpha()
        and len(labels[-1]) >= 2
        and all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", x) for x in labels
        )
        and not value.endswith(
            (".localhost", ".local", ".invalid", ".test", ".example")
        )
        and value not in ("example.com", "example.net", "example.org")
    )


def strong_secret(name: str, value: str) -> None:
    if (
        not TOKEN.fullmatch(value)
        or len(set(value)) < 12
        or any(
            marker in value.lower()
            for marker in (
                "change-me",
                "changeme",
                "replace-me",
                "replace_me",
                "dev_only",
                "placeholder",
                "example-secret",
            )
        )
    ):
        raise ConfigError(
            f"{name} must be a unique random URL-safe secret of at least 32 characters"
        )


def parse_keys(name: str, value: str) -> dict[str, str]:
    mapping = {}
    for pair in value.split(","):
        key, sep, merchant = pair.partition(":")
        if not sep or not MERCHANT.fullmatch(merchant):
            raise ConfigError(f"{name} must contain key:merchant pairs")
        strong_secret(name, key)
        if key in mapping:
            raise ConfigError(f"{name} contains a duplicate key")
        mapping[key] = merchant
    return mapping


def validate(values: dict[str, str], *, rag: bool = False) -> None:
    if set(values) - set(FIELDS):
        raise ConfigError("Configuration contains unknown fields")
    if not domain_ok(values.get("DOMAIN", "")):
        raise ConfigError(
            "DOMAIN must be a real lowercase public DNS hostname without scheme or port"
        )
    merchant = values.get("PORTAL_MERCHANT_ID", "")
    if not MERCHANT.fullmatch(merchant):
        raise ConfigError(
            "PORTAL_MERCHANT_ID must use letters, numbers, underscores or hyphens"
        )
    unique = []
    for name in CORE_SECRETS:
        value = values.get(name, "")
        strong_secret(name, value)
        unique.append(value)
    keys = parse_keys("API_KEYS", values.get("API_KEYS", ""))
    if merchant not in keys.values():
        raise ConfigError("API_KEYS must include PORTAL_MERCHANT_ID")
    unique.extend(keys)
    try:
        clearing = json.loads(values.get("WEBHOOK_CLEARING_ACCOUNTS", "{}"))
        if not isinstance(clearing, dict):
            raise TypeError
        for owner, account in clearing.items():
            if owner not in keys.values() or not isinstance(account, str):
                raise ValueError
            UUID(account)
    except (ValueError, TypeError):
        raise ConfigError(
            "WEBHOOK_CLEARING_ACCOUNTS must map configured merchants to account UUIDs"
        ) from None
    rag_configured = any(values.get(name) for name in OPTIONAL)
    if rag or rag_configured:
        if not all(values.get(name) for name in OPTIONAL):
            raise ConfigError("RAG needs all four optional credential fields")
        strong_secret("RAG_SERVICE_KEY", values["RAG_SERVICE_KEY"])
        strong_secret("RAG_ADMIN_KEY", values["RAG_ADMIN_KEY"])
        rag_keys = parse_keys("RAG_API_KEYS", values["RAG_API_KEYS"])
        if rag_keys.get(values["RAG_SERVICE_KEY"]) != merchant:
            raise ConfigError(
                "RAG_SERVICE_KEY must map to PORTAL_MERCHANT_ID in RAG_API_KEYS"
            )
        provider = values["OPENAI_API_KEY"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", provider):
            raise ConfigError("OPENAI_API_KEY must be a provider-issued key")
        unique.extend((*rag_keys, values["RAG_ADMIN_KEY"], provider))
    if len(set(unique)) != len(unique):
        raise ConfigError("Credentials must not be reused across roles or merchants")


def generate(path: Path, domain: str, merchant: str) -> None:
    values = {"DOMAIN": domain, "PORTAL_MERCHANT_ID": merchant}
    values.update({name: secrets.token_urlsafe(48) for name in CORE_SECRETS})
    values["API_KEYS"] = f"{secrets.token_urlsafe(48)}:{merchant}"
    values["WEBHOOK_CLEARING_ACCOUNTS"] = "{}"
    values.update({name: "" for name in OPTIONAL})
    validate(values)
    content = "# Private deployment credentials. Never commit or print this file.\n"
    content += "\n".join(f"{name}={values[name]}" for name in FIELDS) + "\n"
    # Exclusive creation refuses existing files and symlinks. Mode is private on POSIX.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def check_environment(values: dict[str, str]) -> None:
    for name in FIELDS:
        if name in os.environ and os.environ[name] != values.get(name, ""):
            raise ConfigError(f"Unset the conflicting shell override for {name}")
    if os.environ.get("COMPOSE_PROFILES", "") not in ("", "rag"):
        raise ConfigError("Unset unsupported COMPOSE_PROFILES")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("generate", "check"))
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--domain")
    parser.add_argument("--merchant")
    parser.add_argument(
        "--rag", action="store_true", help="Require complete RAG credentials"
    )
    parser.add_argument(
        "--compose-check",
        action="store_true",
        help="Run Docker Compose config --quiet (no engine needed)",
    )
    args = parser.parse_args()
    try:
        if args.action == "generate":
            if not args.domain or not args.merchant or args.rag or args.compose_check:
                raise ConfigError(
                    "Generation requires --domain and --merchant; optional services are configured separately"
                )
            generate(args.env_file, args.domain, args.merchant)
            print("Created private configuration without displaying credentials.")
            return 0
        values = read_env(args.env_file)
        rag = args.rag or os.environ.get("COMPOSE_PROFILES") == "rag"
        validate(values, rag=rag)
        check_environment(values)
        if os.name == "posix" and args.env_file.stat().st_mode & 0o077:
            raise ConfigError("Private configuration must have mode 0600 (chmod 600)")
        if args.compose_check:
            command = [
                "docker",
                "compose",
                "--project-name",
                "finacle-host",
                "--env-file",
                str(args.env_file.resolve()),
                "-f",
                str(ROOT / "deploy" / "compose.yml"),
            ]
            if rag:
                command.extend(("--profile", "rag"))
            command.extend(("config", "--quiet"))
            # Capture output: even a syntax failure must not echo interpolated credentials.
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=60, check=False
            )
            if result.returncode:
                raise ConfigError(
                    "Compose configuration check failed; review repository configuration without printing resolved secrets"
                )
        print(
            "Configuration checks passed. No containers were started; provider credentials were not verified."
        )
        return 0
    except (ConfigError, OSError, subprocess.SubprocessError):
        # OSError can include a caller-controlled filename; only safe diagnostic categories.
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, ConfigError) else type(error).__name__
        print(f"Configuration failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
