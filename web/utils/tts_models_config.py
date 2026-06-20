"""Helpers for reading third-party TTS model configuration in the Web UI."""


def default_tts_models_config() -> dict:
    """Return default third-party TTS config used when a hot-reloaded singleton is stale."""
    return {
        "default_provider": "minimax",
        "default_model": "speech-2.8-turbo",
        "providers": {
            "minimax": {
                "api_key": "",
                "base_url": "https://api.minimaxi.com",
                "use_proxy": False,
                "default_model": "speech-2.8-turbo",
            }
        },
    }


def get_tts_models_config(config_manager) -> dict:
    """Read TTS model config defensively across Streamlit hot reloads.

    Streamlit can keep an old singleton instance alive after source changes.
    In that case the instance may not have the new helper method or the new
    Pydantic field yet. Falling back to defaults keeps the settings page
    renderable; a process restart will load the full schema.
    """
    if hasattr(config_manager, "get_tts_models_config"):
        return config_manager.get_tts_models_config()

    config = getattr(config_manager, "config", None)
    tts_models = getattr(config, "tts_models", None)
    if tts_models is not None and hasattr(tts_models, "model_dump"):
        return tts_models.model_dump()

    if config is not None and hasattr(config, "to_dict"):
        config_dict = config.to_dict()
        if config_dict.get("tts_models"):
            return config_dict["tts_models"]

    try:
        from pixelle_video.config.loader import load_config_dict

        config_path = str(getattr(config_manager, "config_path", "config.yaml"))
        file_config = load_config_dict(config_path)
        if file_config.get("tts_models"):
            return file_config["tts_models"]
    except Exception:
        pass

    return default_tts_models_config()


def set_tts_models_config(
    config_manager,
    *,
    default_provider: str,
    default_model: str,
    provider: str,
    provider_config: dict,
):
    """Write TTS model config while tolerating stale Streamlit singletons."""
    if hasattr(config_manager, "set_tts_models_defaults") and hasattr(
        config_manager, "set_tts_provider_config"
    ):
        config_manager.set_tts_models_defaults(
            default_provider=default_provider,
            default_model=default_model,
        )
        config_manager.set_tts_provider_config(provider, provider_config)
    else:
        try:
            config_manager.update(
                {
                    "tts_models": {
                        "default_provider": default_provider,
                        "default_model": default_model,
                        "providers": {provider: provider_config},
                    }
                }
            )
        except Exception:
            pass

    try:
        from pixelle_video.config.loader import load_config_dict, save_config_dict

        config_path = str(getattr(config_manager, "config_path", "config.yaml"))
        file_config = load_config_dict(config_path)
        file_config.setdefault("tts_models", {})
        file_config["tts_models"]["default_provider"] = default_provider
        file_config["tts_models"]["default_model"] = default_model
        file_config["tts_models"].setdefault("providers", {})
        file_config["tts_models"]["providers"][provider] = provider_config
        save_config_dict(file_config, config_path)
    except Exception:
        pass


def set_default_tts_voice(
    config_manager,
    *,
    provider: str,
    voice_id: str,
    voice_type: str | None = None,
):
    """Persist the user's default provider voice without caching the full voice list."""
    try:
        config_manager.update(
            {
                "tts_models": {
                    "providers": {
                        provider: {
                            "default_voice_id": voice_id,
                            "default_voice_type": voice_type,
                        }
                    }
                }
            }
        )
    except Exception:
        pass

    try:
        from pixelle_video.config.loader import load_config_dict, save_config_dict

        config_path = str(getattr(config_manager, "config_path", "config.yaml"))
        file_config = load_config_dict(config_path)
        file_config.setdefault("tts_models", {})
        file_config["tts_models"].setdefault("providers", {})
        provider_config = file_config["tts_models"]["providers"].setdefault(provider, {})
        provider_config["default_voice_id"] = voice_id
        provider_config["default_voice_type"] = voice_type
        save_config_dict(file_config, config_path)
    except Exception:
        pass
