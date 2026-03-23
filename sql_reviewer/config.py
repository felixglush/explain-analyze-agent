from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml


class ConfigError(Exception):
    pass


@dataclass
class Config:
    schema_file: str | None
    setup_command: str | None
    file_patterns: list[str]


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    schema_file = raw.get("schema_file")
    setup_command = raw.get("setup_command")
    file_patterns = raw.get("file_patterns")

    if not file_patterns:
        raise ConfigError("Config must include 'file_patterns'")

    has_schema = bool(schema_file)
    has_command = bool(setup_command)
    if has_schema == has_command:  # both True or both False
        raise ConfigError(
            "Config must set exactly one of 'schema_file' or 'setup_command'"
        )

    return Config(
        schema_file=schema_file,
        setup_command=setup_command,
        file_patterns=file_patterns,
    )
