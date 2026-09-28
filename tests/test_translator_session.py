from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.translator import OpenClawAgentBackend


class TranslatorSessionTests(unittest.TestCase):
    def test_each_monitor_run_uses_its_own_agent_session(self) -> None:
        first = OpenClawAgentBackend(logging.getLogger(__name__))
        second = OpenClawAgentBackend(logging.getLogger(__name__))
        self.assertNotEqual(first.session_id, second.session_id)

        failure = SimpleNamespace(returncode=1, stderr="expected", stdout="")
        with patch("src.translator.run_command", return_value=failure) as run_command:
            for backend in (first, first, second):
                with self.assertRaisesRegex(RuntimeError, "expected"):
                    backend.generate_json("test", "test")

        session_ids = []
        for call in run_command.call_args_list:
            command = call.args[0]
            session_ids.append(command[command.index("--session-id") + 1])
        self.assertEqual(session_ids, [first.session_id, first.session_id, second.session_id])


if __name__ == "__main__":
    unittest.main()
