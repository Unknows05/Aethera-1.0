"""
Config Loader — Single source of truth for all configuration.
Replaces scattered config dict reads across the codebase.
"""
from functools import lru_cache
import yaml


@lru_cache(maxsize=1)
def get_config(path='config.yaml'):
    with open(path) as f:
        return yaml.safe_load(f)


def invalidate_config_cache():
    get_config.cache_clear()
