import contextlib
from io import StringIO
import json
import tempfile
from unittest import TestCase
from unittest.mock import patch
from pathlib import Path

from proactive_coding_orchestrator.cli import main


class CliTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "cli.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args, stdin=""):
        out = StringIO()
        with patch("sys.stdin", StringIO(stdin)), contextlib.redirect_stdout(out):
            result = main(["--db", self.db, *args])
        return result, json.loads(out.getvalue())

    def test_json_event_end_to_end(self):
        payload = json.dumps({"kind": "pr_opened", "subject": "Example PR", "event_key": "42"})
        code, event = self.call("emit-json", stdin=payload)
        self.assertEqual(code, 0)
        self.assertTrue(event["created"])
        _, duplicate = self.call("emit-json", stdin=payload)
        self.assertFalse(duplicate["created"])
        _, proposals = self.call("evaluate")
        self.assertEqual(len(proposals["proposals_created"]), 1)
        _, pending = self.call("list", "--status", "pending")
        self.assertEqual(len(pending), 1)
        proposal_id = pending[0]["id"]
        _, approval = self.call("approve", proposal_id)
        self.assertEqual(approval["status"], "approved")
        _, executed = self.call("run")
        self.assertEqual(executed["status"], "completed")
        self.assertIn("SIMULATED ONLY", executed["output"])

    def test_unknown_and_sensitive_fields_rejected(self):
        code, error = self.call("emit-json", stdin=json.dumps({
            "kind": "pr_opened", "subject": "Hello", "access_token": "not-to-store"
        }))
        self.assertEqual(code, 2)
        self.assertIn("unexpected fields", error["error"])

    def test_malformed_json_rejected(self):
        code, error = self.call("emit-json", stdin="not JSON")
        self.assertEqual(code, 2)
        self.assertIn("JSON", error["error"])
