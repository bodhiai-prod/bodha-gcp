from io import BytesIO

from PIL import Image
from reportlab.lib.units import mm

from pdf_utils import DIAGRAM_MAX_SIZE_MM, _make_diagram_image


def _png_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_pdf_diagrams_fit_compact_bounds_without_distortion():
    max_extent = DIAGRAM_MAX_SIZE_MM * mm
    for width, height in ((100, 100), (400, 100), (100, 400)):
        image = _make_diagram_image(_png_bytes(width, height))

        assert image is not None
        assert image.drawWidth <= max_extent
        assert image.drawHeight <= max_extent
        assert image.drawWidth * height == image.drawHeight * width
