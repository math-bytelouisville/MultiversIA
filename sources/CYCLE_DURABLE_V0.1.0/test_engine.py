"""Tests de processus et contrôles négatifs ; aucune inférence ni réseau."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

import engine

BASE = Path(__file__).resolve().parent
RUN_ROOT = Path(os.environ.get("MIA_TEST_ROOT", str(BASE / "evidence" / ("tests-" + uuid.uuid4().hex[:12])))).resolve()


class DurableTests(unittest.TestCase):
    def setUp(self):
        self.root = RUN_ROOT / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        self.eng = engine.Engine(self.root)
        with engine.exclusive(self.root):
            self.eng.initialize()

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(BASE / "engine.py"), "--data", str(self.root), *args],
                                capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    def run_cli(self, *args, expected=0):
        return self.cli("run", "--a", "1", "--b", "0", "--prediction", "1", *args, expected=expected)

    def state(self):
        return self.cli("status")["result"]

    def policy(self, **updates):
        path = self.root / "policy.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(updates)
        engine.atomic_json(path, data)

    def assert_refused_without_effect(self):
        state = self.state()
        self.assertEqual(state["effect_count"], 0)
        self.assertEqual(state["actions"][-1]["state"], "REFUSÉE")

    def test_T01_success_and_prediction_precedes_effect(self):
        action = self.run_cli()["result"]
        state = self.state()
        events = [e for e in state["events"] if e["action_id"] == action["action_id"]]
        self.assertEqual([e["type"] for e in events], ["ACTION_PREPARED", "INTENT_RECORDED", "DISPATCH_RECORDED", "OBSERVATION_RECORDED"])
        self.assertTrue(action["detail"]["prediction_correct"])
        with engine.database(self.eng.receipts) as db:
            receipt = json.loads(db.execute("SELECT payload FROM receipts").fetchone()[0])
        self.assertLessEqual(events[0]["at"], events[1]["at"])
        self.assertLessEqual(events[2]["at"], receipt["at"])
        self.assertLessEqual(receipt["at"], events[3]["at"])
        self.assertEqual(events[1]["data"]["request_sha256"], engine.digest(action["request"]))
        self.assertEqual(state["effect_count"], 1)

    def test_T02_false_prediction_is_preserved(self):
        first = self.cli("run", "--a", "1", "--b", "0", "--prediction", "0")["result"]
        before = self.state()["events"]
        self.run_cli()
        after = self.state()
        self.assertEqual(before, after["events"][:len(before)])
        self.assertEqual(first["state"], "TERMINÉE")
        self.assertFalse(first["detail"]["prediction_correct"])
        self.assertFalse(after["actions"][0]["request"]["prediction"])

    def test_T03_process_crash_before_intent(self):
        self.run_cli("--crash", "before_intent", expected=86)
        state = self.state()
        self.assertEqual(state["effect_count"], 0)
        self.assertEqual(state["actions"][0]["state"], "PRÉPARÉE")
        self.assertNotIn("INTENT_RECORDED", [e["type"] for e in state["events"]])
        self.cli("resume", state["actions"][0]["action_id"])
        self.assertEqual(self.state()["effect_count"], 1)

    def test_T04_process_crash_after_intent(self):
        self.run_cli("--crash", "after_intent", expected=86)
        state = self.state()
        self.assertEqual(state["effect_count"], 0)
        self.assertEqual(state["actions"][0]["state"], "INTENTION_ENREGISTRÉE")
        key = state["actions"][0]["action_id"]
        self.cli("resume", key)
        self.cli("resume", key)
        self.assertEqual(self.state()["effect_count"], 1)

    def test_T05_process_crash_after_effect_reconciles_without_replay(self):
        self.run_cli("--crash", "after_effect", expected=86)
        state = self.state()
        self.assertEqual(state["effect_count"], 1)
        self.assertEqual(state["actions"][0]["state"], "EN_COURS")
        receipt_before = self.eng.receipts.read_bytes()
        key = state["actions"][0]["action_id"]
        self.cli("resume", key)
        self.cli("resume", key)
        resumed = self.state()
        self.assertEqual(resumed["effect_count"], 1)
        self.assertEqual(resumed["actions"][0]["state"], "TERMINÉE")
        self.assertEqual(receipt_before, self.eng.receipts.read_bytes())
        self.assertEqual([e["type"] for e in resumed["events"]].count("DISPATCH_RECORDED"), 1)
        self.assertIn("RESULT_UNCERTAIN", [e["type"] for e in resumed["events"]])

    def test_T05b_no_receipt_stays_uncertain_without_effect(self):
        self.run_cli("--crash", "after_dispatch", expected=86)
        key = self.state()["actions"][0]["action_id"]
        self.cli("resume", key)
        before = self.state()
        self.cli("resume", key)
        after = self.state()
        self.assertEqual(after["actions"][0]["state"], "RÉSULTAT_À_VÉRIFIER")
        self.assertEqual(after["effect_count"], 0)
        self.assertEqual(before["events"], after["events"])

    def test_T05c_lost_receipt_after_effect_never_replays(self):
        self.run_cli("--crash", "after_effect", expected=86)
        key = self.state()["actions"][0]["action_id"]
        with engine.database(self.eng.receipts) as db:
            db.execute("DELETE FROM receipts")
            db.commit()
        self.cli("resume", key)
        self.cli("resume", key)
        state = self.state()
        self.assertEqual(state["receipt_count"], 0)
        self.assertEqual(state["actions"][0]["state"], "RÉSULTAT_À_VÉRIFIER")
        self.assertEqual([e["type"] for e in state["events"]].count("DISPATCH_RECORDED"), 1)

    def test_T06_identical_duplicate_has_no_second_effect(self):
        one = self.run_cli("--operation-key", "same-message")["result"]
        before = self.state()
        two = self.run_cli("--operation-key", "same-message")["result"]
        self.assertEqual(one, two)
        self.assertEqual(before, self.state())

    def test_T06b_duplicate_after_crash_reconciles(self):
        self.run_cli("--operation-key", "same-crash", "--crash", "after_effect", expected=86)
        self.run_cli("--operation-key", "same-crash")
        self.assertEqual(self.state()["effect_count"], 1)
        self.assertEqual(len(self.state()["actions"]), 1)

    def test_T06c_conflicting_operation_key_is_rejected(self):
        self.run_cli("--operation-key", "collision")
        before = self.state()
        self.cli("run", "--a", "0", "--b", "0", "--prediction", "0", "--operation-key", "collision", expected=2)
        self.assertEqual(before, self.state())

    def test_T07_invented_evidence_is_refused(self):
        self.run_cli("--claims-json", '[{"evidence_id":"OBS-INVENTED","lamp_on":true}]')
        self.assert_refused_without_effect()

    def test_T07b_real_evidence_wrong_value_is_refused(self):
        first = self.run_cli()["result"]
        claim = [{"evidence_id": first["detail"]["evidence_id"], "lamp_on": False}]
        result = self.run_cli("--claims-json", json.dumps(claim))["result"]
        self.assertEqual(result["state"], "REFUSÉE")
        self.assertEqual(self.state()["effect_count"], 1)

    def test_T07c_real_matching_evidence_is_accepted(self):
        first = self.run_cli()["result"]
        claim = [{"evidence_id": first["detail"]["evidence_id"], "lamp_on": True}]
        result = self.run_cli("--claims-json", json.dumps(claim))["result"]
        self.assertEqual(result["state"], "TERMINÉE")
        self.assertEqual(self.state()["effect_count"], 2)

    def test_T08_tampered_event_stops_without_overwrite(self):
        self.run_cli()
        with engine.database(self.eng.journal) as db:
            db.execute("UPDATE events SET sha256=? WHERE seq=2", ("f" * 64,))
            db.commit()
        raw = self.eng.journal.read_bytes()
        result = self.run_cli(expected=2)
        self.assertEqual(result["error"], "IntegrityError")
        self.assertEqual(raw, self.eng.journal.read_bytes())

    def test_T08b_deleted_tail_detected_by_separate_head(self):
        self.run_cli()
        with engine.database(self.eng.journal) as db:
            db.execute("DELETE FROM events WHERE seq=(SELECT MAX(seq) FROM events)")
            db.commit()
        result = self.cli("status", expected=2)
        self.assertIn("témoin", result["message"])

    def test_T08c_truncated_database_not_reinitialized(self):
        self.eng.journal.write_bytes(self.eng.journal.read_bytes()[:100])
        before = self.eng.journal.read_bytes()
        self.cli("init", expected=2)
        self.assertEqual(before, self.eng.journal.read_bytes())

    def test_T08d_missing_journal_is_not_recreated(self):
        self.eng.journal.unlink()
        self.cli("init", expected=2)
        self.assertFalse(self.eng.journal.exists())

    def test_T08e_receipt_corruption_blocks_resume(self):
        self.run_cli("--crash", "after_effect", expected=86)
        key = self.state()["actions"][0]["action_id"]
        with engine.database(self.eng.receipts) as db:
            db.execute("UPDATE receipts SET sha256=?", ("f" * 64,))
            db.commit()
        before = self.eng.receipts.read_bytes()
        self.cli("resume", key, expected=2)
        self.assertEqual(before, self.eng.receipts.read_bytes())

    def test_T08f_commit_head_gap_stops_conservatively(self):
        head = self.eng.seal.read_bytes()
        self.run_cli()
        self.eng.seal.write_bytes(head)
        self.cli("status", expected=2)
        self.assertEqual(head, self.eng.seal.read_bytes())

    def test_T09_permission_denied_before_effect(self):
        self.policy(allow_effect=False)
        self.run_cli()
        self.assert_refused_without_effect()

    def test_T09b_revoked_permission_on_restart(self):
        self.run_cli("--crash", "after_intent", expected=86)
        key = self.state()["actions"][0]["action_id"]
        self.policy(allow_effect=False)
        self.cli("resume", key)
        self.assert_refused_without_effect()

    def test_T09c_reconciliation_is_read_only_even_after_revocation(self):
        self.run_cli("--crash", "after_effect", expected=86)
        key = self.state()["actions"][0]["action_id"]
        self.policy(allow_effect=False)
        self.cli("resume", key)
        self.assertEqual(self.state()["actions"][0]["state"], "TERMINÉE")
        self.assertEqual(self.state()["effect_count"], 1)

    def test_T10_unqualified_guard_refuses(self):
        self.policy(guard_qualified=False)
        self.run_cli()
        self.assert_refused_without_effect()

    def test_T10b_missing_ram_sensor_refuses_fixture(self):
        with engine.exclusive(self.root), patch("engine.memory_measurement", return_value={"available": False, "source": "TEST_FIXTURE"}):
            result = self.eng.run(True, False, True)
        self.assertEqual(result["state"], "REFUSÉE")
        self.assert_refused_without_effect()

    def test_T10c_high_ram_refuses_fixture(self):
        with engine.exclusive(self.root), patch("engine.memory_measurement", return_value={"available": True, "ram_percent": 91, "source": "TEST_FIXTURE"}):
            result = self.eng.run(True, False, True)
        self.assertEqual(result["state"], "REFUSÉE")
        self.assert_refused_without_effect()

    def test_T10d_action_budget_refuses_second_effect(self):
        self.policy(max_actions=1)
        self.run_cli()
        result = self.run_cli()["result"]
        self.assertEqual(result["state"], "REFUSÉE")
        self.assertEqual(self.state()["effect_count"], 1)

    def test_T10e_storage_budget_refuses(self):
        self.policy(max_data_bytes=1)
        self.run_cli()
        self.assert_refused_without_effect()

    def test_T10f_missing_policy_never_defaults_to_allow(self):
        (self.root / "policy.json").unlink()
        self.run_cli()
        self.assert_refused_without_effect()

    def test_T11_protocols_remain_separate_and_immutable(self):
        self.run_cli()
        before = self.state()
        new_protocol = dict(engine.PROTOCOL, version="lampe-v2")
        with engine.exclusive(self.root):
            self.eng.register_protocol(new_protocol)
            with self.assertRaises(engine.Refusal):
                self.eng.register_protocol(dict(engine.PROTOCOL, world="changed"))
        second = self.run_cli("--protocol", "lampe-v2")["result"]
        after = self.state()
        self.assertEqual(second["request"]["protocol_version"], "lampe-v2")
        self.assertEqual(before["events"], after["events"][:len(before["events"])])
        self.assertEqual(after["protocols"]["lampe-v1"], engine.PROTOCOL)

    def test_receiver_failure_is_documented_without_lamp_effect(self):
        result = self.run_cli("--receiver-failure")["result"]
        self.assertEqual(result["state"], "ÉCHEC_DOCUMENTÉ")
        self.assertEqual(self.state()["effect_count"], 0)
        self.assertEqual(self.state()["receipt_count"], 1)

    def test_one_controller_lock_blocks_another_process(self):
        before = self.state()
        with engine.exclusive(self.root):
            result = self.run_cli(expected=2)
        self.assertEqual(result["error"], "Refusal")
        self.assertEqual(before, self.state())

    def test_two_processes_same_operation_never_double_effect(self):
        args = [sys.executable, str(BASE / "engine.py"), "--data", str(self.root), "run",
                "--a", "1", "--b", "0", "--prediction", "1", "--operation-key", "concurrent"]
        processes = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8") for _ in range(2)]
        results = [(p, p.communicate(timeout=15)) for p in processes]
        self.assertIn(0, [p.returncode for p, _ in results])
        self.assertTrue(all(p.returncode in (0, 2) for p, _ in results))
        state = self.state()
        self.assertEqual(state["effect_count"], 1)
        self.assertEqual(len(state["actions"]), 1)

    def test_strict_json_rejects_duplicate_keys_and_extra_fields(self):
        self.run_cli("--claims-json", '[{"evidence_id":"x","evidence_id":"y","lamp_on":true}]', expected=2)
        self.run_cli("--claims-json", '[{"evidence_id":"x","lamp_on":true,"grant":true}]', expected=2)
        self.run_cli("--claims-json", '[{"evidence_id":"x","lamp_on":1}]', expected=2)
        self.assertEqual(self.state()["effect_count"], 0)
        self.assertEqual(len(self.state()["actions"]), 0)

    def test_export_escapes_content_and_keeps_journal_unchanged(self):
        self.run_cli("--operation-key", '<script>alert("x")</script>')
        before = self.state()
        output = Path(self.cli("report")["result"]["report"])
        content = output.read_text(encoding="utf-8")
        self.assertNotIn('<script>alert("x")</script>', content)
        self.assertIn("&lt;script&gt;", content)
        self.assertEqual(before, self.state())


if __name__ == "__main__":
    unittest.main(verbosity=2)
