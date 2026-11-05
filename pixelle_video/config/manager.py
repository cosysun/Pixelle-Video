"""Configuration imports legacy YAML once, then persists in the platform database."""

from pathlib import Path

from .loader import load_config_dict, save_config_dict
from .schema import PixelleVideoConfig


class ConfigManager:
    def __init__(self, config_path="config.yaml"):
        self.config_path = Path(config_path)
        self.database = None
        self.config = PixelleVideoConfig(**load_config_dict(str(self.config_path)))

    def bind_database(self, database):
        self.database = database
        stored = database.get_setting("configuration")
        if stored is not None:
            self.config = PixelleVideoConfig(**stored)
        else:
            database.set_setting("configuration", self.config.to_dict())

    def save(self):
        if self.database:
            self.database.set_setting("configuration", self.config.to_dict())
        else:
            save_config_dict(self.config.to_dict(), str(self.config_path))

    def update(self, updates):
        def merge(base, changed):
            for key, value in changed.items():
                if isinstance(value, dict) and isinstance(base.get(key), dict):
                    merge(base[key], value)
                else:
                    base[key] = value
            return base

        self.config = PixelleVideoConfig(**merge(self.config.to_dict(), updates))

    def reload(self):
        self.config = PixelleVideoConfig(
            **(
                self.database.get_setting("configuration", {})
                if self.database
                else load_config_dict(str(self.config_path))
            )
        )

    def validate(self):
        return self.config.validate_required()

    def get(self, key, default=None):
        return self.config.to_dict().get(key, default)

    def get_llm_config(self):
        return self.config.llm.model_dump()

    def get_api_providers_config(self):
        return self.config.api_providers.model_dump()

    def get_tts_models_config(self):
        return self.config.tts_models.model_dump()
