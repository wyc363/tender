import tempfile
import unittest
from pathlib import Path

import pymupdf

from docling_postprocess.page_image import render_page_images


class PageImageTests(unittest.TestCase):
    def test_render_and_reuse_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "one.pdf"
            document = pymupdf.open()
            document.new_page(width=200, height=300)
            document.save(pdf)
            document.close()
            first = render_page_images(pdf, root / "images", width_px=400)
            second = render_page_images(pdf, root / "images", width_px=400)
        self.assertEqual(first["rendered_this_run"], 1)
        self.assertEqual(second["reused_from_cache"], 1)
        self.assertEqual(first["images"][0]["width_px"], 400)
        self.assertEqual(first["images"][0]["height_px"], 600)
        self.assertEqual(first["total_pixels"], second["total_pixels"])


if __name__ == "__main__":
    unittest.main()
