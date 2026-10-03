from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import patch

import dotenv
from pydantic_settings import DotEnvSettingsSource


@contextmanager
def without_root_dotenv():
    try:
        with (
            patch.object(dotenv, "load_dotenv", lambda *_args, **_kwargs: False),
            patch.object(DotEnvSettingsSource, "_read_env_files", lambda _self: {}),
        ):
            yield
    finally:
        processor_config = sys.modules.get("config")
        if processor_config is not None and hasattr(processor_config, "ProcessorSettings"):
            processor_config.ProcessorSettings.model_config["env_file"] = None
