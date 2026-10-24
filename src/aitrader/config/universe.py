"""The fixed research universe (document 11 §2)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from aitrader.config.errors import ConfigError
from aitrader.domain.market import Symbol


class Universe(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    universe: str = Field(min_length=1)
    version: int = Field(ge=1)
    symbols: tuple[Symbol, ...] = Field(min_length=1)

    @field_validator("symbols")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("symbols must be unique")
        return value


def load_universe(path: Path) -> Universe:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return Universe.model_validate(data)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
