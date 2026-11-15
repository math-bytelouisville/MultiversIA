"""Interface de bureau : les expériences tournent dans un processus séparé."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

BASE = Path(__file__).resolve().parent
WORKER_PYTHON = str(Path(sys.executable).with_name("python.exe")) if Path(sys.executable).name.lower() == "pythonw.exe" else sys.executable
CRASHES = {"Aucune panne": None, "Avant l'intention": "before_intent",
           "Après l'intention": "after_intent", "Après l'envoi, avant l'effet": "after_dispatch",
           "Après l'effet, avant le résultat": "after_effect"}


class App:
    def __init__(self, root, data):
        self.root, self.data = root, data
        self.state = {"actions": [], "events": []}
        self.last_id = None
        root.title("MultiversIA — Cycle durable V0.1.0")
        root.geometry("1120x820")
        root.minsize(900, 720)
        root.configure(bg="#f1f5f9")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f1f5f9")
        style.configure("TLabel", background="#f1f5f9", foreground="#19384e", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 23, "bold"))
        style.configure("Sub.TLabel", foreground="#526d82")
        style.configure("TButton", font=("Segoe UI", 10), padding=(13, 8))
        style.configure("Primary.TButton", background="#176c70", foreground="white")
        style.map("Primary.TButton", background=[("active", "#12585c")])
        style.configure("Treeview", rowheight=29, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        container = ttk.Frame(root, padding=20)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(6, weight=1)
        container.rowconfigure(8, weight=1)
        ttk.Label(container, text="MultiversIA", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(container, text="Premier cycle durable · simulation locale · sans IA", style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 8))
        intro = "Choisis les interrupteurs et prédis la lampe. Lance l'expérience, puis vérifie ce qui a été conservé."
        ttk.Label(container, text=intro, wraplength=1000).grid(row=2, column=0, sticky="w", pady=(0, 10))

        controls = ttk.LabelFrame(container, text="  1 · Préparer une expérience  ", padding=10)
        controls.grid(row=3, column=0, sticky="ew")
        self.a, self.b = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Interrupteur A", variable=self.a).grid(row=0, column=0, sticky="w", padx=(0, 24))
        ttk.Checkbutton(controls, text="Interrupteur B", variable=self.b).grid(row=0, column=1, sticky="w", padx=(0, 24))
        ttk.Label(controls, text="Je prédis :").grid(row=0, column=2, sticky="e", padx=(0, 8))
        self.prediction = ttk.Combobox(controls, values=["Lampe allumée", "Lampe éteinte"], state="readonly", width=19)
        self.prediction.set("Lampe allumée")
        self.prediction.grid(row=0, column=3, sticky="w")
        ttk.Label(controls, text="Panne simulée :").grid(row=1, column=0, sticky="w", pady=(14, 0))
        self.crash = ttk.Combobox(controls, values=list(CRASHES), state="readonly", width=34)
        self.crash.set("Aucune panne")
        self.crash.grid(row=1, column=1, columnspan=2, sticky="w", pady=(14, 0))
        self.run_button = ttk.Button(controls, text="Lancer l'expérience", command=self.run, style="Primary.TButton")
        self.run_button.grid(row=1, column=3, sticky="w", pady=(14, 0))
        controls.columnconfigure(4, weight=1)

        feedback = ttk.Frame(container, padding=(0, 8))
        feedback.grid(row=4, column=0, sticky="ew")
        self.lamp = tk.Canvas(feedback, width=70, height=70, background="#f1f5f9", highlightthickness=0)
        self.lamp.pack(side="left", padx=(0, 14))
        self.bulb = self.lamp.create_oval(8, 3, 61, 56, fill="#c7d2de", outline="#8195a6", width=2)
        self.lamp.create_rectangle(24, 57, 45, 65, fill="#8195a6", outline="")
        messages = ttk.Frame(feedback)
        messages.pack(side="left", fill="x", expand=True)
        self.message = tk.StringVar(value="Prêt pour la première expérience.")
        self.metrics = tk.StringVar(value="")
        ttk.Label(messages, textvariable=self.message, wraplength=870, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(messages, textvariable=self.metrics, style="Sub.TLabel").pack(anchor="w", pady=(4, 0))

        bar = ttk.Frame(container)
        bar.grid(row=5, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(bar, text="2 · Expériences conservées", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Button(bar, text="Reprendre la sélection", command=self.resume).pack(side="right")
        ttk.Button(bar, text="Actualiser", command=self.refresh).pack(side="right", padx=7)
        table_frame = ttk.Frame(container)
        table_frame.grid(row=6, column=0, sticky="nsew")
        self.table = ttk.Treeview(table_frame, columns=("id", "a", "b", "prediction", "lamp", "state", "correct"), show="headings", height=4)
        for name, label, width in [("id", "Action", 125), ("a", "A", 35), ("b", "B", 35),
                                    ("prediction", "Prédiction", 95), ("lamp", "Lampe", 85),
                                    ("state", "État", 240), ("correct", "Prédiction juste", 120)]:
            self.table.heading(name, text=label)
            self.table.column(name, width=width, minwidth=width, stretch=name in {"id", "state"})
        self.table.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scroll.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind("<<TreeviewSelect>>", self.select)

        ttk.Label(container, text="3 · Journal de l'action sélectionnée", font=("Segoe UI", 12, "bold")).grid(row=7, column=0, sticky="w", pady=(8, 6))
        log_frame = ttk.Frame(container)
        log_frame.grid(row=8, column=0, sticky="nsew")
        self.log = tk.Text(log_frame, height=5, wrap="word", font=("Consolas", 10), bg="#152e43", fg="#e5eef6", relief="flat", padx=12, pady=8)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        log_scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=log_scroll.set, state="disabled")
        footer = ttk.Frame(container)
        footer.grid(row=9, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(footer, text="Exporter le journal HTML", command=self.export).pack(side="right")
        ttk.Label(footer, text="Une prédiction fausse reste visible.\nSans reçu fiable, aucune relance automatique.", style="Sub.TLabel").pack(side="left")
        ttk.Label(container, text="Données : " + str(data), style="Sub.TLabel", wraplength=1000).grid(row=10, column=0, sticky="w", pady=(7, 0))
        result = self.worker("init")
        if result is not None:
            self.refresh()

    def worker(self, *args, allow_crash=False):
        try:
            result = subprocess.run([WORKER_PYTHON, str(BASE / "engine.py"), "--data", str(self.data), *args],
                                    capture_output=True, encoding="utf-8", timeout=15,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if result.returncode == 86 and allow_crash:
                return {"crashed": True}
            payload = json.loads(result.stdout)
            if result.returncode or not payload.get("ok"):
                raise RuntimeError(payload.get("message", result.stderr or "Échec du processus."))
            return payload["result"]
        except subprocess.TimeoutExpired:
            self.message.set("Délai dépassé : résultat à vérifier. Utilise la reprise, sans relancer l'expérience.")
            return None
        except (OSError, ValueError, RuntimeError) as exc:
            self.message.set("Opération arrêtée : " + str(exc))
            messagebox.showerror("MultiversIA — opération arrêtée", str(exc), parent=self.root)
            return None

    def run(self):
        args = ["run", "--a", str(int(self.a.get())), "--b", str(int(self.b.get())),
                "--prediction", "1" if self.prediction.get() == "Lampe allumée" else "0"]
        crash = CRASHES[self.crash.get()]
        if crash:
            args += ["--crash", crash]
        self.run_button.state(["disabled"])
        self.root.update_idletasks()
        try:
            result = self.worker(*args, allow_crash=True)
            self.refresh()
            if self.state["actions"]:
                self.table.selection_set(self.state["actions"][-1]["action_id"])
                self.table.see(self.state["actions"][-1]["action_id"])
                self.select()
            if result and result.get("crashed"):
                self.message.set("Panne simulée : le processus a été interrompu. Clique sur « Reprendre la sélection ».")
        finally:
            self.run_button.state(["!disabled"])

    def refresh(self):
        state = self.worker("status")
        if state is None:
            self.run_button.state(["disabled"])
            return
        self.state = state
        selected = self.table.selection()
        for item in self.table.get_children():
            self.table.delete(item)
        for action in state["actions"]:
            request, detail = action["request"], action.get("detail", {})
            onoff = lambda value: "Allumée" if value else "Éteinte"
            self.table.insert("", "end", iid=action["action_id"], values=(
                action["action_id"][:12], int(request["a"]), int(request["b"]), onoff(request["prediction"]),
                onoff(detail["lamp_on"]) if "lamp_on" in detail else "—", action["state"],
                ("Oui" if detail["prediction_correct"] else "Non") if "prediction_correct" in detail else "—"))
        self.metrics.set(f"{len(state['actions'])} expérience(s) · {state['effect_count']} effet(s) simulé(s) · {len(state['events'])} événements · intégrité vérifiée")
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected[0])
        elif state["actions"]:
            self.table.selection_set(state["actions"][-1]["action_id"])
        self.select()

    def select(self, _event=None):
        selected = self.table.selection()
        if not selected:
            return
        self.last_id = selected[0]
        action = next(a for a in self.state["actions"] if a["action_id"] == self.last_id)
        detail = action.get("detail", {})
        if action["state"] == "TERMINÉE":
            self.lamp.itemconfigure(self.bulb, fill="#f7c94a" if detail["lamp_on"] else "#52687a")
            self.message.set("Expérience terminée. " + ("Ta prédiction était juste." if detail["prediction_correct"] else "Ta prédiction était fausse ; elle est conservée."))
        else:
            self.lamp.itemconfigure(self.bulb, fill="#c7d2de")
            self.message.set(action["state"] + " — " + detail.get("explanation", "Action en attente de reprise."))
        events = [e for e in self.state["events"] if e["action_id"] == self.last_id]
        text = "\n\n".join(f"#{e['seq']}  {e['type']}\n{e['at']}\n" + json.dumps(e["data"], ensure_ascii=False, indent=2) for e in events)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("1.0", text)
        self.log.configure(state="disabled")

    def resume(self):
        selected = self.table.selection()
        if not selected:
            self.message.set("Sélectionne d'abord une expérience.")
            return
        self.worker("resume", selected[0])
        self.refresh()

    def export(self):
        result = self.worker("report")
        if result:
            self.message.set("Journal exporté : " + result["report"])
            os.startfile(result["report"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=BASE / "data")
    args = parser.parse_args()
    root = tk.Tk()
    App(root, args.data.resolve())
    root.mainloop()


if __name__ == "__main__":
    main()
