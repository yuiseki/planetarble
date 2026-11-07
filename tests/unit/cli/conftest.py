"""Shared fixtures for CLI tests.

The CLI is split across several modules under planetarble.cli, and each
handler module imports its own collaborators (load_config, the managers, the
validation helpers). `patch_cli` replaces a name in every planetarble.cli
module that binds it, so tests do not depend on which module a handler lives
in.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import Any, Callable

import pytest

import planetarble.cli


def cli_modules() -> list[ModuleType]:
    modules = [planetarble.cli]
    for info in pkgutil.iter_modules(planetarble.cli.__path__, prefix="planetarble.cli."):
        modules.append(importlib.import_module(info.name))
    return modules


def cli_attr(name: str) -> Any:
    """The current object bound to `name` in any planetarble.cli module."""
    for module in cli_modules():
        if module is not planetarble.cli and hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(f"no planetarble.cli module binds {name!r}")


@pytest.fixture
def patch_cli(monkeypatch: pytest.MonkeyPatch) -> Callable[[str, Any], None]:
    def _patch(name: str, value: Any) -> None:
        patched = 0
        for module in cli_modules():
            # skip the package itself: it re-exports main() only
            if module is not planetarble.cli and hasattr(module, name):
                monkeypatch.setattr(module, name, value)
                patched += 1
        if not patched:
            raise AttributeError(f"no planetarble.cli module binds {name!r}")

    return _patch
