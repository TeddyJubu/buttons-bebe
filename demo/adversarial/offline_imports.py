"""Import production modules without reading the repository root ``.env``.

Both config modules call ``load_dotenv()`` at import, and ``ProcessorSettings``
also names the root ``.env`` as its Pydantic ``env_file``.  Every adversarial
suite enters this context before its first production import.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import patch

import dotenv


@contextmanager
def without_root_dotenv():
    with patch.object(dotenv, "load_dotenv", lambda *_args, **_kwargs: False):
        yield
    processor_config = sys.modules.get("config")
    if processor_config is not None and hasattr(processor_config, "ProcessorSettings"):
        processor_config.ProcessorSettings.model_config["env_file"] = None
