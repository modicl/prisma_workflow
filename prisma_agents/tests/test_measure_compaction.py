import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from eval.measure_compaction import read_input_file


def test_reads_file_within_base(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hola mundo", encoding="utf-8")
    assert read_input_file("x.txt", base=tmp_path) == "hola mundo"


def test_rejects_parent_traversal(tmp_path):
    (tmp_path.parent / "secret.txt").write_text("secreto", encoding="utf-8")
    with pytest.raises(SystemExit):
        read_input_file("../secret.txt", base=tmp_path)


def test_rejects_absolute_path_outside_base(tmp_path):
    with pytest.raises(SystemExit):
        read_input_file("/etc/passwd", base=tmp_path)


def test_rejects_non_file(tmp_path):
    with pytest.raises(SystemExit):
        read_input_file("no_existe.txt", base=tmp_path)
