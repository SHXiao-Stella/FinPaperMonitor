from __future__ import annotations

import unittest

from scripts.run_econ5_daily import render_markdown


class Econ5EmptyTests(unittest.TestCase):
    def test_no_new_papers_renders_nonempty_digest(self) -> None:
        digest = render_markdown([], {})

        self.assertIn("暂无符合条件", digest)
        self.assertTrue(digest.endswith("\n"))
        self.assertTrue(digest.strip())


if __name__ == "__main__":
    unittest.main()
