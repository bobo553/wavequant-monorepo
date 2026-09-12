"""Resolve stable filesystem paths for resources owned by the WaveQuant workspace."""

from pathlib import Path


# ``project_paths.py`` lives at ``src/wavequant/infrastructure/filesystem``.
# Walking four parents reaches the installable workspace root, independent of
# the caller's current directory. Keeping this derivation here prevents domain code from
# learning about repository layout.
PROJECT_ROOT = Path(__file__).resolve().parents[4]


def project_path(*parts: str) -> Path:
    """Resolve a path inside the workspace without depending on process cwd."""
    return PROJECT_ROOT.joinpath(*parts)
