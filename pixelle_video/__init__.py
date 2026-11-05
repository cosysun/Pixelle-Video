"""Pixelle creation platform."""
__version__ = "0.1.15"


def __getattr__(name):
    if name in {"PixelleVideoCore", "pixelle_video"}:
        from .service import PixelleVideoCore, pixelle_video
        return {"PixelleVideoCore": PixelleVideoCore, "pixelle_video": pixelle_video}[name]
    if name == "config_manager":
        from .config import config_manager
        return config_manager
    raise AttributeError(name)
