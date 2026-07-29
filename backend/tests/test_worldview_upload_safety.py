"""Security regression tests for worldview document uploads."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.api.worldview import (
    MAX_DOCX_ENTRIES,
    MAX_DOCX_UNCOMPRESSED_SIZE,
    _extract_text_from_docx,
)


def _mock_archive(entries):
    archive = MagicMock()
    archive.__enter__.return_value = archive
    archive.infolist.return_value = entries
    return archive


def test_docx_rejects_excessive_uncompressed_size():
    entries = [SimpleNamespace(file_size=MAX_DOCX_UNCOMPRESSED_SIZE + 1)]
    with patch("app.api.worldview.zipfile.ZipFile", return_value=_mock_archive(entries)):
        with pytest.raises(ValueError, match="解压后内容过大"):
            _extract_text_from_docx(b"fake-docx")


def test_docx_rejects_excessive_archive_entries():
    entries = [
        SimpleNamespace(file_size=1)
        for _ in range(MAX_DOCX_ENTRIES + 1)
    ]
    with patch("app.api.worldview.zipfile.ZipFile", return_value=_mock_archive(entries)):
        with pytest.raises(ValueError, match="内部文件数量异常"):
            _extract_text_from_docx(b"fake-docx")
