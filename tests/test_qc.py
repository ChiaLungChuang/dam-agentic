"""Detector tests. The eval corpus covers accuracy; these cover contract.

Both matter and they are not the same thing: the eval says whether the detector is
right, these say whether it behaves. A detector that is accurate and crashes on a
malformed file is not usable by an agent.
"""
import json
import subprocess
import sys
from pathlib import Path

QC = Path(__file__).parent.parent / "skills" / "dam-qc" / "scripts" / "validate_dam.py"

# The synthetic corpus comes from the session-scoped `corpus_dir` fixture in
# conftest.py — generated once for the whole run rather than per test.


def run_qc(files, out):
    subprocess.run([sys.executable, str(QC)] + [str(f) for f in files] +
                   ["--out", str(out)], check=True, capture_output=True)
    return json.loads(Path(out).read_text())


def test_emits_valid_report(corpus_dir, tmp_path):
    files = sorted(corpus_dir.glob("Monitor*.txt"))
    r = run_qc(files, tmp_path / "qc.json")
    assert "inventory" in r and "alignment" in r and "monitors" in r


def test_decisions_required_always_present(corpus_dir, tmp_path):
    """The decisions list is the point of the report. It must exist even when empty."""
    r = run_qc(sorted(corpus_dir.glob("Monitor*.txt")), tmp_path / "qc.json")
    assert isinstance(r["decisions_required"], list)


def test_never_silently_repairs(corpus_dir, tmp_path):
    """Alignment is the only permitted transformation, and it must be reported."""
    r = run_qc(sorted(corpus_dir.glob("Monitor*.txt")), tmp_path / "qc.json")
    assert r["alignment"]["final_bin_dropped"] is True
    assert "per_monitor" in r["alignment"]


def test_malformed_file_errors_are_actionable(tmp_path):
    """Error strings are read by a model deciding what to do next.

    A traceback is not an instruction. This test currently only checks that we fail
    without crashing; tighten it once error text is written to spec.
    """
    bad = tmp_path / "Monitor1.txt"
    bad.write_text("this is not a DAM file\n")
    proc = subprocess.run([sys.executable, str(QC), str(bad),
                           "--out", str(tmp_path / "qc.json")],
                          capture_output=True, text=True)
    assert proc.returncode != 0
    assert "monitor" in proc.stderr.lower() or "parse" in proc.stderr.lower()
