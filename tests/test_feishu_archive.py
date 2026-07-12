from __future__ import annotations

import unittest

from scripts.append_feishu_doc import build_archive_markdown, split_markdown_by_size, validate_run_key


class FeishuArchiveTests(unittest.TestCase):
    def test_run_key_must_match_source(self) -> None:
        self.assertEqual(validate_run_key("top3", "top3-2026-07-12"), "2026-07-12")
        with self.assertRaises(RuntimeError):
            validate_run_key("top3", "nber-2026-W28")

    def test_archive_uses_the_exact_body(self) -> None:
        body = "1. title\n中文摘要\n"
        rendered = build_archive_markdown("econ5", "econ5-2026-07-12", body)
        self.assertIn(body.rstrip("\n"), rendered)
        self.assertTrue(rendered.startswith("## Economics Top5 Daily | 2026-07-12"))

    def test_markdown_chunks_preserve_content(self) -> None:
        markdown = "\n".join(f"line {index}" for index in range(20))
        chunks = split_markdown_by_size(markdown, max_chars=35)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("\n".join(chunks), markdown)


if __name__ == "__main__":
    unittest.main()
