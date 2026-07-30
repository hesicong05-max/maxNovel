"""Tests for project file persistence module.

Verifies that worldview and outline data is correctly exported as
independent document files alongside DB storage (dual-write).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.project_files import (
    PROJECTS_DIR,
    ProjectFileArchiveError,
    archive_project_files,
    finalize_project_file_delete,
    load_outline_file,
    load_worldview_file,
    restore_project_files,
    save_outline_file,
    save_worldview_file,
)


@pytest.fixture
def mock_worldview():
    """A mock Worldview ORM object with all required attributes."""
    wv = MagicMock()
    wv.source = "manual"
    wv.raw_text = "Raw worldview text for testing"
    wv.characters = [{"name": "Hero", "personality": "brave"}]
    wv.geography = [{"name": "Kingdom", "significance": "high"}]
    wv.factions = [{"name": "Guild", "stance": "neutral"}]
    wv.power_system = [{"name": "Mana", "levels": ["low", "high"]}]
    wv.history = [{"name": "War", "time": "year 100"}]
    wv.conflicts = [{"name": "Rivalry", "type": "personal"}]
    wv.special_settings = [{"name": "Curse", "rules": ["no healing"]}]
    wv.parsed_elements = [
        {"id": "char_0", "name": "Hero", "category": "character", "priority": "core"},
        {"id": "geo_0", "name": "Kingdom", "category": "geography", "priority": "important"},
    ]
    wv.created_at = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    return wv


@pytest.fixture
def mock_outline():
    """A mock Outline ORM object with all required attributes."""
    ol = MagicMock()
    ol.story_arc = "A hero rises from obscurity to save the kingdom."
    ol.chapters = [
        {"chapter_num": 1, "title": "Awakening", "summary": "Hero discovers power"},
        {"chapter_num": 2, "title": "Journey", "summary": "Hero leaves home"},
    ]
    ol.reveal_plan = [
        {"chapter": 1, "phase": "起势", "elements": ["Hero"], "summary": "Intro"},
    ]
    ol.created_at = datetime(2025, 1, 16, 14, 0, 0, tzinfo=timezone.utc)
    ol.updated_at = datetime(2025, 1, 16, 14, 30, 0, tzinfo=timezone.utc)
    return ol


class TestSaveWorldviewFile:
    """Tests for save_worldview_file function."""

    def test_creates_file(self, mock_worldview, tmp_path, monkeypatch):
        """File is created with correct path."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_worldview_file("proj_123", mock_worldview)

        filepath = tmp_path / "proj_123" / "worldview.json"
        assert filepath.exists()

    def test_file_contains_all_categories(self, mock_worldview, tmp_path, monkeypatch):
        """File contains all 7 worldview categories."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_worldview_file("proj_123", mock_worldview)

        filepath = tmp_path / "proj_123" / "worldview.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))

        assert data["characters"] == [{"name": "Hero", "personality": "brave"}]
        assert data["geography"] == [{"name": "Kingdom", "significance": "high"}]
        assert data["factions"] == [{"name": "Guild", "stance": "neutral"}]
        assert data["power_system"] == [{"name": "Mana", "levels": ["low", "high"]}]
        assert data["history"] == [{"name": "War", "time": "year 100"}]
        assert data["conflicts"] == [{"name": "Rivalry", "type": "personal"}]
        assert data["special_settings"] == [{"name": "Curse", "rules": ["no healing"]}]

    def test_file_contains_parsed_elements(self, mock_worldview, tmp_path, monkeypatch):
        """File contains parsed_elements with priority and category info."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_worldview_file("proj_123", mock_worldview)

        filepath = tmp_path / "proj_123" / "worldview.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))

        assert len(data["parsed_elements"]) == 2
        assert data["parsed_elements"][0]["name"] == "Hero"
        assert data["parsed_elements"][0]["priority"] == "core"

    def test_file_contains_metadata(self, mock_worldview, tmp_path, monkeypatch):
        """File contains doc_type, project_id, version, exported_at, source."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_worldview_file("proj_123", mock_worldview)

        filepath = tmp_path / "proj_123" / "worldview.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))

        assert data["_doc_type"] == "worldview"
        assert data["_project_id"] == "proj_123"
        assert data["_version"] == 1
        assert "_exported_at" in data
        assert data["source"] == "manual"

    def test_overwrites_existing_file(self, mock_worldview, tmp_path, monkeypatch):
        """Saving again overwrites the old file."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_worldview_file("proj_123", mock_worldview)

        # Modify and save again
        mock_worldview.characters = [{"name": "Updated Hero"}]
        save_worldview_file("proj_123", mock_worldview)

        filepath = tmp_path / "proj_123" / "worldview.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data["characters"] == [{"name": "Updated Hero"}]

    def test_handles_missing_attributes(self, tmp_path, monkeypatch):
        """Function handles objects with missing attributes gracefully."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        wv = MagicMock(spec=[])  # No attributes
        save_worldview_file("proj_456", wv)

        filepath = tmp_path / "proj_456" / "worldview.json"
        assert filepath.exists()
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data["characters"] == []
        assert data["parsed_elements"] == []

    def test_does_not_crash_on_error(self, mock_worldview, tmp_path, monkeypatch):
        """Function logs error but does not raise on filesystem failure."""
        # Make directory creation fail
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", Path("/nonexistent/path/that/should/not/exist"))
        # Should not raise
        save_worldview_file("proj_789", mock_worldview)


class TestSaveOutlineFile:
    """Tests for save_outline_file function."""

    def test_creates_file(self, mock_outline, tmp_path, monkeypatch):
        """File is created with correct path."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_outline_file("proj_123", mock_outline)

        filepath = tmp_path / "proj_123" / "outline.json"
        assert filepath.exists()

    def test_file_contains_story_arc(self, mock_outline, tmp_path, monkeypatch):
        """File contains story_arc."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_outline_file("proj_123", mock_outline)

        filepath = tmp_path / "proj_123" / "outline.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))

        assert data["story_arc"] == "A hero rises from obscurity to save the kingdom."

    def test_file_contains_chapters(self, mock_outline, tmp_path, monkeypatch):
        """File contains chapters array."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_outline_file("proj_123", mock_outline)

        filepath = tmp_path / "proj_123" / "outline.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))

        assert len(data["chapters"]) == 2
        assert data["chapters"][0]["title"] == "Awakening"

    def test_file_contains_reveal_plan(self, mock_outline, tmp_path, monkeypatch):
        """File contains reveal_plan."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_outline_file("proj_123", mock_outline)

        filepath = tmp_path / "proj_123" / "outline.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))

        assert len(data["reveal_plan"]) == 1
        assert data["reveal_plan"][0]["phase"] == "起势"

    def test_file_contains_metadata(self, mock_outline, tmp_path, monkeypatch):
        """File contains doc_type, project_id, version, exported_at."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_outline_file("proj_123", mock_outline)

        filepath = tmp_path / "proj_123" / "outline.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))

        assert data["_doc_type"] == "outline"
        assert data["_project_id"] == "proj_123"
        assert data["_version"] == 1
        assert "_exported_at" in data

    def test_does_not_crash_on_error(self, mock_outline, tmp_path, monkeypatch):
        """Function logs error but does not raise on filesystem failure."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", Path("/nonexistent/path/that/should/not/exist"))
        save_outline_file("proj_789", mock_outline)


class TestLoadFiles:
    """Tests for load_worldview_file and load_outline_file."""

    def test_load_worldview_returns_none_if_not_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        result = load_worldview_file("nonexistent_project")
        assert result is None

    def test_load_outline_returns_none_if_not_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        result = load_outline_file("nonexistent_project")
        assert result is None

    def test_load_worldview_roundtrip(self, mock_worldview, tmp_path, monkeypatch):
        """Save then load returns the same data."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_worldview_file("proj_123", mock_worldview)

        loaded = load_worldview_file("proj_123")
        assert loaded is not None
        assert loaded["characters"] == [{"name": "Hero", "personality": "brave"}]
        assert loaded["parsed_elements"][0]["name"] == "Hero"

    def test_load_outline_roundtrip(self, mock_outline, tmp_path, monkeypatch):
        """Save then load returns the same data."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_outline_file("proj_123", mock_outline)

        loaded = load_outline_file("proj_123")
        assert loaded is not None
        assert loaded["story_arc"] == "A hero rises from obscurity to save the kingdom."
        assert len(loaded["chapters"]) == 2

    def test_coexistence_of_worldview_and_outline(self, mock_worldview, mock_outline, tmp_path, monkeypatch):
        """Both files can coexist in the same project directory."""
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path)
        save_worldview_file("proj_123", mock_worldview)
        save_outline_file("proj_123", mock_outline)

        proj_dir = tmp_path / "proj_123"
        assert (proj_dir / "worldview.json").exists()
        assert (proj_dir / "outline.json").exists()

        wv = load_worldview_file("proj_123")
        ol = load_outline_file("proj_123")
        assert wv is not None
        assert ol is not None
        assert wv["_doc_type"] == "worldview"
        assert ol["_doc_type"] == "outline"


class TestProjectFileArchive:
    """Tests for recoverable project file removal."""

    def test_archive_moves_project_directory(self, tmp_path, monkeypatch):
        projects_dir = tmp_path / "projects"
        staging_dir = tmp_path / "project-delete-staging"
        project_dir = projects_dir / "proj_123"
        project_dir.mkdir(parents=True)
        (project_dir / "worldview.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(
            "app.core.project_files.PROJECT_DELETE_STAGING_DIR", staging_dir
        )

        archive = archive_project_files("proj_123")

        assert archive is not None
        assert not project_dir.exists()
        assert archive.archived_path.parent == staging_dir
        assert (archive.archived_path / "worldview.json").exists()

    def test_archive_can_be_restored(self, tmp_path, monkeypatch):
        projects_dir = tmp_path / "projects"
        staging_dir = tmp_path / "project-delete-staging"
        project_dir = projects_dir / "proj_123"
        project_dir.mkdir(parents=True)
        (project_dir / "outline.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(
            "app.core.project_files.PROJECT_DELETE_STAGING_DIR", staging_dir
        )

        archive = archive_project_files("proj_123")
        assert archive is not None
        restore_project_files(archive)

        assert (project_dir / "outline.json").exists()
        assert not archive.archived_path.exists()

    def test_archive_is_removed_after_successful_delete(self, tmp_path, monkeypatch):
        projects_dir = tmp_path / "projects"
        staging_dir = tmp_path / "project-delete-staging"
        project_dir = projects_dir / "proj_123"
        project_dir.mkdir(parents=True)
        (project_dir / "outline.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(
            "app.core.project_files.PROJECT_DELETE_STAGING_DIR", staging_dir
        )

        archive = archive_project_files("proj_123")
        assert archive is not None
        finalize_project_file_delete(archive)

        assert not project_dir.exists()
        assert not archive.archived_path.exists()
        assert list(staging_dir.iterdir()) == []

    def test_archive_missing_project_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path / "projects")
        monkeypatch.setattr(
            "app.core.project_files.PROJECT_DELETE_STAGING_DIR",
            tmp_path / "project-delete-staging",
        )
        assert archive_project_files("proj_missing") is None

    def test_archive_rejects_path_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", tmp_path / "projects")
        monkeypatch.setattr(
            "app.core.project_files.PROJECT_DELETE_STAGING_DIR",
            tmp_path / "project-delete-staging",
        )
        with pytest.raises(ProjectFileArchiveError):
            archive_project_files("../outside")
