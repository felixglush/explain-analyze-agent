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

    if schema_file is not None and not isinstance(schema_file, str):
        raise ConfigError("'schema_file' must be a string")
    if setup_command is not None and not isinstance(setup_command, str):
        raise ConfigError("'setup_command' must be a string")

    if file_patterns is None:
        raise ConfigError("Config must include 'file_patterns'")
    if not isinstance(file_patterns, list):
        raise ConfigError("'file_patterns' must be a list of strings")
    if len(file_patterns) == 0:
        raise ConfigError("'file_patterns' must not be empty")

    if schema_file is not None and schema_file == "":
        raise ConfigError("'schema_file' must not be an empty string")

    has_schema = bool(schema_file)
    has_command = bool(setup_command)
    if has_schema and has_command:
        raise ConfigError(
            "Config must set exactly one of 'schema_file' or 'setup_command', not both"
        )
    if not has_schema and not has_command:
        raise ConfigError(
            "Config must set exactly one of 'schema_file' or 'setup_command'"
        )

    return Config(
        schema_file=schema_file,
        setup_command=setup_command,
        file_patterns=file_patterns,
    )
