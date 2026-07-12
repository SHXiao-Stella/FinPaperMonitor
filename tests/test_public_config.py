from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class PublicConfigTests(unittest.TestCase):
    def test_llm_config_has_no_local_target_or_absolute_cache(self) -> None:
        payload = yaml.safe_load((ROOT / "config" / "llm_finance_daily.yml").read_text(encoding="utf-8"))
        self.assertEqual(payload["notification"]["target"], "")
        cache_dir = payload["source_settings"]["nber"]["cache_dir"]
        self.assertFalse(Path(cache_dir).is_absolute())

    def test_only_gitkeep_is_tracked_under_data(self) -> None:
        completed = subprocess.run(
            ["git", "ls-files", "data"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(completed.stdout.splitlines(), ["data/.gitkeep"])

    def test_public_files_have_no_server_home_path(self) -> None:
        private_prefix = "/" + "home" + "/ubuntu"
        for path in [ROOT / "config", ROOT / "deploy", ROOT / "docs", ROOT / "paper_monitor", ROOT / "scripts", ROOT / "src"]:
            for file_path in path.rglob("*"):
                if not file_path.is_file() or file_path.suffix not in {".py", ".md", ".txt", ".yml", ".sh"}:
                    continue
                self.assertNotIn(private_prefix, file_path.read_text(encoding="utf-8"), str(file_path))


if __name__ == "__main__":
    unittest.main()
