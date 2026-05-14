#!/usr/bin/env python3
"""Fiction context MCP server adapted from Fiction Forge for this project.

The server indexes the Markdown canon in Biblioteca and exposes search,
character, chapter-context, continuity, and foreshadowing tools to Codex.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml
from fastmcp import FastMCP

from config import get_characters, get_reference_sources, get_structure, load_config


CONFIG = load_config()
STRUCTURE = get_structure(CONFIG)
BOOK_DIR: Path = STRUCTURE["book_dir"]
REFERENCE_DIR: Path = STRUCTURE["reference_dir"]
CHARACTER_ALIASES = get_characters(CONFIG)
REFERENCE_SOURCES = get_reference_sources(CONFIG)
PART_BREAKS = STRUCTURE["parts"]

CONTINUITY_RULES_PATH = REFERENCE_DIR / "continuity_rules.yaml"
CHARACTER_STATES_PATH = REFERENCE_DIR / "character_states.yaml"


def _load_yaml_optional(path: Path) -> dict:
    """Load a YAML file if it exists, otherwise return an empty dict."""
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


CONTINUITY_RULES = _load_yaml_optional(CONTINUITY_RULES_PATH)
CHARACTER_STATES = _load_yaml_optional(CHARACTER_STATES_PATH)


def normalize_text(value: str) -> str:
    """Lowercase text and strip diacritics for forgiving Spanish searches."""
    lowered = value.lower()
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(char for char in normalized if not unicodedata.combining(char))


class DataStore:
    """Load and index the configured reference documents at startup."""

    def __init__(self) -> None:
        self.documents: dict[str, str] = {}
        self.sections: list[dict] = []
        self.missing_sources: dict[str, str] = {}
        self._load_all()

    def _load_all(self) -> None:
        for name, path in REFERENCE_SOURCES.items():
            if path.exists():
                text = path.read_text(encoding="utf-8")
                self.documents[name] = text
                self._parse_sections(name, text)
            else:
                self.missing_sources[name] = str(path)

    def _parse_sections(self, source: str, text: str) -> None:
        lines = text.split("\n")
        current_heading = "(top)"
        current_level = 0
        current_lines: list[str] = []

        for line in lines:
            match = re.match(r"^(#{1,6})\s+(.+)", line)
            if match:
                self._append_section(source, current_heading, current_level, current_lines)
                current_heading = match.group(2).strip()
                current_level = len(match.group(1))
                current_lines = [line]
            else:
                current_lines.append(line)

        self._append_section(source, current_heading, current_level, current_lines)

    def _append_section(self, source: str, heading: str, level: int, lines: list[str]) -> None:
        content = "\n".join(lines).strip()
        if content:
            self.sections.append(
                {
                    "source": source,
                    "heading": heading,
                    "level": level,
                    "content": content,
                }
            )

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Accent-insensitive term search across all indexed sections."""
        normalized_query = normalize_text(query)
        terms = [term for term in normalized_query.split() if term]
        if not terms:
            return []

        results = []
        for section in self.sections:
            normalized_content = normalize_text(section["content"])
            normalized_heading = normalize_text(section["heading"])
            haystack = f"{normalized_heading}\n{normalized_content}"
            matches = sum(1 for term in terms if term in haystack)
            phrase_bonus = 5 if normalized_query in haystack else 0
            required_matches = len(terms) if len(terms) <= 3 else max(3, len(terms) - 1)

            if matches >= required_matches:
                density = phrase_bonus + sum(haystack.count(term) for term in terms)
                results.append((density, section))

        results.sort(key=lambda item: item[0], reverse=True)
        return [section for _, section in results[:max_results]]

    def get_character_sections(self, name: str) -> list[dict]:
        """Find sections mentioning a character, resolving configured aliases."""
        normalized_name = normalize_text(name).strip()
        aliases = [normalized_name]

        for canonical, alias_list in CHARACTER_ALIASES.items():
            normalized_aliases = [normalize_text(alias) for alias in alias_list]
            if normalized_name == normalize_text(canonical) or normalized_name in normalized_aliases:
                aliases = [normalize_text(canonical), *normalized_aliases]
                break

        results = []
        for section in self.sections:
            text = normalize_text(section["content"])
            heading = normalize_text(section["heading"])
            if any(alias in text or alias in heading for alias in aliases):
                results.append(section)

        return results


def get_chapters() -> list[Path]:
    """Return a sorted list of Markdown chapter files."""
    if not BOOK_DIR.exists():
        return []
    chapters = [path for path in BOOK_DIR.glob("*.md") if not path.name.lower().startswith("notes")]

    def sort_key(path: Path) -> tuple[int, str, str]:
        match = re.match(r"(\d+)([a-z]?)", path.name, re.IGNORECASE)
        number = int(match.group(1)) if match else 999
        suffix = match.group(2).lower() if match else ""
        return number, suffix, path.name.lower()

    return sorted(chapters, key=sort_key)


def strip_comments(text: str) -> str:
    """Remove HTML comments from chapter text."""
    lines = text.split("\n")
    filtered = []
    in_comment = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("<!--"):
            if "-->" not in stripped:
                in_comment = True
            continue
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        filtered.append(line)

    return "\n".join(filtered)


def get_chapter_number(filename: str) -> int:
    """Extract the leading numeric chapter number from a filename."""
    match = re.match(r"(\d+)", filename)
    return int(match.group(1)) if match else -1


def get_chapter_title(text: str) -> str:
    """Extract the first H1 title from chapter text."""
    match = re.search(r"^#\s+(.+)", text, re.MULTILINE)
    return match.group(1).strip() if match else "(sin titulo)"


def get_part_for_chapter(chapter_number: int) -> str:
    """Determine the configured part for a chapter number."""
    if chapter_number < 1:
        return "Front Matter"

    current_part = "Front Matter"
    for break_chapter in sorted(PART_BREAKS):
        if chapter_number >= break_chapter:
            info = PART_BREAKS[break_chapter]
            current_part = f"Parte {info['number']}: {info['name']}"
    return current_part


def _get_character_state(canonical: str, chapter: int) -> str:
    """Get an optional timeline-aware character state."""
    states = CHARACTER_STATES.get(canonical, [])
    for entry in states:
        if len(entry) >= 3:
            start, end, note = entry[0], entry[1], entry[2]
            if start <= chapter <= end:
                return note
    return ""


mcp = FastMCP("fiction-context")
store = DataStore()


@mcp.tool()
def search_bible(query: str) -> str:
    """Search the configured canon, story bible, characters, arcs, and notes."""
    results = store.search(query, max_results=8)
    if not results:
        missing = ""
        if store.missing_sources:
            missing = "\n\nMissing sources:\n" + "\n".join(
                f"- {name}: {path}" for name, path in store.missing_sources.items()
            )
        return f"No results found for: {query}{missing}"

    output = []
    for result in results:
        content = result["content"]
        if len(content) > 2000:
            content = content[:2000] + "\n\n... (truncated)"
        output.append(f"## [{result['source']}] {result['heading']}\n\n{content}")
    return "\n\n".join(output)


@mcp.tool()
def get_character(name: str, chapter: int | None = None) -> str:
    """Get character information and optional chapter-specific state."""
    sections = store.get_character_sections(name)
    if not sections:
        return f"No character information found for: {name}"

    character_sections = [section for section in sections if section["source"] == "personajes"]
    other_sections = [section for section in sections if section["source"] != "personajes"]

    output = []
    if character_sections:
        output.append("## Character Profile")
        for section in character_sections[:5]:
            output.append(f"### {section['heading']}\n\n{section['content']}")

    if other_sections:
        output.append("## Additional References")
        for section in other_sections[:5]:
            content = section["content"]
            if len(content) > 1500:
                content = content[:1500] + "\n\n... (truncated)"
            output.append(f"### [{section['source']}] {section['heading']}\n\n{content}")

    if chapter is not None:
        output.append(f"## Chapter {chapter} Context")
        output.append(f"Story position: {get_part_for_chapter(chapter)}")

        normalized_name = normalize_text(name).strip()
        canonical = normalized_name
        for candidate, aliases in CHARACTER_ALIASES.items():
            normalized_aliases = [normalize_text(alias) for alias in aliases]
            if normalized_name == normalize_text(candidate) or normalized_name in normalized_aliases:
                canonical = candidate
                break

        state_notes = _get_character_state(canonical, chapter)
        if state_notes:
            output.append(f"State at Ch {chapter}:\n{state_notes}")

    return "\n\n".join(output)


@mcp.tool()
def get_chapter_context(chapter_number: int) -> str:
    """Get chapter metadata, opening/closing lines, and neighboring context."""
    chapters = get_chapters()
    target_index = None
    target_file = None

    for index, chapter in enumerate(chapters):
        if get_chapter_number(chapter.name) == chapter_number:
            target_index = index
            target_file = chapter
            break

    if target_file is None or target_index is None:
        return f"Chapter {chapter_number} not found in {BOOK_DIR}."

    content = strip_comments(target_file.read_text(encoding="utf-8"))
    title = get_chapter_title(content)
    part = get_part_for_chapter(chapter_number)
    word_count = len(content.split())
    content_lines = [line for line in content.split("\n") if line.strip() and not line.startswith("#")]
    opening = "\n".join(content_lines[:5])
    closing = "\n".join(content_lines[-5:])

    output = [
        f"# Chapter {chapter_number}: {title}",
        f"**Part**: {part}",
        f"**File**: {target_file.name}",
        f"**Words**: {word_count:,}",
        f"\n## Opening lines\n```\n{opening}\n```",
        f"\n## Closing lines\n```\n{closing}\n```",
    ]

    if target_index > 0:
        previous_file = chapters[target_index - 1]
        previous_content = strip_comments(previous_file.read_text(encoding="utf-8"))
        previous_title = get_chapter_title(previous_content)
        previous_lines = [
            line for line in previous_content.split("\n") if line.strip() and not line.startswith("#")
        ]
        previous_ending = "\n".join(previous_lines[-5:])
        output.append(
            f"\n## Previous chapter ending "
            f"(Ch {get_chapter_number(previous_file.name)}: {previous_title})\n"
            f"```\n{previous_ending}\n```"
        )

    if target_index < len(chapters) - 1:
        next_file = chapters[target_index + 1]
        next_content = strip_comments(next_file.read_text(encoding="utf-8"))
        next_title = get_chapter_title(next_content)
        next_lines = [line for line in next_content.split("\n") if line.strip() and not line.startswith("#")]
        next_opening = "\n".join(next_lines[:5])
        output.append(
            f"\n## Next chapter opening "
            f"(Ch {get_chapter_number(next_file.name)}: {next_title})\n"
            f"```\n{next_opening}\n```"
        )

    return "\n".join(output)


@mcp.tool()
def check_continuity(text: str, chapter: int) -> str:
    """Check a passage against optional continuity rules and story position."""
    warnings = []
    text_normalized = normalize_text(text)

    for event in CONTINUITY_RULES.get("death_events", []):
        death_chapter = event.get("chapter", 999)
        if chapter > death_chapter:
            for alias in event.get("aliases", [event.get("character", "")]):
                alias_normalized = normalize_text(alias)
                if alias_normalized in text_normalized:
                    action_patterns = [
                        rf"{re.escape(alias_normalized)}\s+(dijo|dice|camino|corrio|miro|sonrio|hablo)",
                        rf"{re.escape(alias_normalized)}\s+estaba\s+\w+",
                    ]
                    if any(re.search(pattern, text_normalized) for pattern in action_patterns):
                        warnings.append(
                            f"WARNING: {event['character']} appears active in Ch {chapter}, "
                            f"but died in Ch {death_chapter}."
                        )
                        break

    for reveal in CONTINUITY_RULES.get("reveals", []):
        reveal_chapter = reveal.get("chapter", 999)
        before = normalize_text(reveal.get("before", ""))
        after = normalize_text(reveal.get("after", ""))
        if chapter >= reveal_chapter and before and before in text_normalized and after not in text_normalized:
            note = reveal.get("note", f"After Ch {reveal_chapter}, '{before}' is known as '{after}'.")
            warnings.append(f"NOTE: {note}")

    for change in CONTINUITY_RULES.get("status_changes", []):
        change_chapter = change.get("chapter", 999)
        character = normalize_text(change.get("character", ""))
        if chapter >= change_chapter and character and character in text_normalized:
            note = change.get("note", "")
            if note:
                warnings.append(f"NOTE: {note}")

    part = get_part_for_chapter(chapter)
    if not warnings:
        return f"No continuity issues detected for Ch {chapter}.\nStory position: {part}"

    output = [f"Continuity check for Ch {chapter} ({part}):"]
    output.extend(f"- {warning}" for warning in warnings)
    return "\n".join(output)


@mcp.tool()
def get_foreshadowing(thread: str | None = None) -> str:
    """Search plant/payoff material in arcs, backlog, diagnosis, and bible sources."""
    sources = ("arcos", "backlog", "diagnostico", "biblia")

    if thread:
        results = [
            section
            for section in store.search(thread, max_results=8)
            if section["source"] in sources
        ]
    else:
        keywords = (
            "revel",
            "futuro",
            "pendiente",
            "presagio",
            "payoff",
            "pista",
            "señal",
            "senal",
            "más adelante",
            "mas adelante",
            "no se revela",
            "oculto",
        )
        results = []
        for section in store.sections:
            if section["source"] not in sources:
                continue
            text = normalize_text(f"{section['heading']}\n{section['content']}")
            if any(keyword in text for keyword in keywords):
                results.append(section)
            if len(results) >= 8:
                break

    if not results:
        return f"No foreshadowing found for thread: {thread}" if thread else "No foreshadowing data found."

    title = f"## Foreshadowing: {thread}" if thread else "## Foreshadowing and Pending Threads"
    output = [title]
    for result in results:
        content = result["content"]
        if len(content) > 2000:
            content = content[:2000] + "\n\n... (truncated)"
        output.append(f"### [{result['source']}] {result['heading']}\n\n{content}")
    return "\n\n".join(output)


if __name__ == "__main__":
    mcp.run()
