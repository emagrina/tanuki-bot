from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoSnapshot:
    repo_path: str
    tree: list[str]
    important_files: dict[str, str]  # relative_path -> content (truncated)


DEFAULT_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".cache",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
}


IMPORTANT_CANDIDATES = [
    "README.md",
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "Makefile",
    "docker-compose.yml",
    "Dockerfile",
    ".env.example",
    "tsconfig.json",
    "next.config.js",
    "next.config.mjs",
    "vite.config.ts",
    "vite.config.js",
]


def _is_ignored(path: Path) -> bool:
    parts = set(path.parts)
    return any(p in DEFAULT_IGNORE_DIRS for p in parts)


def _safe_read(path: Path, limit_chars: int = 12_000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if len(text) > limit_chars:
            return text[:limit_chars] + "\n\n...<truncated>..."
        return text
    except Exception:
        return ""


def snapshot_repo(repo_path: str, max_tree: int = 800) -> RepoSnapshot:
    root = Path(repo_path).expanduser().resolve()

    tree: list[str] = []
    important: dict[str, str] = {}

    # Build tree (files only)
    for p in root.rglob("*"):
        if _is_ignored(p):
            continue
        if p.is_file():
            rel = str(p.relative_to(root))
            tree.append(rel)
            if len(tree) >= max_tree:
                break

    # Collect important files if present
    for name in IMPORTANT_CANDIDATES:
        cand = root / name
        if cand.exists() and cand.is_file():
            important[name] = _safe_read(cand)

    # If there is a src folder, grab a couple of entrypoints (best effort)
    for extra in ["src/main.ts", "src/index.ts", "src/app.ts", "src/main.py", "src/__main__.py"]:
        cand = root / extra
        if cand.exists() and cand.is_file():
            important[extra] = _safe_read(cand)

    return RepoSnapshot(repo_path=str(root), tree=tree, important_files=important)