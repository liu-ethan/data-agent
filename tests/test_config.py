from pathlib import Path

from backend.app.config import load_settings


def test_load_example_config(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(Path("config.example.yaml").read_text(), encoding="utf-8")
    s = load_settings(str(p))
    assert s.mysql.port == 3306
    assert s.mysql.reader.user
    assert s.write.max_affected_rows == 100
    assert s.schema_rag.max_gap_rounds == 2
