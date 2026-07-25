from pathlib import Path

import pytest
import yaml


@pytest.fixture()
def tmp_db_path(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "llm": {"api_key": "", "base_url": "", "model": ""},
                "backend": {
                    "host": "127.0.0.1",
                    "port": 8000,
                    "jwt_secret": "test-secret",
                    "admin_invite_code": "test-invite",
                    "database_path": str(db_file),
                    "cors_origins": ["http://localhost:5173"],
                },
                "frontend": {
                    "port": 5173,
                    "api_base_url": "http://127.0.0.1:8000",
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_CONFIG", str(config_path))
    from app.config import get_settings

    get_settings.cache_clear()
    yield db_file
    get_settings.cache_clear()
