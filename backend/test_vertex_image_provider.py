import importlib
import io
import sys
import types as stdlib_types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image


class _Config:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _install_fake_genai(monkeypatch):
    google = stdlib_types.ModuleType("google")
    genai = stdlib_types.ModuleType("google.genai")
    types = stdlib_types.ModuleType("google.genai.types")
    types.GenerateContentConfig = _Config
    types.ImageConfig = _Config
    types.HttpOptions = _Config
    genai.types = types
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", types)


def _reload_llm(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "bodha-ai-prod")
    monkeypatch.setenv("IMAGE_PROVIDER", "vertex")
    sys.modules.pop("llm_adapter", None)
    return importlib.import_module("llm_adapter")


def _image_response(data=b"\x89PNG\r\n\x1a\nimage", *, include_text=True):
    parts = []
    if include_text:
        parts.append(SimpleNamespace(text="Here is your diagram.", inline_data=None))
    parts.append(
        SimpleNamespace(
            text=None,
            inline_data=SimpleNamespace(data=data, mime_type="image/png"),
        )
    )
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))],
        usage_metadata=SimpleNamespace(candidates_token_count=12, total_token_count=25),
    )


def test_vertex_request_and_usage(monkeypatch):
    _install_fake_genai(monkeypatch)
    llm = _reload_llm(monkeypatch)
    client = MagicMock()
    client.models.generate_content.return_value = _image_response()
    monkeypatch.setattr(llm, "_vertex_client", lambda: client)
    usage = []
    llm.set_usage_sinks(on_image=lambda *args, **kwargs: usage.append((args, kwargs)))

    assert llm._generate_image_vertex("draw a triangle").startswith(b"\x89PNG")
    call = client.models.generate_content.call_args.kwargs
    assert call["model"] == "gemini-3.1-flash-lite-image"
    assert call["contents"] == "draw a triangle"
    assert call["config"].response_modalities == ["TEXT", "IMAGE"]
    assert call["config"].image_config.aspect_ratio == "1:1"
    assert len(usage) == 1
    args, kwargs = usage[0]
    assert args == ("gemini-3.1-flash-lite-image",)
    assert kwargs["images"] == 1
    assert kwargs["output_tokens"] == 12
    assert kwargs["total_tokens"] == 25
    assert kwargs["latency_ms"] >= 0


def test_vertex_client_uses_project_and_global_location(monkeypatch):
    calls = []
    google = stdlib_types.ModuleType("google")
    genai = stdlib_types.ModuleType("google.genai")
    genai.Client = lambda **kwargs: calls.append(kwargs) or object()
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    llm = _reload_llm(monkeypatch)

    llm._vertex_client()
    assert calls == [{"vertexai": True, "project": "bodha-ai-prod", "location": "global"}]


def test_vertex_parses_image_after_text_and_normalizes_jpeg(monkeypatch):
    _install_fake_genai(monkeypatch)
    llm = _reload_llm(monkeypatch)
    jpeg = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(jpeg, format="JPEG")
    client = MagicMock()
    client.models.generate_content.return_value = _image_response(jpeg.getvalue())
    monkeypatch.setattr(llm, "_vertex_client", lambda: client)

    assert llm._generate_image_vertex("draw")[:8] == b"\x89PNG\r\n\x1a\n"


def test_vertex_no_image_is_best_effort_none(monkeypatch):
    _install_fake_genai(monkeypatch)
    llm = _reload_llm(monkeypatch)
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="blocked", inline_data=None)]))]
    )
    monkeypatch.setattr(llm, "_vertex_client", lambda: client)

    assert llm._generate_image_vertex("draw") is None


def test_vertex_retries_transient_error_but_not_permission_error(monkeypatch):
    _install_fake_genai(monkeypatch)
    llm = _reload_llm(monkeypatch)
    monkeypatch.setattr(llm.time, "sleep", lambda _seconds: None)
    transient = RuntimeError("temporary")
    transient.status_code = 429
    client = MagicMock()
    client.models.generate_content.side_effect = [transient, _image_response()]
    monkeypatch.setattr(llm, "_vertex_client", lambda: client)
    assert llm._generate_image_vertex("draw").startswith(b"\x89PNG")
    assert client.models.generate_content.call_count == 2

    denied = RuntimeError("forbidden")
    denied.status_code = 403
    client.models.generate_content.reset_mock(side_effect=True)
    client.models.generate_content.side_effect = denied
    with pytest.raises(RuntimeError, match="model access"):
        llm._generate_image_vertex("draw")
    assert client.models.generate_content.call_count == 1


def test_vertex_dispatch_timeout_and_prompt_keep_gemini_behavior(monkeypatch):
    _install_fake_genai(monkeypatch)
    llm = _reload_llm(monkeypatch)
    monkeypatch.setattr(llm, "_generate_image_vertex", lambda prompt: b"vertex")

    assert llm._generate_image_sync("draw") == b"vertex"
    assert llm._diagram_timeout() == llm.VERTEX_TIMEOUT + 30.0
    prompt = llm._diagram_prompt({"figure": "a leaf", "labels": ["xylem"]})
    assert "Print these labels and no others" in prompt
