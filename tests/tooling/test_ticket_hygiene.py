"""Unit tests for ticket hygiene scanner functions."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ticket_hygiene"))

from typing import Any  # noqa: F401

import ticket as ticket_module  # type: ignore[import-not-found]
from audit_tickets import (  # type: ignore[import-not-found]
    Finding,
    Ticket,
    _check_closed_cross_references,
    _check_duplicate_ids,
    _check_index_row_accuracy,
    _normalise_title,
    _parse_index_rows,
)
from classifier import (  # type: ignore[import-not-found]
    classify_tickets,
)
from show_path import _check_duplicate_collisions  # type: ignore[import-not-found]
from ticket import (  # type: ignore[import-not-found]
    _next_ticket_id,
    cmd_classify,
    cmd_close,
    cmd_close_cleanup,
    cmd_create,
    cmd_deps,
    cmd_find,
    cmd_history,
    cmd_lint,
    cmd_open,
    cmd_rename,
    cmd_show,
    cmd_stats,
)


class TestNormaliseTitle:
    def test_strip_prefix(self) -> None:
        assert _normalise_title("T1154: Fix Scanner") == "Fix Scanner"
        assert _normalise_title("T1: Bootstrap") == "Bootstrap"

    def test_no_prefix_unchanged(self) -> None:
        assert _normalise_title("Fix Scanner") == "Fix Scanner"


class TestParseIndexRows:
    def test_parses_rows(self, tmp_path: Path) -> None:
        index = tmp_path / "TICKET_INDEX.md"
        index.write_text("| EM-0001 | T1: Title | ⬜ |  |\n| EM-0002 | T2: Other | ✅ | **CLOSED 2026-06-03.** |\n")
        import audit_tickets as at
        orig = at.INDEX_FILE
        try:
            at.INDEX_FILE = index
            rows = _parse_index_rows()
            assert "EM-0001" in rows
            assert rows["EM-0001"] == [("T1: Title", "⬜")]
            assert rows["EM-0002"] == [("T2: Other", "✅")]
        finally:
            at.INDEX_FILE = orig

    def test_duplicate_ids(self, tmp_path: Path) -> None:
        index = tmp_path / "TICKET_INDEX.md"
        index.write_text("| EM-0001 | First | ⬜ |  |\n| EM-0001 | Second | ✅ |  |\n")
        import audit_tickets as at
        orig = at.INDEX_FILE
        try:
            at.INDEX_FILE = index
            rows = _parse_index_rows()
            assert rows["EM-0001"] == [("First", "⬜"), ("Second", "✅")]
        finally:
            at.INDEX_FILE = orig


class TestCheckIndexRowAccuracy:
    def test_clean_match(self, tmp_path: Path) -> None:
        ticket = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "title": "T1: Title", "status": "OPEN"},
        )
        findings: list[Finding] = []
        _check_index_row_accuracy([ticket], {"EM-0001": [("T1: Title", "⬜")]}, findings)
        assert len(findings) == 0

    def test_status_drift(self, tmp_path: Path) -> None:
        ticket = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "title": "T1: Title", "status": "CLOSED"},
        )
        findings: list[Finding] = []
        _check_index_row_accuracy([ticket], {"EM-0001": [("T1: Title", "⬜")]}, findings)
        assert len(findings) == 1
        assert findings[0].check == "index_accuracy"
        assert "⬜" in findings[0].message

    def test_title_drift(self, tmp_path: Path) -> None:
        ticket = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "title": "T1: Real Title", "status": "OPEN"},
        )
        findings: list[Finding] = []
        _check_index_row_accuracy([ticket], {"EM-0001": [("T1: Wrong Title", "⬜")]}, findings)
        assert len(findings) == 1
        assert "Real Title" in findings[0].message

    def test_duplicate_id_any_match(self, tmp_path: Path) -> None:
        """If multiple index rows exist for same ID, ANY match passes."""
        ticket = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "title": "T1: Title", "status": "OPEN"},
        )
        findings: list[Finding] = []
        rows = {"EM-0001": [("T1: Other", "✅"), ("T1: Title", "⬜")]}
        _check_index_row_accuracy([ticket], rows, findings)
        assert len(findings) == 0


class TestCheckDuplicateIds:
    def test_no_duplicates(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "a.md", ticket_id="EM-0001", frontmatter={})
        t2 = Ticket(path=tmp_path / "b.md", ticket_id="EM-0002", frontmatter={})
        findings: list[Finding] = []
        _check_duplicate_ids([t1, t2], findings)
        assert len(findings) == 0

    def test_finds_open_involved_duplicates_as_error(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "a.md", ticket_id="EM-0001", frontmatter={"status": "OPEN"})
        t2 = Ticket(path=tmp_path / "b.md", ticket_id="EM-0001", frontmatter={"status": "CLOSED"})
        findings: list[Finding] = []
        _check_duplicate_ids([t1, t2], findings)
        assert len(findings) == 1
        assert findings[0].check == "duplicate_id"
        assert findings[0].ticket_id == "EM-0001"
        assert findings[0].level == "ERROR"
        assert "2 files" in findings[0].message

    def test_closed_only_duplicates_suppressed(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "a.md", ticket_id="EM-0001", frontmatter={"status": "CLOSED"})
        t2 = Ticket(path=tmp_path / "b.md", ticket_id="EM-0001", frontmatter={"status": "WONT_FIX"})
        findings: list[Finding] = []
        _check_duplicate_ids([t1, t2], findings)
        assert len(findings) == 0


class TestCmdFind:
    def test_found(self, tmp_path: Path) -> None:
        t = Ticket(path=tmp_path / "EM-0001.md", ticket_id="EM-0001", frontmatter={"id": "EM-0001"})
        assert cmd_find([t], "EM-0001") == 0

    def test_not_found(self, tmp_path: Path) -> None:
        t = Ticket(path=tmp_path / "EM-0001.md", ticket_id="EM-0001", frontmatter={})
        assert cmd_find([t], "EM-9999") == 1


class TestCmdOpen:
    def test_lists_open_by_epic(self, tmp_path: Path) -> None:
        t1 = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "epic": "EM-E001", "title": "T1: One"},
        )
        t2 = Ticket(
            path=tmp_path / "EM-0002.md",
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "status": "CLOSED", "epic": "EM-E001", "title": "T2: Two"},
        )
        t3 = Ticket(
            path=tmp_path / "EM-0003.md",
            ticket_id="EM-0003",
            frontmatter={"id": "EM-0003", "status": "OPEN", "epic": "EM-E002", "title": "T3: Three"},
        )
        assert cmd_open([t1, t2, t3]) == 0

    def test_no_open(self, tmp_path: Path) -> None:
        t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "CLOSED", "epic": "EM-E001", "title": "T1: One"},
        )
        assert cmd_open([t]) == 0


class TestCmdDeps:
    def test_not_found(self, tmp_path: Path) -> None:
        assert cmd_deps([], "EM-9999") == 1

    def test_no_deps(self, tmp_path: Path) -> None:
        t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "T1", "depends_on": [], "blocks": []},
        )
        assert cmd_deps([t], "EM-0001") == 0

    def test_with_deps(self, tmp_path: Path) -> None:
        t1 = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "T1", "depends_on": ["EM-0002"], "blocks": []},
        )
        t2 = Ticket(
            path=tmp_path / "EM-0002.md",
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "status": "CLOSED", "title": "T2", "depends_on": [], "blocks": []},
        )
        assert cmd_deps([t1, t2], "EM-0001") == 0

    def test_with_blocks(self, tmp_path: Path) -> None:
        t1 = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "T1", "depends_on": [], "blocks": ["EM-0002"]},
        )
        t2 = Ticket(
            path=tmp_path / "EM-0002.md",
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "status": "OPEN", "title": "T2", "depends_on": [], "blocks": []},
        )
        assert cmd_deps([t1, t2], "EM-0001") == 0

    def test_recursive_deps(self, tmp_path: Path) -> None:
        t1 = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "T1", "depends_on": ["EM-0002"], "blocks": []},
        )
        t2 = Ticket(
            path=tmp_path / "EM-0002.md",
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "status": "OPEN", "title": "T2", "depends_on": ["EM-0003"], "blocks": []},
        )
        t3 = Ticket(
            path=tmp_path / "EM-0003.md",
            ticket_id="EM-0003",
            frontmatter={"id": "EM-0003", "status": "CLOSED", "title": "T3", "depends_on": [], "blocks": []},
        )
        assert cmd_deps([t1, t2, t3], "EM-0001") == 0

    def test_unknown_ref(self, tmp_path: Path) -> None:
        t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "T1", "depends_on": ["EM-9999"], "blocks": []},
        )
        assert cmd_deps([t], "EM-0001") == 0


class TestCmdShow:
    def test_show(self, tmp_path: Path) -> None:
        f = tmp_path / "EM-0001.md"
        f.write_text("# Hello\n")
        t = Ticket(path=f, ticket_id="EM-0001", frontmatter={"id": "EM-0001"})
        assert cmd_show([t], "EM-0001") == 0

    def test_not_found(self, tmp_path: Path) -> None:
        assert cmd_show([], "EM-9999") == 1


class TestCmdCreate:
    def test_creates_ticket(self, tmp_path: Path) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        open_dir = epic_dir / "open"
        open_dir.mkdir(parents=True)
        result = cmd_create(
            tickets=[],
            epic_dirs={"EM-E020": epic_dir},
            ticket_id="EM-9999",
            title="Test Ticket",
            epic="EM-E020",
            effort="1h",
            risk="MEDIUM",
            priority=3,
            today="2026-06-03",
        )
        assert result == 0
        created = open_dir / "EM-9999-test-ticket.md"
        assert created.exists()
        text = created.read_text(encoding="utf-8")
        assert "id: EM-9999" in text
        assert 'title: "Test Ticket"' in text
        assert "epic: EM-E020" in text
        assert "status: OPEN" in text
        assert "risk: MEDIUM" in text
        assert "effort: 1h" in text
        assert "priority: 3" in text
        assert "depends_on: []" in text
        assert "blocks: []" in text
        assert "created: 2026-06-03" in text

    def test_blocks_duplicate_id(self, tmp_path: Path) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        open_dir = epic_dir / "open"
        open_dir.mkdir(parents=True)
        existing = Ticket(path=open_dir / "EM-9999.md", ticket_id="EM-9999", frontmatter={"id": "EM-9999"})
        result = cmd_create(
            tickets=[existing],
            epic_dirs={"EM-E020": epic_dir},
            ticket_id="EM-9999",
            title="Duplicate",
            epic="EM-E020",
            effort="1h",
            risk="LOW",
            priority=3,
        )
        assert result == 1

    def test_blocks_invalid_id(self, tmp_path: Path) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        open_dir = epic_dir / "open"
        open_dir.mkdir(parents=True)
        result = cmd_create(
            tickets=[],
            epic_dirs={"EM-E020": epic_dir},
            ticket_id="BAD-ID",
            title="Bad",
            epic="EM-E020",
            effort="1h",
            risk="LOW",
            priority=3,
        )
        assert result == 1

    def test_blocks_unknown_epic(self, tmp_path: Path) -> None:
        result = cmd_create(
            tickets=[],
            epic_dirs={"EM-E020": tmp_path},
            ticket_id="EM-9999",
            title="Bad Epic",
            epic="EM-EXXX",
            effort="1h",
            risk="LOW",
            priority=3,
        )
        assert result == 1

    def test_blocks_missing_open_dir(self, tmp_path: Path) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        epic_dir.mkdir()
        result = cmd_create(
            tickets=[],
            epic_dirs={"EM-E020": epic_dir},
            ticket_id="EM-9999",
            title="No Open Dir",
            epic="EM-E020",
            effort="1h",
            risk="LOW",
            priority=3,
        )
        assert result == 1

    def test_kebab_case(self, tmp_path: Path) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        open_dir = epic_dir / "open"
        open_dir.mkdir(parents=True)
        result = cmd_create(
            tickets=[],
            epic_dirs={"EM-E020": epic_dir},
            ticket_id="EM-9999",
            title="Hello World! Test",
            epic="EM-E020",
            effort="1h",
            risk="LOW",
            priority=3,
            today="2026-06-03",
        )
        assert result == 0
        created = open_dir / "EM-9999-hello-world-test.md"
        assert created.exists()


class TestCmdCloseCleanup:
    def test_not_found(self, tmp_path: Path) -> None:
        assert cmd_close_cleanup([], "EM-9999", write=False) == 1

    def test_no_references(self, tmp_path: Path) -> None:
        closed = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "CLOSED", "closed": "2026-06-03"},
        )
        assert cmd_close_cleanup([closed], "EM-0001", write=False) == 0

    def test_dry_run_lists_references(self, tmp_path: Path) -> None:
        closed = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "CLOSED", "closed": "2026-06-03"},
        )
        ref = Ticket(
            path=tmp_path / "EM-0002.md",
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "status": "OPEN", "depends_on": ["EM-0001"], "blocks": []},
        )
        assert cmd_close_cleanup([closed, ref], "EM-0001", write=False) == 0

    def test_write_removes_from_depends_on(self, tmp_path: Path) -> None:
        closed = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "CLOSED", "closed": "2026-06-03"},
        )
        f = tmp_path / "EM-0002.md"
        f.write_text(
            "---\nid: EM-0002\nstatus: OPEN\ndepends_on: [EM-0001]\nblocks: []\ncreated: 2026-06-03\n---\n\n# Body\n",
            encoding="utf-8",
        )
        ref = Ticket(
            path=f,
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "status": "OPEN", "depends_on": ["EM-0001"], "blocks": []},
        )
        assert cmd_close_cleanup([closed, ref], "EM-0001", write=True) == 0
        text = f.read_text(encoding="utf-8")
        assert "depends_on: []" in text
        assert "EM-1159 closed" not in text
        assert "EM-0001 closed 2026-06-03" in text

    def test_write_removes_from_blocks(self, tmp_path: Path) -> None:
        closed = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "CLOSED", "closed": "2026-06-03"},
        )
        f = tmp_path / "EM-0002.md"
        f.write_text(
            "---\nid: EM-0002\nstatus: OPEN\ndepends_on: []\nblocks: [EM-0001]\ncreated: 2026-06-03\n---\n\n# Body\n",
            encoding="utf-8",
        )
        ref = Ticket(
            path=f,
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "status": "OPEN", "depends_on": [], "blocks": ["EM-0001"]},
        )
        assert cmd_close_cleanup([closed, ref], "EM-0001", write=True) == 0
        text = f.read_text(encoding="utf-8")
        assert "blocks: []" in text
        assert "EM-0001 closed 2026-06-03" in text

    def test_write_appends_before_retro(self, tmp_path: Path) -> None:
        closed = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "CLOSED", "closed": "2026-06-03"},
        )
        f = tmp_path / "EM-0002.md"
        f.write_text(
            "---\nid: EM-0002\nstatus: OPEN\ndepends_on: [EM-0001]\nblocks: []\ncreated: 2026-06-03\n---\n\n# Body\n\n## Retro\n",
            encoding="utf-8",
        )
        ref = Ticket(
            path=f,
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "status": "OPEN", "depends_on": ["EM-0001"], "blocks": []},
        )
        assert cmd_close_cleanup([closed, ref], "EM-0001", write=True) == 0
        text = f.read_text(encoding="utf-8")
        assert text.index("Unblocked.") < text.index("## Retro")


class TestCmdHistory:
    def test_not_found(self, tmp_path: Path) -> None:
        assert cmd_history([], "EM-9999") == 1

    def test_not_tracked_by_git(self, tmp_path: Path) -> None:
        f = tmp_path / "EM-0001.md"
        f.write_text("# test\n")
        t = Ticket(path=f, ticket_id="EM-0001", frontmatter={"id": "EM-0001"})
        assert cmd_history([t], "EM-0001") == 1

    def test_shows_commits(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = tmp_path / "EM-0001.md"
        f.write_text("# test\n")
        t = Ticket(path=f, ticket_id="EM-0001", frontmatter={"id": "EM-0001"})

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            class Result:
                returncode = 0
                stdout = "2026-06-03  abc1234  Fix thing\n2026-06-02  def5678  Create ticket\n"
                stderr = ""
            if cmd[1] == "ls-files":
                return Result()
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)
        assert cmd_history([t], "EM-0001") == 0

    def test_oneline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = tmp_path / "EM-0001.md"
        f.write_text("# test\n")
        t = Ticket(path=f, ticket_id="EM-0001", frontmatter={"id": "EM-0001"})


        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            class Result:
                returncode = 0
                stdout = "abc1234 Fix thing\n"
                stderr = ""
            if cmd[1] == "ls-files":
                return Result()
            # Check format string
            if "--format=%h %s" in cmd:
                return Result()
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)
        assert cmd_history([t], "EM-0001", oneline=True) == 0


class TestCmdLint:
    def test_runs_scanner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            class Result:
                returncode = 0
                stdout = "Audit passed\n"
                stderr = ""
            return Result()
        monkeypatch.setattr("subprocess.run", fake_run)
        assert cmd_lint(new=False) == 0

    def test_new_with_no_previous(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import ticket as t
        orig = t._LAST_RUN_FILE
        try:
            t._LAST_RUN_FILE = tmp_path / ".ticket_hygiene_last_run"
            def fake_run(cmd: list[str], **kwargs: Any) -> Any:
                class Result:
                    returncode = 0
                    stdout = "Audit passed\n"
                    stderr = ""
                return Result()
            monkeypatch.setattr("subprocess.run", fake_run)
            assert cmd_lint(new=True) == 0
            assert t._LAST_RUN_FILE.exists()
        finally:
            t._LAST_RUN_FILE = orig

    def test_new_shows_only_new(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import ticket as t
        orig = t._LAST_RUN_FILE
        try:
            t._LAST_RUN_FILE = tmp_path / ".ticket_hygiene_last_run"
            t._LAST_RUN_FILE.write_text("old finding\n", encoding="utf-8")
            def fake_run(cmd: list[str], **kwargs: Any) -> Any:
                class Result:
                    returncode = 1
                    stdout = "old finding\nnew finding\n"
                    stderr = ""
                return Result()
            monkeypatch.setattr("subprocess.run", fake_run)
            assert cmd_lint(new=True) == 1
        finally:
            t._LAST_RUN_FILE = orig


class TestCmdStats:
    def test_basic(self, tmp_path: Path) -> None:
        t1 = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "epic": "EM-E001", "effort": "2h"},
        )
        t2 = Ticket(
            path=tmp_path / "EM-0002.md",
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "status": "CLOSED", "epic": "EM-E001", "effort": "4h", "closed": "2026-06-01"},
        )
        assert cmd_stats([t1, t2]) == 0

    def test_scoped_to_epic(self, tmp_path: Path) -> None:
        t1 = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "epic": "EM-E001", "effort": "2h"},
        )
        t2 = Ticket(
            path=tmp_path / "EM-0002.md",
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "status": "OPEN", "epic": "EM-E002", "effort": "4h"},
        )
        assert cmd_stats([t1, t2], epic="EM-E001") == 0

    def test_ignores_na_effort(self, tmp_path: Path) -> None:
        t1 = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "epic": "EM-E001", "effort": "N/A"},
        )
        assert cmd_stats([t1]) == 0


class TestNextTicketId:
    def test_finds_next(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "EM-0001.md", ticket_id="EM-0001", frontmatter={})
        t2 = Ticket(path=tmp_path / "EM-0005.md", ticket_id="EM-0005", frontmatter={})
        assert _next_ticket_id([t1, t2]) == "EM-0006"

    def test_ignores_suffixes(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "EM-0125-A.md", ticket_id="EM-0125-A", frontmatter={})
        assert _next_ticket_id([t1]) == "EM-0126"

    def test_ignores_epic_ids(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "EM-E019.md", ticket_id="EM-E019", frontmatter={})
        assert _next_ticket_id([t1]) == "EM-0001"


class TestCmdCreateAutoId:
    def test_auto_id(self, tmp_path: Path) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        open_dir = epic_dir / "open"
        open_dir.mkdir(parents=True)
        existing = Ticket(
            path=open_dir / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001"},
        )
        result = cmd_create(
            tickets=[existing],
            epic_dirs={"EM-E020": epic_dir},
            ticket_id=None,
            title="Auto Test",
            epic="EM-E020",
            effort="1h",
            risk="LOW",
            priority=3,
            auto_id=True,
        )
        assert result == 0
        created = open_dir / "EM-0002-auto-test.md"
        assert created.exists()

    def test_dry_run(self, tmp_path: Path) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        open_dir = epic_dir / "open"
        open_dir.mkdir(parents=True)
        result = cmd_create(
            tickets=[],
            epic_dirs={"EM-E020": epic_dir},
            ticket_id="EM-0001",
            title="Dry Run",
            epic="EM-E020",
            effort="1h",
            risk="LOW",
            priority=3,
            dry_run=True,
        )
        assert result == 0
        assert not (open_dir / "EM-0001-dry-run.md").exists()


class TestNextTicketIdEpicSequence:
    def test_finds_next_within_epic(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "EM-0001.md", ticket_id="EM-0001", frontmatter={"epic": "EM-E020"})
        t2 = Ticket(path=tmp_path / "EM-0005.md", ticket_id="EM-0005", frontmatter={"epic": "EM-E020"})
        t_other = Ticket(path=tmp_path / "EM-0010.md", ticket_id="EM-0010", frontmatter={"epic": "EM-E019"})
        assert _next_ticket_id([t1, t2, t_other], epic="EM-E020") == "EM-0006"

    def test_ignores_suffixes_within_epic(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "EM-0125-A.md", ticket_id="EM-0125-A", frontmatter={"epic": "EM-E020"})
        assert _next_ticket_id([t1], epic="EM-E020") == "EM-0126"

    def test_falls_back_to_global_when_no_epic_tickets(self, tmp_path: Path) -> None:
        t_other = Ticket(path=tmp_path / "EM-0010.md", ticket_id="EM-0010", frontmatter={"epic": "EM-E019"})
        assert _next_ticket_id([t_other], epic="EM-E020") == "EM-0001"

    def test_fallback_uses_global_max(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "EM-0003.md", ticket_id="EM-0003", frontmatter={"epic": "EM-E019"})
        t2 = Ticket(path=tmp_path / "EM-0005.md", ticket_id="EM-0005", frontmatter={"epic": "EM-E020"})
        assert _next_ticket_id([t1, t2], epic="EM-E020") == "EM-0006"


class TestCmdCreateEpicSequence:
    def test_epic_sequence_scopes_id(self, tmp_path: Path) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        open_dir = epic_dir / "open"
        open_dir.mkdir(parents=True)
        existing_same = Ticket(
            path=open_dir / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "epic": "EM-E020"},
        )
        existing_other = Ticket(
            path=tmp_path / "EM-0005.md",
            ticket_id="EM-0005",
            frontmatter={"id": "EM-0005", "epic": "EM-E019"},
        )
        result = cmd_create(
            tickets=[existing_same, existing_other],
            epic_dirs={"EM-E020": epic_dir},
            ticket_id=None,
            title="Epic Seq",
            epic="EM-E020",
            effort="1h",
            risk="LOW",
            priority=3,
            auto_id=True,
            epic_sequence=True,
        )
        assert result == 0
        created = open_dir / "EM-0002-epic-seq.md"
        assert created.exists()


class TestCmdCreateCollision:
    def test_rejects_existing_ticket_id(self, tmp_path: Path) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        open_dir = epic_dir / "open"
        open_dir.mkdir(parents=True)
        existing = Ticket(
            path=open_dir / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001"},
        )
        result = cmd_create(
            tickets=[existing],
            epic_dirs={"EM-E020": epic_dir},
            ticket_id="EM-0001",
            title="Collision",
            epic="EM-E020",
            effort="1h",
            risk="LOW",
            priority=3,
        )
        assert result == 1

    def test_auto_id_detects_collision(self, tmp_path: Path) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        open_dir = epic_dir / "open"
        open_dir.mkdir(parents=True)
        existing = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001"},
        )
        result = cmd_create(
            tickets=[existing],
            epic_dirs={"EM-E020": epic_dir},
            ticket_id=None,
            title="Collision Auto",
            epic="EM-E020",
            effort="1h",
            risk="LOW",
            priority=3,
            auto_id=True,
        )
        assert result == 0
        created = open_dir / "EM-0002-collision-auto.md"
        assert created.exists()


class TestCheckClosedCrossReferences:
    def test_stale_ref_in_closed_ticket(self, tmp_path: Path) -> None:
        closed = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "CLOSED", "depends_on": ["EM-9999"], "blocks": []},
        )
        findings: list[Finding] = []
        _check_closed_cross_references([closed], {}, findings)
        assert len(findings) == 1
        assert findings[0].level == "WARNING"
        assert findings[0].check == "closed_cross_ref"
        assert "EM-9999" in findings[0].message

    def test_ignores_open_tickets(self, tmp_path: Path) -> None:
        open_t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "depends_on": ["EM-9999"], "blocks": []},
        )
        findings: list[Finding] = []
        _check_closed_cross_references([open_t], {}, findings)
        assert len(findings) == 0


class TestShowPathCollisionGuard:
    def test_no_collisions(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "a.md", ticket_id="EM-0001", frontmatter={"status": "OPEN"})
        t2 = Ticket(path=tmp_path / "b.md", ticket_id="EM-0002", frontmatter={"status": "OPEN"})
        assert _check_duplicate_collisions([t1, t2]) == []

    def test_open_involved_collision(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "a.md", ticket_id="EM-0001", frontmatter={"status": "OPEN"})
        t2 = Ticket(path=tmp_path / "b.md", ticket_id="EM-0001", frontmatter={"status": "CLOSED"})
        collisions = _check_duplicate_collisions([t1, t2])
        assert len(collisions) == 1
        assert collisions[0][0] == "EM-0001"

    def test_closed_only_collision_suppressed(self, tmp_path: Path) -> None:
        t1 = Ticket(path=tmp_path / "a.md", ticket_id="EM-0001", frontmatter={"status": "CLOSED"})
        t2 = Ticket(path=tmp_path / "b.md", ticket_id="EM-0001", frontmatter={"status": "WONT_FIX"})
        assert _check_duplicate_collisions([t1, t2]) == []


class TestClassifier:
    def test_active_ready(self, tmp_path: Path) -> None:
        t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "Do thing", "depends_on": [], "blocks": []},
        )
        t.path.write_text("---\nid: EM-0001\n---\n\n# Body\n\n## Acceptance Criteria\n\n- [ ] one\n")
        results = classify_tickets([t])
        assert len(results) == 1
        assert results[0].category == "1 — Active / Legitimate"
        assert results[0].subcategory == "ready-to-work"

    def test_stale_missing_ac(self, tmp_path: Path) -> None:
        t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "Do thing", "depends_on": [], "blocks": []},
        )
        t.path.write_text("---\nid: EM-0001\n---\n\n# Body\n\nNo checkboxes here.\n")
        results = classify_tickets([t])
        assert results[0].category == "2 — Stale / Needs Review"
        assert results[0].subcategory == "missing-acceptance-criteria"

    def test_stale_deleted_component(self, tmp_path: Path) -> None:
        t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "Do thing", "depends_on": [], "blocks": []},
        )
        t.path.write_text("---\nid: EM-0001\n---\n\nUse replacement_map.py.\n\n## Acceptance Criteria\n\n- [ ] one\n")
        results = classify_tickets([t])
        assert results[0].category == "2 — Stale / Needs Review"
        assert results[0].subcategory == "references-deleted-component"

    def test_likely_zombie_wontfix(self, tmp_path: Path) -> None:
        wontfix = Ticket(
            path=tmp_path / "EM-0002.md",
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "status": "WONT_FIX"},
        )
        t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "Do thing", "depends_on": ["EM-0002"], "blocks": []},
        )
        t.path.write_text("---\nid: EM-0001\n---\n\n# Body\n\n## Acceptance Criteria\n\n- [ ] one\n")
        results = classify_tickets([t, wontfix])
        assert results[0].category == "3 — Likely Zombie"
        assert results[0].subcategory == "blocked-by-wontfix"

    def test_epic_root_forces_active(self, tmp_path: Path) -> None:
        t = Ticket(
            path=tmp_path / "EM-E020.md",
            ticket_id="EM-E020",
            frontmatter={"id": "EM-E020", "status": "OPEN", "title": "Epic"},
        )
        t.path.write_text("---\nid: EM-E020\n---\n\n# Epic\n")
        results = classify_tickets([t])
        assert results[0].category == "1 — Active / Legitimate"
        assert results[0].subcategory == "epic-root"


class TestCmdClassify:
    def test_json_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "Do thing"},
        )
        t.path.write_text("---\nid: EM-0001\n---\n\n# Body\n\n## Acceptance Criteria\n\n- [ ] one\n")
        assert cmd_classify([t], json_out=True) == 0
        captured = capsys.readouterr()
        assert '"1 \u2014 Active / Legitimate"' in captured.out or "Active / Legitimate" in captured.out


class TestCmdRename:
    def test_dry_run(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        open_dir = epic_dir / "open"
        open_dir.mkdir(parents=True)
        old_file = open_dir / "EM-0001-old-title.md"
        old_file.write_text(
            "---\nid: EM-0001\ntitle: Old Title\n---\n\n# EM-0001 — Old Title\n\nDepends on EM-9999.\n",
            encoding="utf-8",
        )
        old = Ticket(path=old_file, ticket_id="EM-0001", frontmatter={"id": "EM-0001"})
        result = cmd_rename([old], {}, "EM-0001", "EM-9999", write=False)
        assert result == 0
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out
        assert "EM-0001-old-title.md" in captured.out
        assert old_file.exists()

    def test_write_renames_and_updates_refs(self, tmp_path: Path) -> None:
        epic_dir = tmp_path / "EM-E020-process"
        open_dir = epic_dir / "open"
        open_dir.mkdir(parents=True)

        old_file = open_dir / "EM-0001-old-title.md"
        old_file.write_text(
            "---\nid: EM-0001\ntitle: Old Title\n---\n\n# EM-0001 — Old Title\n",
            encoding="utf-8",
        )
        other_file = open_dir / "EM-0002-other.md"
        other_file.write_text(
            "---\nid: EM-0002\ntitle: Other\ndepends_on: [EM-0001]\nblocks: []\n---\n\n# Other\n",
            encoding="utf-8",
        )

        old = Ticket(path=old_file, ticket_id="EM-0001", frontmatter={"id": "EM-0001", "title": "Old Title"})
        other = Ticket(
            path=other_file,
            ticket_id="EM-0002",
            frontmatter={"id": "EM-0002", "title": "Other", "depends_on": ["EM-0001"], "blocks": []},
        )

        # Patch _regenerate_index to avoid subprocess in temp dir
        import ticket as ticket_mod
        original_regenerate = ticket_mod._regenerate_index
        ticket_mod._regenerate_index = lambda: None
        try:
            result = cmd_rename([old, other], {"EM-E020": epic_dir}, "EM-0001", "EM-9999", write=True)
        finally:
            ticket_mod._regenerate_index = original_regenerate

        assert result == 0
        assert not old_file.exists()
        new_file = open_dir / "EM-9999-old-title.md"
        assert new_file.exists()
        assert "id: EM-9999" in new_file.read_text(encoding="utf-8")
        other_text = other_file.read_text(encoding="utf-8")
        assert "depends_on: [EM-9999]" in other_text
        assert "EM-0001" not in other_text

    def test_rejects_existing_id(self, tmp_path: Path) -> None:
        old_file = tmp_path / "EM-0001.md"
        old_file.write_text("---\nid: EM-0001\n---\n")
        existing_file = tmp_path / "EM-9999.md"
        existing_file.write_text("---\nid: EM-9999\n---\n")
        old = Ticket(path=old_file, ticket_id="EM-0001", frontmatter={"id": "EM-0001"})
        existing = Ticket(path=existing_file, ticket_id="EM-9999", frontmatter={"id": "EM-9999"})
        assert cmd_rename([old, existing], {}, "EM-0001", "EM-9999", write=False) == 1


class TestLintJson:
    def test_lint_json_clean(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import ticket as t
        orig = t._LAST_RUN_FILE
        try:
            t._LAST_RUN_FILE = tmp_path / ".ticket_hygiene_last_run"

            def fake_run(cmd: list[str], **kwargs: Any) -> Any:
                class Result:
                    returncode = 0
                    stdout = "[]"
                    stderr = ""
                return Result()

            monkeypatch.setattr("subprocess.run", fake_run)
            assert cmd_lint(json_out=True) == 0
        finally:
            t._LAST_RUN_FILE = orig


class TestAuditJson:
    def test_audit_json_clean(self, tmp_path: Path) -> None:
        import audit_tickets as at
        # Minimal happy-path ticket
        t = Ticket(path=tmp_path / "EM-0001.md", ticket_id="EM-0001", frontmatter={
            "id": "EM-0001",
            "title": "T",
            "epic": "EM-E020",
            "status": "OPEN",
            "risk": "LOW",
            "effort": "1h",
            "depends_on": [],
            "blocks": [],
            "created": "2026-06-14",
            "priority": 3,
        })
        t.path.write_text("---\nid: EM-0001\ntitle: T\nepic: EM-E020\nstatus: OPEN\nrisk: LOW\neffort: 1h\ndepends_on: []\nblocks: []\ncreated: 2026-06-14\npriority: 3\n---\n\n# T\n\n## Acceptance Criteria\n\n- [ ] one\n")
        # Suppress index checks by pointing to a temp index
        orig_index = at.INDEX_FILE
        try:
            at.INDEX_FILE = tmp_path / "TICKET_INDEX.md"
            findings: list[Finding] = []
            epic_dirs = {"EM-E020": tmp_path}
            at._check_required_fields(t, findings)
            at._check_cross_references([t], epic_dirs, findings)
            assert all(f.level != "ERROR" for f in findings)
        finally:
            at.INDEX_FILE = orig_index


class TestClassifierRulesYaml:
    def test_load_default_rules(self, tmp_path: Path) -> None:
        import classifier as clf
        rules = clf._load_rules()
        assert "deleted_components" in rules
        assert "__main__.py" in rules["deleted_components"]

    def test_load_custom_rules(self, tmp_path: Path) -> None:
        import classifier as clf
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            'schema_version: "2.0"\ndeleted_components: ["foo.py"]\nplaceholder_words: ["spike"]\ndrift_verbs: ["change"]\nlocked_phrases: ["Pipeline"]\n',
            encoding="utf-8",
        )
        rules = clf._load_rules(rules_file)
        assert rules["schema_version"] == "2.0"
        assert rules["deleted_components"] == ["foo.py"]


class TestDocsDrift:
    def test_detects_drift(self, tmp_path: Path) -> None:
        import classifier as clf
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            'schema_version: "1.0"\ndeleted_components: []\nplaceholder_words: []\ndrift_verbs: ["change"]\nlocked_phrases: ["Token Format"]\n',
            encoding="utf-8",
        )
        t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "Drifty"},
        )
        t.path.write_text(
            "---\nid: EM-0001\nstatus: OPEN\ntitle: Drifty\n---\n\n# Body\n\nWe should change the Token Format.\n\n## Acceptance Criteria\n\n- [ ] one\n",
            encoding="utf-8",
        )
        findings = clf.docs_drift_findings([t], rules_path=rules_file)
        assert len(findings) == 1
        assert findings[0].ticket_id == "EM-0001"
        assert "Token Format" in findings[0].phrases

    def test_ignores_closed_tickets(self, tmp_path: Path) -> None:
        import classifier as clf
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            'schema_version: "1.0"\ndeleted_components: []\nplaceholder_words: []\ndrift_verbs: ["change"]\nlocked_phrases: ["Token Format"]\n',
            encoding="utf-8",
        )
        t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "CLOSED", "title": "Drifty"},
        )
        t.path.write_text(
            "---\nid: EM-0001\nstatus: CLOSED\ntitle: Drifty\n---\n\n# Body\n\nWe should change the Token Format.\n",
            encoding="utf-8",
        )
        findings = clf.docs_drift_findings([t], rules_path=rules_file)
        assert len(findings) == 0


class TestJsonSchemaVersion:
    def test_classify_json_has_schema_version(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        t = Ticket(
            path=tmp_path / "EM-0001.md",
            ticket_id="EM-0001",
            frontmatter={"id": "EM-0001", "status": "OPEN", "title": "Do thing"},
        )
        t.path.write_text("---\nid: EM-0001\n---\n\n# Body\n\n## Acceptance Criteria\n\n- [ ] one\n")
        assert cmd_classify([t], json_out=True) == 0
        captured = capsys.readouterr()
        assert '"schema_version": "1.0"' in captured.out
        assert '"categories"' in captured.out

    def test_lint_json_has_schema_version(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import ticket as t
        orig = t._LAST_RUN_FILE
        try:
            t._LAST_RUN_FILE = tmp_path / ".ticket_hygiene_last_run"

            def fake_run(cmd: list[str], **kwargs: Any) -> Any:
                class Result:
                    returncode = 0
                    stdout = '{"schema_version": "1.0", "findings": []}'
                    stderr = ""
                return Result()

            monkeypatch.setattr("subprocess.run", fake_run)
            assert cmd_lint(json_out=True) == 0
        finally:
            t._LAST_RUN_FILE = orig


class TestClose:
    """Unit tests for the atomic ticket close command."""

    @pytest.fixture
    def ticket_dir(self, tmp_path: Path) -> Path:
        epic = tmp_path / "EM-E020"
        (epic / "open").mkdir(parents=True)
        (epic / "closed").mkdir(parents=True)
        return epic

    def _make_ticket(
        self,
        ticket_dir: Path,
        tid: str = "EM-1762",
        status: str = "OPEN",
        location: str = "open",
        retro: bool = True,
    ) -> Ticket:
        target_dir = ticket_dir / location
        path = target_dir / f"{tid}-title.md"
        retro_section = ""
        if retro:
            retro_section = "\n## Retro\n\n### If I Had 3 More Hours\nNothing.\n"
        body = (
            f"# {tid}\n\n**Status:** {status}\n\n"
            "## Acceptance Criteria\n\n- [ ] one\n"
            f"{retro_section}"
        )
        text = (
            "---\n"
            f"id: {tid}\n"
            "title: Title\n"
            "epic: EM-E020\n"
            f"status: {status}\n"
            "risk: LOW\n"
            "effort: 2h\n"
            "priority: 2\n"
            "depends_on: []\n"
            "blocks: []\n"
            "created: 2026-06-23\n"
            "---\n\n"
            f"{body}"
        )
        path.write_text(text, encoding="utf-8")
        fm = {
            "id": tid,
            "title": "Title",
            "epic": "EM-E020",
            "status": status,
            "risk": "LOW",
            "effort": "2h",
            "priority": 2,
            "depends_on": [],
            "blocks": [],
            "created": "2026-06-23",
        }
        return Ticket(path=path, ticket_id=tid, frontmatter=fm)

    def _patch_side_effects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ticket_module, "_regenerate_index", lambda: None)

        class FakeResult:
            returncode = 0
            stdout = "clean"
            stderr = ""

        monkeypatch.setattr(ticket_module.subprocess, "run", lambda *args, **kwargs: FakeResult())

    def test_close_happy_path(
        self, ticket_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_side_effects(monkeypatch)
        ticket = self._make_ticket(ticket_dir, retro=True)
        epic_dirs = {"EM-E020": ticket_dir}

        assert cmd_close([ticket], epic_dirs, "EM-1762", date="2026-06-23") == 0

        open_path = ticket_dir / "open" / "EM-1762-title.md"
        closed_path = ticket_dir / "closed" / "EM-1762-title.md"
        assert not open_path.exists()
        assert closed_path.exists()

        closed_text = closed_path.read_text(encoding="utf-8")
        assert "status: CLOSED" in closed_text
        assert "closed: 2026-06-23" in closed_text
        assert "**Status:** CLOSED" in closed_text

    def test_close_dry_run_does_not_write(
        self, ticket_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_side_effects(monkeypatch)
        ticket = self._make_ticket(ticket_dir, retro=True)
        epic_dirs = {"EM-E020": ticket_dir}
        original_text = ticket.path.read_text(encoding="utf-8")

        assert cmd_close([ticket], epic_dirs, "EM-1762", dry_run=True) == 0

        assert ticket.path.exists()
        assert ticket.path.read_text(encoding="utf-8") == original_text
        assert not (ticket_dir / "closed" / "EM-1762-title.md").exists()

    def test_close_missing_retro_fails(
        self, ticket_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_side_effects(monkeypatch)
        ticket = self._make_ticket(ticket_dir, retro=False)
        epic_dirs = {"EM-E020": ticket_dir}

        assert cmd_close([ticket], epic_dirs, "EM-1762", date="2026-06-23") == 1
        assert ticket.path.exists()
        assert "status: OPEN" in ticket.path.read_text(encoding="utf-8")

    def test_close_force_overrides_retro_check(
        self, ticket_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_side_effects(monkeypatch)
        ticket = self._make_ticket(ticket_dir, retro=False)
        epic_dirs = {"EM-E020": ticket_dir}

        assert cmd_close([ticket], epic_dirs, "EM-1762", date="2026-06-23", force=True) == 0
        assert not ticket.path.exists()
        assert (ticket_dir / "closed" / "EM-1762-title.md").exists()

    def test_close_idempotent_already_closed(
        self, ticket_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_side_effects(monkeypatch)
        ticket = self._make_ticket(ticket_dir, status="CLOSED", location="closed", retro=True)
        epic_dirs = {"EM-E020": ticket_dir}

        assert cmd_close([ticket], epic_dirs, "EM-1762", date="2026-06-23") == 0
        assert (ticket_dir / "closed" / "EM-1762-title.md").exists()
        assert not (ticket_dir / "open" / "EM-1762-title.md").exists()

    def test_close_refuses_epic_root(
        self, ticket_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_side_effects(monkeypatch)
        path = ticket_dir / "open" / "EM-E020-epic.md"
        path.write_text("---\nid: EM-E020\n---\n\n# Epic\n", encoding="utf-8")
        ticket = Ticket(path=path, ticket_id="EM-E020", frontmatter={"id": "EM-E020", "status": "OPEN"})
        epic_dirs = {"EM-E020": ticket_dir}

        assert cmd_close([ticket], epic_dirs, "EM-E020") == 1
        assert path.exists()

    def test_close_updates_affected_tickets(
        self, ticket_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_side_effects(monkeypatch)
        closed_ticket = self._make_ticket(ticket_dir, tid="EM-1762", retro=True)

        blocker_path = ticket_dir / "open" / "EM-1763-blocked.md"
        blocker_path.write_text(
            "---\n"
            "id: EM-1763\n"
            "title: Blocked\n"
            "epic: EM-E020\n"
            "status: OPEN\n"
            "risk: LOW\n"
            "effort: 1h\n"
            "priority: 3\n"
            "depends_on: [EM-1762]\n"
            "blocks: []\n"
            "created: 2026-06-23\n"
            "---\n\n# Blocked\n\n## Retro\n\n### If I Had 3 More Hours\nNothing.\n",
            encoding="utf-8",
        )
        blocker = Ticket(
            path=blocker_path,
            ticket_id="EM-1763",
            frontmatter={
                "id": "EM-1763",
                "title": "Blocked",
                "epic": "EM-E020",
                "status": "OPEN",
                "risk": "LOW",
                "effort": "1h",
                "priority": 3,
                "depends_on": ["EM-1762"],
                "blocks": [],
                "created": "2026-06-23",
            },
        )
        epic_dirs = {"EM-E020": ticket_dir}

        assert cmd_close([closed_ticket, blocker], epic_dirs, "EM-1762", date="2026-06-23") == 0

        blocker_text = blocker_path.read_text(encoding="utf-8")
        assert "depends_on: []" in blocker_text
        assert "EM-1762 closed 2026-06-23. Unblocked." in blocker_text
