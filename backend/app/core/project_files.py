"""Project file storage — persists worldview and outline as independent document files.

This module provides an additional layer of persistence alongside the database.
Worldview and outline are exported as JSON files to data/projects/{project_id}/
so that users have visible, inspectable document artifacts.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

# Root directory for project file storage
PROJECTS_DIR = DATA_DIR / "projects"


def _ensure_project_dir(project_id: str) -> Path:
    """Create and return the project file directory."""
    proj_dir = PROJECTS_DIR / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    return proj_dir


def save_worldview_file(project_id: str, worldview: Any) -> None:
    """Export worldview data as an independent JSON document file.

    Called after DB commit in set_worldview to ensure the file is always
    in sync with the database record.

    Args:
        project_id: The project ID
        worldview: The Worldview ORM model instance (must have attributes:
            characters, geography, factions, power_system, history,
            conflicts, special_settings, raw_text, source, parsed_elements,
            created_at)
    """
    try:
        proj_dir = _ensure_project_dir(project_id)
        filepath = proj_dir / "worldview.json"

        doc = {
            "_doc_type": "worldview",
            "_project_id": project_id,
            "_version": 1,
            "_exported_at": datetime.now(timezone.utc).isoformat(),
            "source": getattr(worldview, "source", "manual"),
            "raw_text": getattr(worldview, "raw_text", None),
            "characters": getattr(worldview, "characters", []),
            "geography": getattr(worldview, "geography", []),
            "factions": getattr(worldview, "factions", []),
            "power_system": getattr(worldview, "power_system", []),
            "history": getattr(worldview, "history", []),
            "conflicts": getattr(worldview, "conflicts", []),
            "special_settings": getattr(worldview, "special_settings", []),
            "parsed_elements": getattr(worldview, "parsed_elements", []),
            "created_at": getattr(worldview, "created_at", None),
        }

        # Convert datetime to ISO string for JSON serialization
        if doc["created_at"] and isinstance(doc["created_at"], datetime):
            doc["created_at"] = doc["created_at"].isoformat()

        filepath.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Worldview file saved: %s (%d bytes)", filepath, filepath.stat().st_size)
    except Exception as e:
        logger.error("Failed to save worldview file for project %s: %s", project_id, e)
        # Non-fatal — DB is the source of truth, file is supplementary


def save_outline_file(project_id: str, outline: Any) -> None:
    """Export outline data as an independent JSON document file.

    Called after DB commit in generate_outline / generate_outline_stream /
    update_outline to ensure the file is always in sync with the database record.

    Args:
        project_id: The project ID
        outline: The Outline ORM model instance (must have attributes:
            story_arc, chapters, reveal_plan, created_at, updated_at)
    """
    try:
        proj_dir = _ensure_project_dir(project_id)
        filepath = proj_dir / "outline.json"

        created_at = getattr(outline, "created_at", None)
        updated_at = getattr(outline, "updated_at", None)

        doc = {
            "_doc_type": "outline",
            "_project_id": project_id,
            "_version": 1,
            "_exported_at": datetime.now(timezone.utc).isoformat(),
            "story_arc": getattr(outline, "story_arc", ""),
            "chapters": getattr(outline, "chapters", []),
            "reveal_plan": getattr(outline, "reveal_plan", []),
            "created_at": created_at.isoformat() if isinstance(created_at, datetime) else created_at,
            "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at,
        }

        filepath.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Outline file saved: %s (%d bytes)", filepath, filepath.stat().st_size)
    except Exception as e:
        logger.error("Failed to save outline file for project %s: %s", project_id, e)
        # Non-fatal — DB is the source of truth, file is supplementary


def load_worldview_file(project_id: str) -> dict[str, Any] | None:
    """Read worldview from file (for verification / debugging / backup).

    Returns None if file doesn't exist.
    """
    filepath = PROJECTS_DIR / project_id / "worldview.json"
    if not filepath.exists():
        return None
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to read worldview file for project %s: %s", project_id, e)
        return None


def load_outline_file(project_id: str) -> dict[str, Any] | None:
    """Read outline from file (for verification / debugging / backup).

    Returns None if file doesn't exist.
    """
    filepath = PROJECTS_DIR / project_id / "outline.json"
    if not filepath.exists():
        return None
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to read outline file for project %s: %s", project_id, e)
        return None
