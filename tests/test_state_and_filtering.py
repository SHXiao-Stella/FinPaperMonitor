from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from paper_monitor.filtering import filter_papers
from paper_monitor.state import RecordState


class StateAndFilteringTests(unittest.TestCase):
    def test_record_state_round_trip_and_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "pushed.json"
            state = RecordState(str(path))
            state.add_many(["doi:1", "doi:1", "", "N/A", "doi:2"])
            self.assertEqual(state.load(), {"doi:1", "doi:2"})

    def test_filter_applies_type_date_include_and_exclude(self) -> None:
        papers = [
            {
                "id": "keep",
                "source": "top_journal",
                "type": "journal-article",
                "title": "Asset pricing with language models",
                "abstract": "Evidence from markets",
                "published": "2026-01-02",
                "year": 2026,
            },
            {
                "id": "excluded",
                "source": "top_journal",
                "type": "journal-article",
                "title": "Asset pricing corrigendum",
                "abstract": "",
                "published": "2026-01-02",
                "year": 2026,
            },
            {
                "id": "old",
                "source": "top_journal",
                "type": "journal-article",
                "title": "Asset pricing",
                "abstract": "",
                "published": "2019-12-31",
                "year": 2019,
            },
            {
                "id": "wrong-type",
                "source": "top_journal",
                "type": "editorial",
                "title": "Asset pricing",
                "abstract": "",
                "published": "2026-01-02",
                "year": 2026,
            },
        ]

        selected = filter_papers(
            papers,
            include_keywords=["asset pricing"],
            exclude_keywords=["corrigendum"],
            min_date="2020-01-01",
        )
        self.assertEqual([paper["id"] for paper in selected], ["keep"])


if __name__ == "__main__":
    unittest.main()
