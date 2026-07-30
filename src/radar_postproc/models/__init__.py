"""Model plugin registry, mirroring the datasets registry.

Adding a model = one new file in this package + a @register_model decorator +
one entry under train.models in config/model.yaml.
"""

_REGISTRY: dict[str, type] = {}


def register_model(cls):
    """Class decorator: register a model plugin under its ``name`` attribute."""
    name = getattr(cls, "name", None)
    if not name:
        raise ValueError(f"{cls.__name__} must define a class-level `name`")
    if name in _REGISTRY:
        raise ValueError(f"Duplicate model plugin name: {name}")
    _REGISTRY[name] = cls
    return cls


def get_model(name: str, **kwargs):
    """Instantiate a registered model by name with optional kwargs."""
    _load_all()
    if name not in _REGISTRY:
        raise KeyError(f"Unknown model {name!r}. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name](**kwargs)


def list_models() -> list[str]:
    _load_all()
    return sorted(_REGISTRY)


def _load_all():
    """Import model modules so their @register_model decorators run."""
    from . import atten_refl, linear  # noqa: F401
