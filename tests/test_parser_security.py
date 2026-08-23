import zipfile

import pytest

from app.config import settings
from app.parsers.epub_parser import EpubException, read_epub_safe


def test_epub_rejects_path_traversal_entry(tmp_path):
    archive_path = tmp_path / "unsafe.epub"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", "unsafe")

    with pytest.raises(EpubException, match="unsafe path"):
        read_epub_safe(str(archive_path))


def test_epub_rejects_entry_count_limit(tmp_path, monkeypatch):
    archive_path = tmp_path / "many-entries.epub"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("one.txt", "1")
        archive.writestr("two.txt", "2")

    monkeypatch.setattr(settings, "max_epub_entries", 1)
    with pytest.raises(EpubException, match="too many entries"):
        read_epub_safe(str(archive_path))
