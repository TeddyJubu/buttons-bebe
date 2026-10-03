"""Import production modules without reading the repository root ``.env``.

Both config modules call ``load_dotenv()`` at import, and ``ProcessorSettings``
also names the root ``.env`` as its Pydantic ``env_file``.  A settings object
built during the first import would read that file through Pydantic's own
dotenv source, which never calls ``dotenv.load_dotenv``.  Every adversarial
suite enters this context before its first production import; both readers are
disabled only for its duration, and processor settings built afterwards have
``env_file=None``.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import patch

import dotenv
from pydantic_settings import DotEnvSettingsSource


@contextmanager
def without_root_dotenv():
    with (
        patch.object(dotenv, "load_dotenv", lambda *_args, **_kwargs: False),
        patch.object(DotEnvSettingsSource, "_read_env_files", lambda _self: {}),
    ):
        yield
    processor_config = sys.modules.get("config")
    if processor_config is not None and hasattr(processor_config, "ProcessorSettings"):
        processor_config.ProcessorSettings.model_config["env_file"] = None
