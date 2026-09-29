#!/usr/bin/env python3
"""Make one billable Vertex AI Gemini image request as an explicit preflight.

Unlike check_gcp.py this is intentionally not part of the standard preflight:
it verifies both Model Garden access and the VM/operator's ADC permissions by
asking the configured image model to return a tiny exam-style diagram.
"""
from __future__ import annotations

import os
import sys


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> int:
    from google import genai
    from google.genai import types

    project = require("GCP_PROJECT_ID")
    model = os.environ.get("VERTEX_IMAGE_MODEL", "gemini-3.1-flash-lite-image")
    location = os.environ.get("VERTEX_IMAGE_LOCATION", "global")
    client = genai.Client(vertexai=True, project=project, location=location)
    response = client.models.generate_content(
        model=model,
        contents=(
            "Generate a simple black-and-white 2D line diagram of a labelled "
            "triangle ABC on a white background. Return an image."
        ),
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            candidate_count=1,
            image_config=types.ImageConfig(aspect_ratio="1:1"),
        ),
    )
    for candidate in response.candidates or []:
        for part in candidate.content.parts or []:
            inline_data = getattr(part, "inline_data", None)
            if inline_data and inline_data.data:
                print(
                    f"OK Vertex AI image model={model} location={location} "
                    f"bytes={len(inline_data.data)} mime={inline_data.mime_type}"
                )
                return 0
    raise RuntimeError("Vertex AI returned no image part")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - preflight should print provider errors
        print(f"FAIL Vertex AI image preflight: {exc}", file=sys.stderr)
        raise SystemExit(1)
