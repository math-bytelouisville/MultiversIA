"""Prototype local. Le simulateur et le contrôleur ont deux bases distinctes."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import uuid

VERSION = "0.1.0"
GENESIS = "0" * 64
PROTOCOL = {"version": "lampe-v1", "world": "lampe-ab-v1", "prediction": "boolean-before-effect"}
DEFAULT_POLICY = {"allow_effect": True, "scope": "lampe-ab-v1", "max_actions": 100,
                  "max_data_bytes": 20_000_000, "min_free_bytes": 64_000_000,
                  "max_ram_percent": 90, "guard_qualified": True}
TERMINAL = {"TERMINÉE", "ÉCHEC_DOCUMENTÉ", "REFUSÉE"}
CRASH_EXIT = 86


class Refusal(Exception):
    pass


class IntegrityError(Exception):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Clé JSON dupliquée : " + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError("Constante JSON interdite : " + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def atomic_json(path, value):
    temp = path.with_name(path.name + ".pending")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


@contextmanager
def exclusive(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "controller.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Refusal("Un autre contrôleur utilise déjà ce dossier.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def database(path, create=False):
    if not create and not path.is_file():
        raise IntegrityError("Base absente : " + path.name)
    connection = sqlite3.connect(path.as_uri() + ("?mode=rwc" if create else "?mode=rw"), uri=True, timeout=2)
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA journal_mode=DELETE")
    try:
        yield connection
    finally:
        connection.close()


def memory_measurement():
    """Mesure réelle Windows ; aucune qualification CPU/GPU revendiquée."""
    if os.name != "nt":
        return {"available": False, "reason": "Capteur RAM qualifié uniquement pour ce prototype Windows."}
    import ctypes
    from ctypes import wintypes
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong) for name in
            ("total_phys", "avail_phys", "total_page", "avail_page", "total_virtual", "avail_virtual", "extended")]
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"available": False, "reason": "GlobalMemoryStatusEx a échoué."}
    return {"available": True, "ram_percent": int(status.load), "ram_available_bytes": status.avail_phys,
            "source": "Windows GlobalMemoryStatusEx", "measured_at": utc()}


class Engine:
    """L'appelant doit détenir exclusive(root) pour toute opération."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.journal = self.root / "journal.sqlite3"
        self.receipts = self.root / "receipts.sqlite3"
        self.seal = self.root / "journal-head.json"

    def initialize(self):
        existing = {p.name for p in self.root.iterdir()} - {"controller.lock"}
        if existing:
            self.verify()
            return
        atomic_json(self.root / "installation.json", {"schema": "mia-local-1", "created_at": utc()})
        with database(self.journal, create=True) as db:
            db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL)")
            db.commit()
        with database(self.receipts, create=True) as db:
            db.execute("CREATE TABLE receipts (operation_key TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL)")
            db.commit()
        atomic_json(self.seal, {"seq": 0, "sha256": GENESIS})
        atomic_json(self.root / "policy.json", DEFAULT_POLICY)
        self.append("PROTOCOL_REGISTERED", None, PROTOCOL)

    def events(self):
        try:
            installation = strict_json((self.root / "installation.json").read_text(encoding="utf-8"))
            if installation.get("schema") != "mia-local-1":
                raise IntegrityError("Version du dossier inconnue.")
            seal = strict_json(self.seal.read_text(encoding="utf-8"))
            with database(self.journal) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise IntegrityError("SQLite signale un journal endommagé.")
                rows = db.execute("SELECT seq,payload,sha256 FROM events ORDER BY seq").fetchall()
            previous, events = GENESIS, []
            for expected, (seq, raw, hashed) in enumerate(rows, 1):
                event = strict_json(raw)
                if (set(event) != {"schema", "seq", "at", "type", "action_id", "data", "previous_sha256"}
                        or event["schema"] != "mia-event-1" or seq != expected or event["seq"] != seq
                        or event["previous_sha256"] != previous or digest(event) != hashed
                        or canonical(event) != raw):
                    raise IntegrityError("Chaîne du journal invalide à l'événement " + str(seq))
                events.append(dict(event, sha256=hashed))
                previous = hashed
            if seal != {"seq": len(events), "sha256": previous}:
                raise IntegrityError("Le journal et son témoin de fin diffèrent. Arrêt sans réparation automatique.")
            return events
        except (OSError, ValueError, TypeError, KeyError, sqlite3.DatabaseError) as exc:
            raise IntegrityError("Journal illisible : " + str(exc)) from exc

    def append(self, kind, action_id, data):
        events = self.events()
        event = {"schema": "mia-event-1", "seq": len(events) + 1, "at": utc(), "type": kind,
                 "action_id": action_id, "data": data,
                 "previous_sha256": events[-1]["sha256"] if events else GENESIS}
        hashed = digest(event)
        with database(self.journal) as db:
            db.execute("INSERT INTO events VALUES (?,?,?)", (event["seq"], canonical(event), hashed))
            db.commit()
        # Deux supports : une coupure dans cet intervalle entraîne un arrêt conservateur.
        atomic_json(self.seal, {"seq": event["seq"], "sha256": hashed})
        return event

    def projection(self, events=None):
        actions, protocols, evidence = {}, {}, {}
        transitions = {
            "INTENT_RECORDED": ({"PRÉPARÉE"}, "INTENTION_ENREGISTRÉE"),
            "DISPATCH_RECORDED": ({"INTENTION_ENREGISTRÉE"}, "EN_COURS"),
            "RESULT_UNCERTAIN": ({"EN_COURS", "RÉSULTAT_À_VÉRIFIER"}, "RÉSULTAT_À_VÉRIFIER"),
            "REFUSED": ({"PRÉPARÉE", "INTENTION_ENREGISTRÉE", "EN_COURS"}, "REFUSÉE"),
            "OBSERVATION_RECORDED": ({"EN_COURS", "RÉSULTAT_À_VÉRIFIER"}, "TERMINÉE"),
            "FAILURE_RECORDED": ({"EN_COURS", "RÉSULTAT_À_VÉRIFIER"}, "ÉCHEC_DOCUMENTÉ"),
        }
        for event in self.events() if events is None else events:
            kind, key, data = event["type"], event["action_id"], event["data"]
            if kind == "PROTOCOL_REGISTERED":
                if key is not None or data["version"] in protocols:
                    raise IntegrityError("Protocole réécrit.")
                protocols[data["version"]] = data
            elif kind == "ACTION_PREPARED":
                if key in actions or data["protocol_version"] not in protocols:
                    raise IntegrityError("Action dupliquée ou protocole inconnu.")
                if any(a["request"]["operation_key"] == data["operation_key"] for a in actions.values()):
                    raise IntegrityError("Clé d'opération dupliquée.")
                actions[key] = {"action_id": key, "request": data, "state": "PRÉPARÉE", "created_at": event["at"]}
            elif kind in transitions:
                allowed, target = transitions[kind]
                if key not in actions or actions[key]["state"] not in allowed:
                    raise IntegrityError("Transition d'état invalide : " + kind)
                actions[key].update(state=target, detail=data, updated_at=event["at"])
                if kind == "OBSERVATION_RECORDED":
                    evidence[data["evidence_id"]] = data
            else:
                raise IntegrityError("Type d'événement inconnu : " + str(kind))
        return actions, protocols, evidence

    def verify(self):
        events = self.events()
        actions, protocols, evidence = self.projection(events)
        try:
            with database(self.receipts) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise IntegrityError("Base des reçus endommagée.")
                rows = db.execute("SELECT operation_key,payload,sha256 FROM receipts").fetchall()
            known = {a["request"]["operation_key"]: a for a in actions.values()}
            for key, raw, hashed in rows:
                receipt = strict_json(raw)
                if key not in known:
                    raise IntegrityError("Reçu sans action connue.")
                self.check_receipt(known[key], receipt, hashed)
                if canonical(receipt) != raw:
                    raise IntegrityError("Octets du reçu non canoniques.")
                if known[key]["state"] in {"PRÉPARÉE", "INTENTION_ENREGISTRÉE", "REFUSÉE"}:
                    raise IntegrityError("Effet sans envoi autorisé dans le journal.")
            for action in actions.values():
                if action["state"] in {"TERMINÉE", "ÉCHEC_DOCUMENTÉ"}:
                    receipt = self.receipt(action)
                    if receipt is None or action["detail"]["receipt_sha256"] != digest(receipt):
                        raise IntegrityError("Preuve finale absente ou différente du reçu.")
                    if action["state"] == "TERMINÉE":
                        if (receipt["status"] != "EFFECT_DONE" or action["detail"]["lamp_on"] != receipt["lamp_on"]
                                or action["detail"]["prediction_correct"] != (action["request"]["prediction"] == receipt["lamp_on"])):
                            raise IntegrityError("Observation incohérente avec son reçu.")
                    elif receipt["status"] != "FAILED":
                        raise IntegrityError("Échec incohérent avec son reçu.")
        except (sqlite3.DatabaseError, ValueError, KeyError, TypeError) as exc:
            raise IntegrityError("Preuves invalides : " + str(exc)) from exc
        return events, actions, protocols, evidence

    def register_protocol(self, protocol):
        _, _, versions, _ = self.verify()
        if set(protocol) != set(PROTOCOL) or protocol["world"] != PROTOCOL["world"] or protocol["prediction"] != PROTOCOL["prediction"]:
            raise Refusal("Ce prototype accepte seulement le protocole lampe booléen.")
        if not isinstance(protocol["version"], str) or not 1 <= len(protocol["version"]) <= 80:
            raise Refusal("Version de protocole invalide.")
        if protocol["version"] in versions:
            if versions[protocol["version"]] != protocol:
                raise Refusal("Une version existante est immuable.")
            return
        self.append("PROTOCOL_REGISTERED", None, protocol)

    def gate(self, action, actions):
        measurements = {"at": utc()}
        try:
            policy = strict_json((self.root / "policy.json").read_text(encoding="utf-8"))
            if set(policy) != set(DEFAULT_POLICY) or type(policy["allow_effect"]) is not bool or type(policy["guard_qualified"]) is not bool:
                return "Politique invalide.", measurements
            for name in ("max_actions", "max_data_bytes", "min_free_bytes", "max_ram_percent"):
                if type(policy[name]) is not int or policy[name] <= 0:
                    return "Borne de politique invalide : " + name, measurements
            measurements["policy_sha256"] = digest(policy)
            if not policy["allow_effect"] or policy["scope"] != PROTOCOL["world"]:
                return "Permission refusée pour cette expérience.", measurements
            if not policy["guard_qualified"]:
                return "Garde local non qualifié.", measurements
            if len(actions) > policy["max_actions"]:
                return "Budget d'actions épuisé.", measurements
            measurements["data_bytes"] = sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())
            measurements["disk_free_bytes"] = shutil.disk_usage(self.root).free
            if measurements["data_bytes"] >= policy["max_data_bytes"]:
                return "Budget de stockage atteint.", measurements
            if measurements["disk_free_bytes"] < policy["min_free_bytes"]:
                return "Espace libre insuffisant.", measurements
            measurements["memory"] = memory_measurement()
            if not measurements["memory"]["available"]:
                return "Capteur RAM indisponible.", measurements
            if measurements["memory"]["ram_percent"] >= min(90, policy["max_ram_percent"]):
                return "Seuil de mémoire atteint.", measurements
            _, _, evidence = self.projection()
            for claim in action["request"]["claims"]:
                item = evidence.get(claim["evidence_id"])
                if item is None or item["lamp_on"] != claim["lamp_on"]:
                    return "Référence d'observation absente ou valeur incorrecte.", measurements
            return None, measurements
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return "Contrôle impossible : " + str(exc), measurements

    def prepare(self, a, b, prediction, operation_key=None, protocol_version="lampe-v1", claims=None, receiver_failure=False):
        _, actions, protocols, _ = self.verify()
        if any(type(value) is not bool for value in (a, b, prediction, receiver_failure)):
            raise Refusal("A, B et la prédiction doivent être des booléens.")
        if protocol_version not in protocols:
            raise Refusal("Version de protocole inconnue.")
        if claims is None:
            claims = []
        if not isinstance(claims, list) or len(claims) > 20:
            raise Refusal("Liste de références invalide.")
        for claim in claims:
            if (not isinstance(claim, dict) or set(claim) != {"evidence_id", "lamp_on"}
                    or not isinstance(claim["evidence_id"], str) or len(claim["evidence_id"]) > 100
                    or type(claim["lamp_on"]) is not bool):
                raise Refusal("Format de référence invalide.")
        key = operation_key if operation_key is not None else "OP-" + uuid.uuid4().hex
        if not isinstance(key, str) or not 1 <= len(key) <= 120:
            raise Refusal("Clé d'opération invalide.")
        request = {"a": a, "b": b, "prediction": prediction, "operation_key": key,
                   "protocol_version": protocol_version, "claims": claims, "receiver_failure": receiver_failure}
        for action in actions.values():
            if action["request"]["operation_key"] == key:
                if action["request"] != request:
                    raise Refusal("Même clé avec un contenu différent : collision refusée.")
                return action["action_id"]
        # Limite matérielle du prototype, même si la politique est élargie manuellement.
        if len(actions) >= 200:
            raise Refusal("Limite de ce dossier : 200 actions. Conserver ce dossier et ouvrir une nouvelle mission.")
        action_id = "ACT-" + uuid.uuid4().hex
        self.append("ACTION_PREPARED", action_id, request)
        return action_id

    def check_receipt(self, action, receipt, hashed):
        fields = {"schema", "operation_key", "action_id", "request_sha256", "a", "b", "status", "lamp_on", "effect_count", "at"}
        if (set(receipt) != fields or receipt["schema"] != "mia-receipt-1" or digest(receipt) != hashed
                or receipt["operation_key"] != action["request"]["operation_key"]
                or receipt["action_id"] != action["action_id"]
                or receipt["request_sha256"] != digest(action["request"])
                or receipt["a"] is not action["request"]["a"] or receipt["b"] is not action["request"]["b"]
                or receipt["status"] not in {"EFFECT_DONE", "FAILED"}
                or type(receipt["effect_count"]) is not int
                or (receipt["status"] == "EFFECT_DONE" and (type(receipt["lamp_on"]) is not bool or receipt["effect_count"] != 1))
                or (receipt["status"] == "FAILED" and (receipt["lamp_on"] is not None or receipt["effect_count"] != 0))):
            raise IntegrityError("Reçu invalide ou non lié à l'action.")

    def receipt(self, action):
        with database(self.receipts) as db:
            row = db.execute("SELECT payload,sha256 FROM receipts WHERE operation_key=?", (action["request"]["operation_key"],)).fetchone()
        if row is None:
            return None
        receipt = strict_json(row[0])
        self.check_receipt(action, receipt, row[1])
        return receipt

    def execute_receiver(self, action):
        """Effet simulé + reçu atomiques ICI, sans garantie pour un service externe réel."""
        request = action["request"]
        with database(self.receipts) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT payload,sha256 FROM receipts WHERE operation_key=?", (request["operation_key"],)).fetchone()
            if existing:
                receipt = strict_json(existing[0])
                self.check_receipt(action, receipt, existing[1])
                return receipt
            failed = request["receiver_failure"]
            receipt = {"schema": "mia-receipt-1", "operation_key": request["operation_key"],
                       "action_id": action["action_id"], "request_sha256": digest(request),
                       "a": request["a"], "b": request["b"], "status": "FAILED" if failed else "EFFECT_DONE",
                       "lamp_on": None if failed else request["a"] != request["b"],
                       "effect_count": 0 if failed else 1, "at": utc()}
            db.execute("INSERT INTO receipts VALUES (?,?,?)", (request["operation_key"], canonical(receipt), digest(receipt)))
            db.commit()
            return receipt

    def archive_receipt(self, action, receipt):
        data = {"receipt_sha256": digest(receipt), "operation_key": receipt["operation_key"],
                "explanation": "Reçu retrouvé et vérifié."}
        if receipt["status"] == "FAILED":
            data["explanation"] = "Le simulateur a enregistré un échec avant effet."
            self.append("FAILURE_RECORDED", action["action_id"], data)
        else:
            data.update(evidence_id="OBS-" + action["action_id"], lamp_on=receipt["lamp_on"],
                        prediction_correct=action["request"]["prediction"] == receipt["lamp_on"])
            self.append("OBSERVATION_RECORDED", action["action_id"], data)

    @staticmethod
    def crash(current, selected):
        if current == selected:
            os._exit(CRASH_EXIT)

    def resume(self, action_id, crash=None):
        _, actions, _, _ = self.verify()
        if action_id not in actions:
            raise Refusal("Action inconnue.")
        action = actions[action_id]
        state = action["state"]
        if state in TERMINAL:
            return action
        if state in {"EN_COURS", "RÉSULTAT_À_VÉRIFIER"}:
            if state != "RÉSULTAT_À_VÉRIFIER":
                self.append("RESULT_UNCERTAIN", action_id, {"explanation": "Envoi enregistré, résultat à rapprocher. Aucune relance."})
            receipt = self.receipt(action)
            if receipt is not None:
                self.archive_receipt(action, receipt)
            return self.projection()[0][action_id]
        reason, measures = self.gate(action, actions)
        if reason:
            self.append("REFUSED", action_id, {"explanation": reason, "measurements": measures})
            return self.projection()[0][action_id]
        if state == "PRÉPARÉE":
            self.crash("before_intent", crash)
            self.append("INTENT_RECORDED", action_id, {"request_sha256": digest(action["request"]), "measurements": measures})
            self.crash("after_intent", crash)
        reason, measures = self.gate(action, actions)
        if reason:
            self.append("REFUSED", action_id, {"explanation": reason, "measurements": measures})
            return self.projection()[0][action_id]
        self.append("DISPATCH_RECORDED", action_id, {"request_sha256": digest(action["request"]), "measurements": measures})
        self.crash("after_dispatch", crash)
        # Contrôle au point d'effet. Les modifications par l'API sont sérialisées par le verrou.
        reason, measures = self.gate(action, actions)
        if reason:
            self.append("REFUSED", action_id, {"explanation": reason, "measurements": measures})
            return self.projection()[0][action_id]
        receipt = self.execute_receiver(action)
        self.crash("after_effect", crash)
        self.archive_receipt(action, receipt)
        return self.projection()[0][action_id]

    def run(self, a, b, prediction, *, crash=None, **kwargs):
        action_id = self.prepare(a, b, prediction, **kwargs)
        return self.resume(action_id, crash)

    def status(self):
        events, actions, protocols, _ = self.verify()
        with database(self.receipts) as db:
            receipts = [strict_json(r[0]) for r in db.execute("SELECT payload FROM receipts ORDER BY rowid")]
        return {"version": VERSION, "scope": "Simulation locale sans IA", "integrity": "VERIFIED",
                "actions": list(actions.values()), "events": events, "protocols": protocols,
                "effect_count": sum(r["effect_count"] for r in receipts), "receipt_count": len(receipts)}

    def report(self):
        state = self.status()
        esc = lambda value: html.escape(str(value))
        rows = "".join("<tr>" + "".join("<td>" + esc(value) + "</td>" for value in
            (a["action_id"], a["request"]["a"], a["request"]["b"], a["request"]["prediction"],
             a.get("detail", {}).get("lamp_on", "—"), a["state"], a.get("detail", {}).get("prediction_correct", "—"))) + "</tr>" for a in state["actions"])
        output = self.root / "journal-lisible.html"
        content = ('<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>MultiversIA — journal local</title><style>body{font:16px/1.6 system-ui;max-width:1200px;margin:40px auto;padding:20px;background:#f4f7fb;color:#18354b}'
            'table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #ccd6e0;padding:9px;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:20px}'
            '</style><h1>MultiversIA — cycle durable V' + VERSION + '</h1><p>Simulation locale sans IA. Export du ' + esc(utc()) + '.</p>'
            '<p>Intégrité vérifiée lors de cet export. Effets simulés : <strong>' + str(state["effect_count"]) + '</strong>.</p>'
            '<p>Une expérience terminée peut contenir une prédiction fausse. Les tests de panne ne certifient pas une coupure électrique ni un service externe réel.</p>'
            '<table><tr><th>Action</th><th>A</th><th>B</th><th>Prédiction</th><th>Lampe</th><th>État</th><th>Prédiction juste</th></tr>' + rows + '</table>'
            '<h2>Événements conservés</h2><pre>' + esc(json.dumps(state, ensure_ascii=False, indent=2)) + '</pre></html>')
        output.write_text(content, encoding="utf-8")
        return str(output)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="MultiversIA — prototype local de cycle durable")
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parent / "data")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "status", "report"):
        commands.add_parser(name)
    run = commands.add_parser("run")
    run.add_argument("--a", type=int, choices=[0, 1], required=True)
    run.add_argument("--b", type=int, choices=[0, 1], required=True)
    run.add_argument("--prediction", type=int, choices=[0, 1], required=True)
    run.add_argument("--operation-key")
    run.add_argument("--protocol", default="lampe-v1")
    run.add_argument("--receiver-failure", action="store_true")
    run.add_argument("--claims-json", default="[]")
    run.add_argument("--crash", choices=["before_intent", "after_intent", "after_dispatch", "after_effect"])
    resume = commands.add_parser("resume")
    resume.add_argument("action_id")
    args = parser.parse_args()
    try:
        with exclusive(args.data.resolve()):
            engine = Engine(args.data)
            if args.command == "init":
                engine.initialize()
                result = {"initialized": True, "data": str(engine.root)}
            elif args.command == "status":
                result = engine.status()
            elif args.command == "report":
                result = {"report": engine.report()}
            elif args.command == "resume":
                result = engine.resume(args.action_id)
            else:
                result = engine.run(bool(args.a), bool(args.b), bool(args.prediction),
                    operation_key=args.operation_key, protocol_version=args.protocol, crash=args.crash,
                    claims=strict_json(args.claims_json), receiver_failure=args.receiver_failure)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except (Refusal, IntegrityError, OSError, sqlite3.DatabaseError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
