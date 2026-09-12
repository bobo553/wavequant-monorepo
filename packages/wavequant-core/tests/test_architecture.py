"""Architecture contracts for the incremental WaveQuant Core layering."""

import ast
from importlib import import_module
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "wavequant"
PROHIBITED_DOMAIN_PREFIXES = (
    "wavequant.application",
    "wavequant.infrastructure",
    "wavequant.interfaces",
)


def test_domain_does_not_depend_on_outer_layers() -> None:
    """Pure strategy rules must not acquire I/O or presentation dependencies."""

    violations: list[str] = []
    domain_root = PACKAGE_ROOT / "domain"
    for module_path in sorted(domain_root.rglob("*.py")):
        # A direct domain module may use one leading dot; a module one feature
        # directory deeper may use two. More dots would escape ``domain``.
        directory_depth = len(module_path.relative_to(domain_root).parts) - 1
        maximum_internal_level = directory_depth + 1
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_module = node.module or ""
                # ``from ..application`` is a relative escape from the domain
                # package and is forbidden even though its AST module name does
                # not include the top-level ``wavequant`` prefix.
                if node.level > maximum_internal_level or imported_module.startswith(PROHIBITED_DOMAIN_PREFIXES):
                    relative_name = module_path.relative_to(PACKAGE_ROOT)
                    violations.append(f"{relative_name}:{node.lineno} -> {imported_module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(PROHIBITED_DOMAIN_PREFIXES):
                        relative_name = module_path.relative_to(PACKAGE_ROOT)
                        violations.append(f"{relative_name}:{node.lineno} -> {alias.name}")

    assert violations == []


@pytest.mark.parametrize(
    "module_name",
    [
        "wavequant.domain.models.model",
        "wavequant.domain.market_structure.trend_structure",
        "wavequant.domain.market_state.market_regime",
        "wavequant.domain.strategies.integrated_strategy",
        "wavequant.application.analytics.backtest",
        "wavequant.application.trading.order_service",
        "wavequant.application.governance.operations",
        "wavequant.infrastructure.persistence.artifact_cache",
        "wavequant.infrastructure.market_data.data",
        "wavequant.infrastructure.filesystem.project_paths",
        "wavequant.interfaces.charts.chart_geometry",
        "wavequant.interfaces.research_tools.tdx_backtest",
        "wavequant.interfaces.screening.buy_scanner",
        "wavequant.interfaces.cli",
    ],
)
def test_canonical_feature_modules_are_importable(module_name: str) -> None:
    """Every advertised feature package must expose an importable implementation."""

    assert import_module(module_name).__name__ == module_name


def test_layer_roots_do_not_contain_compatibility_modules() -> None:
    """Removed flat imports must not silently return as duplicate implementations."""

    expected_files = {
        PACKAGE_ROOT: {"__init__.py"},
        PACKAGE_ROOT / "domain": {"__init__.py"},
        PACKAGE_ROOT / "application": {"__init__.py"},
        PACKAGE_ROOT / "infrastructure": {"__init__.py"},
        PACKAGE_ROOT / "interfaces": {"__init__.py", "cli.py"},
    }

    for directory, expected in expected_files.items():
        actual = {path.name for path in directory.glob("*.py")}
        assert actual == expected


def test_project_root_survives_infrastructure_relocation() -> None:
    """Moving the path adapter must not move its resolved workspace root."""

    paths = import_module("wavequant.infrastructure.filesystem.project_paths")

    assert paths.PROJECT_ROOT == PACKAGE_ROOT.parents[1]
    assert (paths.PROJECT_ROOT / "pyproject.toml").is_file()


def test_visualization_fingerprints_the_canonical_strategy_source() -> None:
    """Moving chart adapters must not redirect the strategy audit hash."""

    visualization = import_module("wavequant.interfaces.charts.visualization")

    assert visualization._strategy_source_path() == (
        PACKAGE_ROOT / "domain" / "strategies" / "integrated_strategy.py"
    )
