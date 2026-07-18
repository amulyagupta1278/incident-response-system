from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from agents.connector_registry import runtime_connectors


def load_obsidian_documents(repo_root: Path) -> Iterable["KnowledgeChunk"]:
    from agents.knowledge_base import KnowledgeChunk

    repo_root = repo_root.resolve()
    for record in runtime_connectors("obsidian_vault"):
        config = record.get("config") or {}
        raw_path = str(config.get("path") or "").strip()
        if not raw_path:
            continue

        vault_path = Path(raw_path).expanduser()
        if not vault_path.is_absolute():
            vault_path = (repo_root / vault_path).resolve()
        if not vault_path.exists() or not vault_path.is_dir():
            continue

        vault_name = vault_path.name
        for rel_path, content, updated_at, tags in _scan_vault(vault_path):
            title, tags = _extract_title_and_tags(content, rel_path.name, tags)
            yield KnowledgeChunk(
                chunk_id=f"obsidian:{vault_name}:{rel_path.as_posix()}",
                title=title,
                source_path=f"obsidian://{vault_name}/{rel_path.as_posix()}",
                kind="obsidian",
                content=content,
                updated_at=updated_at,
                tags=",".join(tags),
            )


def _scan_vault(vault_path: Path) -> Iterable[tuple[Path, str, str, List[str]]]:
    for file_path in sorted(vault_path.rglob("*.md")):
        if any(part.startswith(".") for part in file_path.relative_to(vault_path).parts):
            continue
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        updated_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc).isoformat()
        tags = _extract_frontmatter_tags(text)
        yield file_path.relative_to(vault_path), text, updated_at, tags


def _extract_title_and_tags(content: str, filename: str, tags: List[str]) -> tuple[str, List[str]]:
    title = _extract_markdown_title(content) or Path(filename).stem.replace("-", " ").title()
    if not tags:
        tags = _extract_frontmatter_tags(content)
    return title, tags


def _extract_markdown_title(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _extract_frontmatter_tags(content: str) -> List[str]:
    if not content.startswith("---"):
        return []
    parts = content.split("---", 2)
    if len(parts) < 3:
        return []
    body = parts[1]
    tags: List[str] = []
    for line in body.splitlines():
        match = re.match(r"^tags:\s*(.*)", line, re.IGNORECASE)
        if match:
            raw_tags = match.group(1).strip()
            if raw_tags.startswith("[") and raw_tags.endswith("]"):
                raw_tags = raw_tags[1:-1]
            tags = [tag.strip().strip('"\'') for tag in re.split(r",\s*", raw_tags) if tag.strip()]
            break
    return tags

