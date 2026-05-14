"""Shared configuration loader for the local Fiction Forge MCP tools."""

from pathlib import Path

import yaml


def find_root() -> Path:
    """Find the project root by walking up from this file's directory."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "project.yaml").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent


ROOT_DIR = find_root()


def load_config() -> dict:
    """Load project.yaml from the project root."""
    config_path = ROOT_DIR / "project.yaml"
    if config_path.exists():
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return {}


def get_project(config: dict) -> dict:
    """Get project metadata with defaults."""
    project = config.get("project", {})
    return {
        "title": project.get("title", "Libro"),
        "subtitle": project.get("subtitle", ""),
        "author": project.get("author", ""),
        "publisher": project.get("publisher", ""),
        "year": project.get("year", 2026),
    }


def get_structure(config: dict) -> dict:
    """Get directory structure config with defaults."""
    structure = config.get("structure", {})
    return {
        "book_dir": ROOT_DIR / structure.get("book_dir", "Capitulos"),
        "reference_dir": ROOT_DIR / structure.get("reference_dir", "Biblioteca"),
        "output_dir": ROOT_DIR / structure.get("output_dir", "output"),
        "templates_dir": ROOT_DIR / structure.get("templates_dir", "templates"),
        "cover_image": ROOT_DIR / structure["cover_image"] if structure.get("cover_image") else None,
        "illustrations_src": ROOT_DIR / structure["illustrations_src"]
        if structure.get("illustrations_src")
        else None,
        "front_matter": set(structure.get("front_matter", [])),
        "parts": {int(k): v for k, v in structure.get("parts", {}).items()},
    }


def get_characters(config: dict) -> dict:
    """Get character alias map."""
    characters = config.get("characters") or {}
    return characters.get("aliases") or {}


def get_reference_sources(config: dict) -> dict[str, Path]:
    """Get reference source paths resolved from the project root."""
    sources = config.get("reference_sources") or {}
    return {key: ROOT_DIR / value for key, value in sources.items()}


def get_scanner_config(config: dict) -> dict:
    """Get scanner configuration defaults for future Fiction Forge tooling."""
    scanner = config.get("scanner", {})
    severity = scanner.get("severity", {})
    return {
        "preset": scanner.get("preset", "literary_fiction"),
        "severity": {
            "critical": severity.get("critical", 12.0),
            "high": severity.get("high", 6.0),
            "medium": severity.get("medium", 3.0),
        },
    }

