from __future__ import annotations

import importlib
import unittest


class ImportTests(unittest.TestCase):
    def test_all_runtime_entrypoints_import(self) -> None:
        modules = [
            "scripts.append_feishu_doc",
            "scripts.doctor",
            "scripts.run_delivery",
            "scripts.run_monitor",
            "scripts.run_top3_daily",
            "scripts.run_econ5_daily",
            "scripts.run_nber_weekly",
            "scripts.run_llm_finance_daily",
            "src.pipeline",
            "src.sources.arxiv",
            "src.sources.semanticscholar",
            "src.sources.ssrn",
            "src.sources.nber",
            "src.sources.top3",
        ]
        for module in modules:
            with self.subTest(module=module):
                importlib.import_module(module)


if __name__ == "__main__":
    unittest.main()
