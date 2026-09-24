import importlib
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock


def _reload_storage(monkeypatch):
    monkeypatch.setenv("STORAGE_BUCKET", "test-bucket")
    monkeypatch.setenv("STORAGE_PREFIX", "bodhiai")
    monkeypatch.delenv("LOAD_TEST_STUB", raising=False)
    sys.modules.pop("storage", None)
    return importlib.import_module("storage")


def test_storage_preserves_public_paths(monkeypatch):
    storage = _reload_storage(monkeypatch)
    blob = MagicMock()
    blob.content_type = "application/pdf"
    blob.download_as_bytes.return_value = b"pdf"
    bucket = MagicMock()
    bucket.blob.return_value = blob
    client = MagicMock()
    client.bucket.return_value = bucket
    monkeypatch.setattr(storage.gcs, "Client", MagicMock(return_value=client))

    saved = storage.put_object("qpgen/books/a.pdf", b"pdf", "application/pdf")
    assert saved == {
        "path": "qpgen/books/a.pdf",
        "key": "bodhiai/qpgen/books/a.pdf",
        "bucket": "test-bucket",
    }
    blob.upload_from_string.assert_called_once()
    data, content_type = storage.get_object("qpgen/books/a.pdf")
    assert data == b"pdf"
    assert content_type == "application/pdf"


def test_storage_load_stub_needs_no_credentials(monkeypatch):
    monkeypatch.setenv("LOAD_TEST_STUB", "true")
    monkeypatch.delenv("STORAGE_BUCKET", raising=False)
    sys.modules.pop("storage", None)
    storage = importlib.import_module("storage")
    assert storage.init_storage() == "stub-bucket"
    assert storage.put_object("a", b"x", "text/plain")["path"] == "a"
    assert storage.get_object("a")[1] == "application/pdf"


def test_vision_response_text_shape(monkeypatch):
    import pdf_utils

    response = SimpleNamespace(
        error=SimpleNamespace(message=""),
        full_text_annotation=SimpleNamespace(text="line one\nline two\n"),
        text_annotations=[],
    )
    client = MagicMock()
    client.document_text_detection.return_value = response
    monkeypatch.setattr(pdf_utils, "_get_vision_client", lambda: client)
    monkeypatch.setattr(
        "pdf2image.convert_from_bytes",
        lambda *_args, **_kwargs: [SimpleNamespace(save=lambda buf, format: buf.write(b"png"))],
    )
    monkeypatch.setattr(pdf_utils, "_ocr_page_bytes", lambda _img: b"png")
    monkeypatch.setattr("pypdf.PdfReader", lambda *_args, **_kwargs: SimpleNamespace(pages=[1]))

    assert pdf_utils._ocr_pdf(b"pdf", max_pages=1) == "line one\nline two"
    client.document_text_detection.assert_called_once()
