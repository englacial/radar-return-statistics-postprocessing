"""Dataset plugin registry. Configs reference plugins by name.

Adding a dataset = one new file in this package + a @register decorator + one
entry in the per-store config.
"""

from .base import ExternalDataset  # noqa: F401

_REGISTRY: dict[str, type] = {}


def register(cls):
    """Class decorator: register a dataset plugin under its ``name`` attribute."""
    name = getattr(cls, "name", None)
    if not name:
        raise ValueError(f"{cls.__name__} must define a class-level `name`")
    if name in _REGISTRY:
        raise ValueError(f"Duplicate dataset plugin name: {name}")
    _REGISTRY[name] = cls
    return cls


def get_dataset(name: str, **kwargs):
    """Instantiate a registered plugin by name with optional kwargs."""
    _load_all()
    if name not in _REGISTRY:
        raise KeyError(f"Unknown dataset {name!r}. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name](**kwargs)


def list_datasets() -> list[str]:
    _load_all()
    return sorted(_REGISTRY)


def _load_all():
    """Import plugin modules so their @register decorators run."""
    from . import bedmachine, era5, ghf, itslive, measures_vel  # noqa: F401
