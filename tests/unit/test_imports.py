"""Every module in the package imports cleanly.

Large parts of the CLI and processing code are not exercised by behavioural
tests, so a broken import after moving code between modules would otherwise go
unnoticed. GDAL and numpy are optional at import time (collection-only hosts
have neither), so this must pass with and without them.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import planetarble


def _all_modules() -> list[str]:
    names = [planetarble.__name__]
    for info in pkgutil.walk_packages(planetarble.__path__, prefix=f"{planetarble.__name__}."):
        names.append(info.name)
    return sorted(names)


MODULES = _all_modules()

# Modules that import an optional system dependency at module level. They are
# only imported lazily by the rest of the package, so hosts without it still
# work; here they are skipped instead of failing when it is missing.
REQUIRES = {
    "planetarble.processing.composite": "numpy",
}


def test_module_discovery_finds_the_package() -> None:
    # Guard against walk_packages silently finding nothing.
    assert "planetarble.cli.main" in MODULES
    assert "planetarble.processing.manager" in MODULES
    assert len(MODULES) >= 40


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    if name in REQUIRES:
        pytest.importorskip(REQUIRES[name])
    importlib.import_module(name)


def test_requires_lists_only_real_modules() -> None:
    assert set(REQUIRES) <= set(MODULES)
