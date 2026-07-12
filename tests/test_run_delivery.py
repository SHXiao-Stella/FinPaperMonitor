from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from scripts import run_delivery


class RunDeliveryTests(unittest.TestCase):
    def test_all_production_schedules_are_declared(self) -> None:
        self.assertEqual(
            {source: spec.schedule for source, spec in run_delivery.SPECS.items()},
            {
                "top3": "0 8 * * *",
                "nber": "10 8 * * 1",
                "llm_finance": "20 8 * * *",
                "econ5": "30 8 * * *",
            },
        )

    def test_nber_uses_iso_week_year(self) -> None:
        spec = run_delivery.SPECS["nber"]
        self.assertEqual(run_delivery.run_suffix(spec, dt.date(2027, 1, 1)), "2026-W53")

    def test_build_commands_uses_the_same_selected_file_for_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generate, archive, commit, selected, rendered, run_key = run_delivery.build_commands(
                run_delivery.SPECS["econ5"],
                dt.date(2026, 7, 12),
                root=root,
                python_executable="python-test",
            )

        self.assertIn("--from-date", generate)
        self.assertEqual(generate[generate.index("--selected-out") + 1], str(selected))
        self.assertEqual(generate[generate.index("--rendered-out") + 1], str(rendered))
        self.assertEqual(archive[archive.index("--input") + 1], str(rendered))
        self.assertEqual(archive[archive.index("--run-key") + 1], run_key)
        self.assertEqual(commit[commit.index("--commit-from-file") + 1], str(selected))

    @staticmethod
    def _write_generated_outputs(command: list[str]) -> None:
        selected = Path(command[command.index("--selected-out") + 1])
        rendered = Path(command[command.index("--rendered-out") + 1])
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_text('{"id":"paper-1"}\n', encoding="utf-8")
        rendered.write_text("1. test paper\n", encoding="utf-8")

    def test_archive_happens_before_commit(self) -> None:
        calls: list[str] = []

        def runner(command, *, step, **kwargs):
            calls.append(step)
            if len(calls) == 1:
                self._write_generated_outputs(list(command))

        with tempfile.TemporaryDirectory() as temp_dir:
            output = run_delivery.execute_delivery(
                run_delivery.SPECS["top3"],
                dt.date(2026, 7, 12),
                root=Path(temp_dir),
                runner=runner,
            )

        self.assertEqual(
            calls,
            ["第一步：生成摘要", "第二步：飞书文档归档", "第三步：提交 pushed state"],
        )
        self.assertEqual(output, "FinTop3\n1. test paper\n")

    def test_archive_failure_prevents_commit(self) -> None:
        calls: list[str] = []

        def runner(command, *, step, **kwargs):
            calls.append(step)
            if len(calls) == 1:
                self._write_generated_outputs(list(command))
            elif len(calls) == 2:
                raise run_delivery.DeliveryStepError(step, "archive failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(run_delivery.DeliveryStepError):
                run_delivery.execute_delivery(
                    run_delivery.SPECS["top3"],
                    dt.date(2026, 7, 12),
                    root=Path(temp_dir),
                    runner=runner,
                )

        self.assertEqual(calls, ["第一步：生成摘要", "第二步：飞书文档归档"])

    def test_prepare_only_does_not_archive_or_commit(self) -> None:
        calls: list[str] = []

        def runner(command, *, step, **kwargs):
            calls.append(step)
            self._write_generated_outputs(list(command))

        with tempfile.TemporaryDirectory() as temp_dir:
            run_delivery.execute_delivery(
                run_delivery.SPECS["llm_finance"],
                dt.date(2026, 7, 12),
                root=Path(temp_dir),
                prepare_only=True,
                runner=runner,
            )

        self.assertEqual(calls, ["第一步：生成摘要"])


if __name__ == "__main__":
    unittest.main()
