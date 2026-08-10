"""Project file storage for worldview exports and recoverable project archives.

This module provides an additional layer of persistence alongside the database.
Worldview is exported to data/projects/{project_id}/. Historical outline.json
files are left untouched for recovery, but automatic outline export was retired.
"""

import json
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.core.legacy_json import read_legacy_object_list
logger = logging.getLogger(__name__)

# Root directory for project file storage
PROJECTS_DIR = DATA_DIR / "projects"
PROJECT_DELETE_STAGING_DIR = DATA_DIR / "project-delete-staging"


class ProjectFileArchiveError(RuntimeError):
    """Raised when project files cannot be moved to or restored from the archive."""


@dataclass(frozen=True)
class ProjectFileArchive:
    """Paths needed to restore project files if the database transaction fails."""

    project_id: str
    original_path: Path
    archived_path: Path


def _safe_child(root: Path, name: str) -> Path:
    """Return a direct child of root and reject traversal or symlink escapes."""
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ProjectFileArchiveError("Invalid project storage name")

    root_resolved = root.resolve()
    candidate = root / name
    if candidate.resolve(strict=False).parent != root_resolved:
        raise ProjectFileArchiveError("Project storage path escapes its root")
    return candidate


def _ensure_project_dir(project_id: str) -> Path:
    """Create and return the project file directory."""
    proj_dir = _safe_child(PROJECTS_DIR, project_id)
    proj_dir.mkdir(parents=True, exist_ok=True)
    return proj_dir


def archive_project_files(project_id: str) -> ProjectFileArchive | None:
    """Move supplementary files to staging until the DB delete commits."""
    try:
        source = _safe_child(PROJECTS_DIR, project_id)
        if not source.exists():
            return None
        if source.is_symlink() or not source.is_dir():
            raise ProjectFileArchiveError("Project storage is not a regular directory")

        if PROJECT_DELETE_STAGING_DIR.is_symlink():
            raise ProjectFileArchiveError("Project delete staging root cannot be a symlink")
        PROJECT_DELETE_STAGING_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive_name = f"{project_id}--{timestamp}--{uuid.uuid4().hex[:8]}"
        destination = _safe_child(PROJECT_DELETE_STAGING_DIR, archive_name)
        source.replace(destination)
        logger.info("Project files archived: %s -> %s", source, destination)
        return ProjectFileArchive(project_id, source, destination)
    except ProjectFileArchiveError:
        raise
    except OSError as exc:
        raise ProjectFileArchiveError(
            f"Failed to archive project files for {project_id}"
        ) from exc


def restore_project_files(archive: ProjectFileArchive) -> None:
    """Restore archived files if the surrounding database delete fails."""
    try:
        if archive.original_path.exists():
            raise ProjectFileArchiveError("Active project storage already exists")
        if archive.archived_path.is_symlink() or not archive.archived_path.is_dir():
            raise ProjectFileArchiveError("Archived project storage is unavailable")

        archive.original_path.parent.mkdir(parents=True, exist_ok=True)
        archive.archived_path.replace(archive.original_path)
        logger.info(
            "Project files restored after failed delete: %s -> %s",
            archive.archived_path,
            archive.original_path,
        )
    except ProjectFileArchiveError:
        raise
    except OSError as exc:
        raise ProjectFileArchiveError(
            f"Failed to restore project files for {archive.project_id}"
        ) from exc


def finalize_project_file_delete(archive: ProjectFileArchive | None) -> None:
    """Remove staged supplementary files after the database delete commits.

    This operates only on runtime data under PROJECT_DELETE_STAGING_DIR. It does
    not remove repository source code or configuration.
    """
    if archive is None:
        return

    try:
        staging_root = PROJECT_DELETE_STAGING_DIR.resolve()
        archived_path = archive.archived_path
        if (
            archived_path.resolve(strict=False).parent != staging_root
            or archived_path.is_symlink()
            or not archived_path.is_dir()
        ):
            raise ProjectFileArchiveError("Staged project storage is invalid")
        shutil.rmtree(archived_path)
        logger.info("Staged project files permanently removed: %s", archived_path)
    except ProjectFileArchiveError:
        raise
    except OSError as exc:
        raise ProjectFileArchiveError(
            f"Failed to finalize project file delete for {archive.project_id}"
        ) from exc


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
    temporary_path: Path | None = None
    try:
        collections: dict[str, list[dict[str, Any]]] = {}
        for field in (
            "characters",
            "geography",
            "factions",
            "power_system",
            "history",
            "conflicts",
            "special_settings",
            "parsed_elements",
        ):
            result = read_legacy_object_list(getattr(worldview, field, None))
            if not result.valid:
                logger.error(
                    "Worldview file export blocked project=%s field=%s category=%s",
                    project_id,
                    field,
                    result.error_category,
                )
                return
            collections[field] = result.items

        proj_dir = _ensure_project_dir(project_id)
        filepath = proj_dir / "worldview.json"

        doc = {
            "_doc_type": "worldview",
            "_project_id": project_id,
            "_version": 1,
            "_exported_at": datetime.now(timezone.utc).isoformat(),
            "source": getattr(worldview, "source", "manual"),
            "raw_text": getattr(worldview, "raw_text", None),
            **collections,
            "created_at": getattr(worldview, "created_at", None),
        }

        # Convert datetime to ISO string for JSON serialization
        if doc["created_at"] and isinstance(doc["created_at"], datetime):
            doc["created_at"] = doc["created_at"].isoformat()

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=proj_dir,
            prefix=".worldview.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(json.dumps(doc, ensure_ascii=False, indent=2))
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, filepath)
        temporary_path = None
        logger.info("Worldview file saved: %s (%d bytes)", filepath, filepath.stat().st_size)
    except Exception as e:
        logger.error("Failed to save worldview file for project %s: %s", project_id, e)
        # Non-fatal — DB is the source of truth, file is supplementary
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to clean temporary worldview export project=%s",
                    project_id,
                )


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
