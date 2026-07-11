"""Unit tests for the ticket hygiene source scanner."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="module")
def scanner() -> Any:
    """Load the source scanner module from the hygiene scripts directory."""
    spec = importlib.util.spec_from_file_location(
        "_source_scanner",
        Path(__file__).resolve().parents[2] / "scripts" / "ticket_hygiene" / "_source_scanner.py",
    )
    assert spec is not None and spec.loader is not None  # defensive: narrows Optional[T] before use
    module = importlib.util.module_from_spec(spec)
    sys.modules["_source_scanner"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    return tmp_path


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_pii_leaks_flags_original_in_log_call(scanner: Any, tmp_project: Path) -> None:
    src = tmp_project / "leak.py"
    write(
        src,
        """
import logging
logger = logging.getLogger(__name__)

def scrub(original: str) -> str:
    logger.warning("scrubbed: %s", original)
    return original
""",
    )
    findings = scanner.scan_pii_leaks([src])
    assert any(f.check == "pii-scan" and "original" in f.message for f in findings)


def test_scan_pii_leaks_allows_length_only(scanner: Any, tmp_project: Path) -> None:
    src = tmp_project / "safe.py"
    write(
        src,
        """
import logging
logger = logging.getLogger(__name__)

def scrub(original: str) -> str:
    original_len = len(original)
    logger.warning("scrubbed length: %d", original_len)
    return original
""",
    )
    findings = scanner.scan_pii_leaks([src])
    assert not findings


def test_scan_v2_residue_flags_text_segment(scanner: Any, tmp_project: Path) -> None:
    src = tmp_project / "residue.py"
    # Build the forbidden name at runtime so this test file itself does not
    # trigger the project-wide v2 residue scan.
    forbidden = "Text" + "Segment"
    write(src, f"class OldThing({forbidden}): ...\n")
    findings = scanner.scan_v2_residue([src])
    expected_fragment = "Text" + "Segment"
    assert any(expected_fragment in f.message for f in findings)


def test_scan_v2_residue_clean_file(scanner: Any, tmp_project: Path) -> None:
    src = tmp_project / "clean.py"
    write(src, "class Segment: ...\n")
    findings = scanner.scan_v2_residue([src])
    assert not findings


def test_scan_source_cli_fails_on_fake_pii_leak(tmp_project: Path) -> None:
    """The full --scan-source gate must exit non-zero when a fake PII leak exists."""
    audit = Path(__file__).resolve().parents[2] / "scripts" / "ticket_hygiene" / "audit_tickets.py"
    src = tmp_project / "clean_paste_lite" / "leak.py"
    write(
        src,
        '''
import logging
logger = logging.getLogger(__name__)

def scrub(original: str) -> str:
    logger.warning("leaked: %s", original)
    return original
''',
    )
    result = subprocess.run(
        [sys.executable, str(audit), "--scan-source"],
        cwd=tmp_project,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    combined = result.stdout + result.stderr
    assert "pii-scan" in combined
    assert "original" in combined


def test_scan_source_cli_passes_on_clean_project(tmp_project: Path) -> None:
    """The full --scan-source gate must exit zero on a project with no violations."""
    audit = Path(__file__).resolve().parents[2] / "scripts" / "ticket_hygiene" / "audit_tickets.py"
    src = tmp_project / "clean_paste_lite" / "safe.py"
    write(src, "class Segment: ...\n")
    result = subprocess.run(
        [sys.executable, str(audit), "--scan-source"],
        cwd=tmp_project,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"expected exit 0, got {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
