import importlib

import prompt2midi


def test_version():
    assert prompt2midi.__version__ == "0.0.0"


def test_subpackages_importable():
    for name in ("providers", "compiler", "validate", "api"):
        importlib.import_module(f"prompt2midi.{name}")
