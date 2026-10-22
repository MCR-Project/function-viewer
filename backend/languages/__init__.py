"""The language registry: every supported language plugs in here.

To add a language: implement a LanguagePlugin in a new module (see
python.py / rust.py) and add an instance to LANGUAGES below. Nothing
outside this package needs to change - analyzer.py dispatches off file
extension (and, for an extension a plugin only claims conditionally, such as
C++ taking `.h`, off that plugin's `claims()`; see base.py).
"""

from __future__ import annotations

from .base import SKIP_DIRS, LanguagePlugin
from .c import CLanguage
from .cpp import CppLanguage
from .python import PythonLanguage
from .rust import RustLanguage
from .typescript import TypeScriptLanguage

LANGUAGES: list[LanguagePlugin] = [PythonLanguage(), RustLanguage(), TypeScriptLanguage(), CLanguage(), CppLanguage()]

# Each extension's unconditional owner; a plugin's `conditional_extensions` are not in here (see analyzer.py).
EXTENSION_TO_LANGUAGE: dict[str, LanguagePlugin] = {ext: lang for lang in LANGUAGES for ext in lang.extensions}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    ext for lang in LANGUAGES for ext in lang.extensions | lang.conditional_extensions
)

__all__ = ["SKIP_DIRS", "LanguagePlugin", "LANGUAGES", "EXTENSION_TO_LANGUAGE", "SUPPORTED_EXTENSIONS"]
