from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_real_kb_lib():
    """Load kb_lib from source with its heavy deps stubbed.

    Sibling tests install a fake ``kb_lib`` in sys.modules, so load the file
    under its own name instead of importing it.
    """
    stubs = {
        "frontmatter": types.ModuleType("frontmatter"),
        "lancedb": types.ModuleType("lancedb"),
        "lancedb.pydantic": types.SimpleNamespace(LanceModel=object, Vector=lambda _dim: list),
        "fastembed": types.SimpleNamespace(TextEmbedding=None),
    }
    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location("kb_lib_under_test", SCRIPTS / "kb_lib.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class FakeVector(list):
    def tolist(self) -> list:
        return list(self)


class RecordingModel:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def embed(self, texts, batch_size=256, **_kwargs):
        self.batch_sizes.append(batch_size)
        return (FakeVector([float(len(text))]) for text in texts)


class TestEmbedPassages(unittest.TestCase):
    def test_passages_are_embedded_in_small_batches(self) -> None:
        # fastembed's default batch of 256 OOM-killed kb-sync at MemoryMax=2G.
        kb_lib = load_real_kb_lib()
        model = RecordingModel()
        kb_lib._model = model

        vectors = kb_lib.embed_passages(["a" * n for n in range(1, 101)])

        self.assertEqual(vectors, [[float(n)] for n in range(1, 101)])
        self.assertEqual(len(model.batch_sizes), 1)
        self.assertLessEqual(model.batch_sizes[0], 32)


if __name__ == "__main__":
    unittest.main()
