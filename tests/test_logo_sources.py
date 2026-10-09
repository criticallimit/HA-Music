"""Verify build-time imports without contacting the logo host."""
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ha_music"))
import logo_sources


class LogoImportTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT)))
        self.stack.enter_context(patch.object(logo_sources, "TARGET", self.folder))
        self.stack.enter_context(patch.object(logo_sources, "ASSETS", {"test":"Test.svg"}))
        self.request = self.stack.enter_context(patch.object(logo_sources, "urlopen"))

    def import_svg(self, inner):
        content = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                   '<!-- imported original artwork -->' + inner + '</svg>').encode()
        self.request.return_value.__enter__.return_value.read.return_value = content
        logo_sources.install()
        return content

    def test_valid_local_fragment_is_imported(self):
        content = self.import_svg('<path id="a" d="M0 0h10v10Z"/><use href="#a"/>')
        self.assertEqual((self.folder / "test.svg").read_bytes(), content)
        self.request.assert_called_once()

    def test_executable_elements_are_rejected_before_overwriting(self):
        existing = self.folder / "test.svg"
        existing.write_bytes(b"previous good logo")
        for element in ('<script>alert(1)</script>', '<foreignObject><div/></foreignObject>'):
            with self.subTest(element=element), self.assertRaises(ValueError):
                self.import_svg(element)
            self.assertEqual(existing.read_bytes(), b"previous good logo")

    def test_event_handlers_are_rejected(self):
        with self.assertRaises(ValueError):
            self.import_svg('<path onload="alert(1)" d="M0 0h10v10Z"/>')
        self.assertFalse((self.folder / "test.svg").exists())

    def test_non_fragment_references_are_rejected(self):
        for link in ("https://example.com/logo", " HTTP://example.com/logo", "javascript:alert(1)",
                     "data:image/svg+xml,external", "file:///etc/passwd", "//example.com/logo", "other.svg#a"):
            with self.subTest(link=link), self.assertRaises(ValueError):
                self.import_svg('<use href="' + link + '"/>')

    def test_wrong_document_is_rejected(self):
        self.request.return_value.__enter__.return_value.read.return_value = b'<html>' + b' ' * 200 + b'</html>'
        with self.assertRaises(ValueError):
            logo_sources.install()
        self.assertFalse((self.folder / "test.svg").exists())

    def test_oversize_download_is_bounded_and_rejected(self):
        stream = self.request.return_value.__enter__.return_value
        stream.read.return_value = b' ' * 512001
        with self.assertRaises(ValueError):
            logo_sources.install()
        stream.read.assert_called_once_with(512001)


if __name__ == "__main__":
    unittest.main()

