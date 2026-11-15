"""Tests internes Tk hors écran ; aucun clic dans les applications de l'utilisateur."""
import json
from pathlib import Path
import tkinter as tk
import unittest

import app
from test_engine import RUN_ROOT


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.data = RUN_ROOT / self._testMethodName
        self.root = tk.Tk()
        self.root.withdraw()
        self.view = app.App(self.root, self.data)

    def tearDown(self):
        self.root.destroy()

    def test_interface_launches_worker_and_displays_prediction(self):
        self.view.run_button.invoke()
        self.root.update()
        self.assertEqual(self.view.state["effect_count"], 1)
        self.assertEqual(self.view.state["actions"][-1]["state"], "TERMINÉE")
        self.assertIn("juste", self.view.message.get())
        self.assertIn("OBSERVATION_RECORDED", self.view.log.get("1.0", "end"))

    def test_interface_recovers_real_worker_crash_without_second_effect(self):
        self.view.crash.set("Après l'effet, avant le résultat")
        self.view.run_button.invoke()
        self.root.update()
        self.assertEqual(self.view.state["effect_count"], 1)
        self.assertEqual(self.view.state["actions"][-1]["state"], "EN_COURS")
        self.view.resume()
        self.root.update()
        self.assertEqual(self.view.state["effect_count"], 1)
        self.assertEqual(self.view.state["actions"][-1]["state"], "TERMINÉE")
        self.assertIn("RESULT_UNCERTAIN", self.view.log.get("1.0", "end"))

    def test_interface_restart_restores_selection_and_durable_history(self):
        self.view.prediction.set("Lampe éteinte")
        self.view.run_button.invoke()
        self.root.update()
        before = self.view.state
        self.root.destroy()
        self.root = tk.Tk()
        self.root.withdraw()
        self.view = app.App(self.root, self.data)
        self.root.update()
        self.assertEqual(before, self.view.state)
        self.assertIn("fausse", self.view.message.get())
        self.assertEqual(len(self.view.table.selection()), 1)
        report = self.view.worker("report")
        self.assertTrue(Path(report["report"]).is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
