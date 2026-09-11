"""Stable paths for resources owned by the WaveQuant workspace."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_path(*parts: str) -> Path:
    """Resolve a path inside the workspace without depending on process cwd."""
    return PROJECT_ROOT.joinpath(*parts)
