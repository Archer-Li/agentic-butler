from datetime import timedelta
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from proactive_coding_orchestrator.core import (connect, decide, ingest_event,
    list_proposals, mark_interrupted, mute_kind, propose_pending, run_one,
    stats, timestamp, utc_now)


class EventFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "state.db")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def event(self, *, event_key="id-1", kind="ci_failed"):
        return ingest_event(self.conn, kind=kind, subject="CI job red",
                            source="fixture", event_key=event_key)

    def proposal(self):
        self.event()
        ids = propose_pending(self.conn)
        self.assertEqual(len(ids), 1)
        return ids[0]

    def test_replay_is_idempotent(self):
        e1 = self.event()
        e2 = self.event()
        self.assertEqual(e1, (e1[0], True))
        self.assertEqual(e2, (e1[0], False))
        self.assertEqual(len(propose_pending(self.conn)), 1)
        self.assertEqual(len(propose_pending(self.conn)), 0)
        self.assertEqual(stats(self.conn)["events"], 1)

    def test_approval_required_for_execution(self):
        p = self.proposal()
        self.assertIsNone(run_one(self.conn))
        self.assertEqual(decide(self.conn, p, approve=True), "approved")
        out = run_one(self.conn)
        self.assertEqual(out["status"], "completed")
        self.assertIn("SIMULATED ONLY", out["output"])
        self.assertIsNone(run_one(self.conn))
        self.assertEqual(stats(self.conn)["completed"], 1)

    def test_rejection_cannot_execute(self):
        p = self.proposal()
        self.assertEqual(decide(self.conn, p, approve=False), "rejected")
        self.assertIsNone(run_one(self.conn))
        with self.assertRaises(ValueError):
            decide(self.conn, p, approve=True)

    def test_expired_proposal_cannot_be_approved(self):
        p = self.proposal()
        with self.conn:
            self.conn.execute("UPDATE proposals SET expires_at=? WHERE id=?",
                              (timestamp(utc_now() - timedelta(minutes=1)), p))
        self.assertEqual(decide(self.conn, p, approve=True), "expired")
        self.assertIsNone(run_one(self.conn))

    def test_workflow_tamper_blocks_execution(self):
        p = self.proposal()
        self.assertEqual(decide(self.conn, p, approve=True), "approved")
        with self.conn:
            self.conn.execute("UPDATE proposals SET steps=? WHERE id=?",
                              ('[{"action":"unexpected","scope":"write"}]', p))
        result = run_one(self.conn)
        self.assertEqual(result["status"], "failed_integrity")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0], 0)

    def test_muting_prevents_proposal(self):
        mute_kind(self.conn, "plan_saved", mute=True)
        self.event(kind="plan_saved")
        self.assertEqual(propose_pending(self.conn), [])
        mute_kind(self.conn, "plan_saved", mute=False)
        self.event(event_key="id-2", kind="plan_saved")
        self.assertEqual(len(propose_pending(self.conn)), 1)

    def test_duplicate_runs_not_possible(self):
        p = self.proposal()
        decide(self.conn, p, approve=True)
        self.assertIsNotNone(run_one(self.conn))
        self.assertIsNone(run_one(self.conn))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0], 1)

    def test_crashed_workflow_never_auto_retries(self):
        p = self.proposal()
        decide(self.conn, p, approve=True)
        with self.conn:
            self.conn.execute("UPDATE proposals SET status='running' WHERE id=?", (p,))
        self.assertEqual(mark_interrupted(self.conn), 1)
        self.assertIsNone(run_one(self.conn))
        self.assertEqual(list_proposals(self.conn)[0]["status"], "interrupted")

    def test_invalid_event_rejected(self):
        with self.assertRaises(ValueError):
            ingest_event(self.conn, kind="arbitrary_command", subject="shell")
        with self.assertRaises(ValueError):
            ingest_event(self.conn, kind="ci_failed", subject="")

    def test_concurrent_evaluation_avoids_duplicate_proposals(self):
        self.event()
        other = connect(Path(self.temp.name) / "state.db")
        try:
            self.assertEqual(len(propose_pending(self.conn)), 1)
            self.assertEqual(propose_pending(other), [])
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
