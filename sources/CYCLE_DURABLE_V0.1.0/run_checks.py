"""Exécute la suite et archive résultats, empreintes et dossiers de preuve."""
import hashlib
import json
from pathlib import Path
import time
import unittest

import engine
import test_engine
import test_app


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def addSuccess(self, test):
        self.records.append({"test": test.id(), "status": "PASS", "seconds": round(time.monotonic() - self.started, 3)})
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.records.append({"test": test.id(), "status": "FAIL", "error": self._exc_info_to_string(err, test)})
        super().addFailure(test, err)

    def addError(self, test, err):
        self.records.append({"test": test.id(), "status": "ERROR", "error": self._exc_info_to_string(err, test)})
        super().addError(test, err)


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_engine)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(test_app))
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    base = Path(__file__).resolve().parent
    evidence = base / "evidence"
    evidence.mkdir(exist_ok=True)
    report = {"at": engine.utc(), "scope": "Processus Python locaux et simulateur SQLite. Aucun modèle, réseau, Lean ou test électrique.",
              "status": "PASS" if result.wasSuccessful() else "FAIL", "tests_run": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors), "test_data": str(test_engine.RUN_ROOT),
              "sources": {name: hashlib.sha256((base / name).read_bytes()).hexdigest()
                          for name in ("engine.py", "app.py", "test_engine.py", "test_app.py", "run_checks.py")},
              "limitations": ["T12 Lean différé : hors périmètre du prototype sans C#.",
                              "Les cas capteur absent et RAM élevée utilisent des fixtures explicites.",
                              "Les tests de processus utilisent le capteur RAM réel Windows.",
                              "Les arrêts os._exit ne simulent pas une coupure électrique.",
                              "Le reçu et l'effet sont atomiques uniquement dans le simulateur."],
              "tests": result.records}
    engine.atomic_json(evidence / "test-results.json", report)
    print("\nPreuves :", evidence / "test-results.json")
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
