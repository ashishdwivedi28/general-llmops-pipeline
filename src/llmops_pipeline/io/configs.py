"""Parse, merge, and convert YAML/JSON config objects via OmegaConf."""

import typing as T

import omegaconf as oc

Config = oc.ListConfig | oc.DictConfig


def parse_file(path: str) -> Config:
    """Load a YAML/JSON config file."""
    return oc.OmegaConf.load(path)


def parse_string(string: str) -> Config:
    """Parse an inline config string."""
    return oc.OmegaConf.create(string)


def merge_configs(configs: T.Sequence[Config]) -> Config:
    """Merge multiple configs (later configs override earlier)."""
    return oc.OmegaConf.merge(*configs)


def to_object(config: Config, resolve: bool = True) -> object:
    """Convert to plain Python dict."""
    return oc.OmegaConf.to_container(config, resolve=resolve)
