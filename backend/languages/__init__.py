"""The language registry: every supported language plugs in here.

To add a language: implement a LanguagePlugin in a new module (see
python.py / rust.py) and add an instance to LANGUAGES below. Nothing
outside this package needs to change - analyzer.py dispatches purely off
file extension.
"""

from __future__ import annotations

from .base import SKIP_DIRS, LanguagePlugin
from .python import PythonLanguage
from .rust import RustLanguage

LANGUAGES: list[LanguagePlugin] = [PythonLanguage(), RustLanguage()]

EXTENSION_TO_LANGUAGE: dict[str, LanguagePlugin] = {ext: lang for lang in LANGUAGES for ext in lang.extensions}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(EXTENSION_TO_LANGUAGE)

__all__ = ["SKIP_DIRS", "LanguagePlugin", "LANGUAGES", "EXTENSION_TO_LANGUAGE", "SUPPORTED_EXTENSIONS"]
