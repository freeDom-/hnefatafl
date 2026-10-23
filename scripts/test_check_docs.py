"""Regression checks for documentation failures without project dependencies."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import check_docs


class DocumentationChecks(unittest.TestCase):
    def run_fixture(self, document, target="# Repeated heading\n\n## Repeated heading\n"):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs/README.md").write_text(document)
            (root / "docs/guide.md").write_text(target)
            (root / "docs/image.png").write_bytes(b"fixture")
            names = b"docs/README.md\0docs/guide.md\0"
            output = StringIO()
            with (
                patch.object(check_docs, "ROOT", root),
                patch.object(check_docs.subprocess, "check_output", return_value=names),
                redirect_stdout(output),
                redirect_stderr(output),
            ):
                status = check_docs.main()
            return status, output.getvalue()

    def test_duplicate_headings_multiline_links_and_fenced_examples(self):
        status, output = self.run_fixture(
            "# Index\n[Guide](\n guide.md#repeated-heading-1)\n"
            "![image](image.png)\n```md\n[example](missing.md)\n```\n"
        )
        self.assertEqual(status, 0, output)

    def test_broken_heading_fails(self):
        status, output = self.run_fixture("# Index\n[Guide](guide.md#missing)\n")
        self.assertEqual(status, 1)
        self.assertIn("missing heading anchor", output)

    def test_missing_image_fails(self):
        status, output = self.run_fixture("# Index\n[Guide](guide.md)\n![x](missing.png)\n")
        self.assertEqual(status, 1)
        self.assertIn("missing destination", output)

    def test_reference_and_percent_encoded_heading(self):
        status, output = self.run_fixture(
            "# Index\n[Guide][target]\n[target]: guide.md#caf%C3%A9\n", "# Café\n"
        )
        self.assertEqual(status, 0, output)

    def test_orphaned_document_fails(self):
        status, output = self.run_fixture("# Index\n")
        self.assertEqual(status, 1)
        self.assertIn("unreachable", output)

    def test_unclosed_fence_fails(self):
        status, output = self.run_fixture("# Index\n[Guide](guide.md)\n```\n")
        self.assertEqual(status, 1)
        self.assertIn("unclosed fenced code block", output)


if __name__ == "__main__":
    unittest.main()
