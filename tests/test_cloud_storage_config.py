from backend.store import LocalApiRecordStore, build_api_record_store
from src.config import get_settings


def test_settings_parse_cloud_backends(monkeypatch, tmp_path):
    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HELPMATE_STATE_STORE_BACKEND", "supabase")
    monkeypatch.setenv("HELPMATE_VECTOR_STORE_BACKEND", "chroma_http")
    monkeypatch.setenv("HELPMATE_CHROMA_HTTP_HOST", "cloud.example.com")
    monkeypatch.setenv("HELPMATE_CHROMA_HTTP_PORT", "443")
    monkeypatch.setenv("HELPMATE_CHROMA_HTTP_SSL", "true")
    monkeypatch.setenv("HELPMATE_CHROMA_HTTP_HEADERS", "Authorization=Bearer token,X-Test=value")
    monkeypatch.delenv("HELPMATE_CHROMA_API_KEY", raising=False)
    monkeypatch.delenv("CHROMA_API_KEY", raising=False)

    settings = get_settings()

    assert settings.uses_supabase_state is True
    assert settings.uses_chroma_http is True
    assert settings.chroma_http_host == "cloud.example.com"
    assert settings.chroma_http_port == 443
    assert settings.chroma_http_ssl is True
    assert settings.chroma_http_headers == {
        "Authorization": "Bearer token",
        "X-Test": "value",
    }
    assert settings.chroma_api_key == "token"


def test_settings_parse_chroma_api_key_from_header(monkeypatch, tmp_path):
    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HELPMATE_CHROMA_HTTP_HEADERS", "x-chroma-token=secret-token")
    monkeypatch.delenv("HELPMATE_CHROMA_API_KEY", raising=False)
    monkeypatch.delenv("CHROMA_API_KEY", raising=False)

    settings = get_settings()

    assert settings.chroma_api_key == "secret-token"


def test_build_api_record_store_defaults_to_local(monkeypatch, tmp_path):
    monkeypatch.setenv("HELPMATE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HELPMATE_STATE_STORE_BACKEND", raising=False)

    settings = get_settings()
    store = build_api_record_store(settings)

    assert isinstance(store, LocalApiRecordStore)
