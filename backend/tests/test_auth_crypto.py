from app.auth.passwords import hash_password, verify_password
from app.auth.jwt import create_access_token, decode_access_token
from app.config import get_settings


def test_password_hash_roundtrip(tmp_db_path):
    get_settings.cache_clear()
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip(tmp_db_path):
    get_settings.cache_clear()
    token = create_access_token(user_id="1", username="alice", role="analyst")
    payload = decode_access_token(token)
    assert payload["sub"] == "1"
    assert payload["username"] == "alice"
    assert payload["role"] == "analyst"
