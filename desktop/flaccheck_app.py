#!/usr/bin/env python3
"""
CutoffCheck — desktop window for flaccheck.py

  • Add files / Add folder (or drag & drop if tkinterdnd2 is installed)
  • Results table, colour-coded by verdict
  • Click a row to see its spectrogram and details
  • Export CSV / save the current spectrogram as PNG

Run:  python3 flaccheck_app.py
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import flaccheck as fc

try:  # optional drag & drop
    from tkinterdnd2 import DND_FILES, TkinterDnD
    BaseTk = TkinterDnD.Tk
    HAS_DND = True
except Exception:
    BaseTk = tk.Tk
    HAS_DND = False

TAG_COLOURS = {
    "genuine": "#1f8f4a",
    "fakehires": "#b7791f",
    "suspicious": "#b7791f",
    "lossy": "#c53030",
    "error": "#c53030",
    "pending": "#808080",
}


def tag_for(verdict: str) -> str:
    v = verdict.upper()
    if v.startswith("LOSSY"):
        return "lossy"
    if v.startswith("ERROR"):
        return "error"
    if v.startswith("SUSPIC"):
        return "suspicious"
    if "FAKE" in v:
        return "fakehires"
    if v.startswith("GENUINE"):
        return "genuine"
    return "pending"


class App(BaseTk):
    def __init__(self):
        super().__init__()
        fc.ensure_ffmpeg_on_path()
        self.title("CutoffCheck")
        self.geometry("1200x800")
        self.minsize(900, 600)

        self.results: dict[str, fc.Result] = {}   # tree item id -> Result
        self.paths: dict[str, str] = {}           # tree item id -> file path
        self.q: queue.Queue = queue.Queue()
        self.busy = False

        self._build_ui()
        self.after(150, self._poll)

        if not self._have_ffmpeg():
            messagebox.showwarning(
                "ffmpeg not found",
                "CutoffCheck needs ffmpeg.\n\nInstall it with:\n  brew install ffmpeg\n\nthen reopen the app.")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        bar = ttk.Frame(self, padding=(10, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="Add Files…", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="Add Folder…", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(bar, text="Clear", command=self.clear).pack(side="left")
        ttk.Button(bar, text="Export CSV…", command=self.export_csv).pack(side="right")
        ttk.Button(bar, text="Save Spectrogram…", command=self.save_png).pack(side="right", padx=6)

        self.status = tk.StringVar(value="Drop FLAC files or folders here, or use Add Files."
                                   if HAS_DND else "Use Add Files or Add Folder to begin.")
        ttk.Label(bar, textvariable=self.status).pack(side="left", padx=16)
        self.progress = ttk.Progressbar(bar, length=160, mode="determinate")
        self.progress.pack(side="right", padx=10)

        panes = ttk.PanedWindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # results table
        top = ttk.Frame(panes)
        cols = ("file", "format", "cutoff", "verdict")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", selectmode="browse")
        for c, w, label in [("file", 430, "File"), ("format", 130, "Format"),
                            ("cutoff", 90, "Cutoff"), ("verdict", 460, "Verdict")]:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor="w", stretch=(c in ("file", "verdict")))
        for t, col in TAG_COLOURS.items():
            self.tree.tag_configure(t, foreground=col)
        sb = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        panes.add(top, weight=2)

        # detail: spectrogram + notes
        bottom = ttk.Frame(panes)
        self.fig = Figure(figsize=(12, 4), dpi=90)
        self.canvas = FigureCanvasTkAgg(self.fig, master=bottom)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.details = tk.Text(bottom, height=5, wrap="word", relief="flat",
                               font=("Menlo", 11) if sys.platform == "darwin" else ("Courier", 10))
        self.details.pack(fill="x")
        self.details.configure(state="disabled")
        panes.add(bottom, weight=3)

        if HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self.on_drop)

    # ------------------------------------------------------------ actions
    def add_files(self):
        files = filedialog.askopenfilenames(
            title="Choose audio files",
            filetypes=[("Lossless audio", " ".join(f"*{e}" for e in sorted(fc.AUDIO_EXT))),
                       ("All files", "*.*")])
        self.enqueue(list(files))

    def add_folder(self):
        d = filedialog.askdirectory(title="Choose a folder")
        if d:
            self.enqueue([d])

    def on_drop(self, event):
        self.enqueue(list(self.tk.splitlist(event.data)))

    def clear(self):
        if self.busy:
            return
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self.paths.clear()
        self.fig.clear()
        self.canvas.draw_idle()
        self._set_details("")

    def enqueue(self, items: list[str]):
        files = fc.collect(items)
        known = set(self.paths.values())
        files = [f for f in files if f not in known]
        if not files:
            return
        ids = []
        for f in files:
            iid = self.tree.insert("", "end", values=(os.path.basename(f), "", "", "waiting…"),
                                   tags=("pending",))
            self.paths[iid] = f
            ids.append(iid)
        if not self.busy:
            self._start(ids)
        else:
            self.pending_more = getattr(self, "pending_more", []) + ids

    def _start(self, ids):
        self.busy = True
        self.progress.configure(maximum=len(ids), value=0)
        threading.Thread(target=self._worker, args=(ids,), daemon=True).start()

    def _worker(self, ids):
        for n, iid in enumerate(ids, 1):
            path = self.paths[iid]
            self.q.put(("start", iid, n, len(ids)))
            try:
                r = fc.analyse(path)
            except Exception as e:
                r = fc.Result(path=path, verdict="ERROR", error=str(e))
            self.q.put(("done", iid, r))
        self.q.put(("finished",))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "start":
                    _, iid, n, total = msg
                    self.tree.set(iid, "verdict", "analysing…")
                    self.status.set(f"Analysing {n} of {total}: {os.path.basename(self.paths[iid])}")
                elif msg[0] == "done":
                    _, iid, r = msg
                    self.results[iid] = r
                    fmt = f"{r.sr/1000:g} kHz / {r.bits}-bit" if r.sr else "—"
                    cut = f"{r.cutoff_khz} kHz" if r.cutoff_khz else "—"
                    v = r.verdict + (f"  [{r.lossy_guess}]" if r.lossy_guess else "")
                    self.tree.item(iid, values=(os.path.basename(r.path), fmt, cut, v),
                                   tags=(tag_for(r.verdict),))
                    self.progress.step(1)
                    if not self.tree.selection():
                        self.tree.selection_set(iid)
                elif msg[0] == "finished":
                    more = getattr(self, "pending_more", [])
                    self.pending_more = []
                    if more:
                        self._start(more)
                    else:
                        self.busy = False
                        self._summarise()
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _summarise(self):
        counts: dict[str, int] = {}
        for r in self.results.values():
            k = tag_for(r.verdict)
            counts[k] = counts.get(k, 0) + 1
        names = {"genuine": "genuine", "fakehires": "fake hi-res/24-bit",
                 "suspicious": "suspicious", "lossy": "lossy", "error": "errors"}
        parts = [f"{counts[k]} {names[k]}" for k in names if counts.get(k)]
        self.status.set(f"Done — {len(self.results)} files: " + ", ".join(parts))

    def on_select(self, _=None):
        sel = self.tree.selection()
        if not sel or sel[0] not in self.results:
            return
        r = self.results[sel[0]]
        if r.error:
            self.fig.clear()
            self.canvas.draw_idle()
            self._set_details(f"{r.path}\n\nError: {r.error}")
            return
        fc.draw_figure(r, self.fig)
        self.canvas.draw_idle()
        lines = [
            r.path,
            f"Verdict: {r.verdict}" + (f"   (looks like {r.lossy_guess})" if r.lossy_guess else ""),
            f"Format: {r.sr/1000:g} kHz / {r.bits}-bit (bits actually used: {r.eff_bits})   "
            f"Cutoff: {r.cutoff_khz} kHz   Wall: {r.wall_db} dB",
        ] + [f"• {n}" for n in r.notes]
        self._set_details("\n".join(lines))

    def _set_details(self, text: str):
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def export_csv(self):
        if not self.results:
            messagebox.showinfo("Nothing to export", "Analyse some files first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="cutoffcheck_report.csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            ordered = [self.results[i] for i in self.tree.get_children() if i in self.results]
            fc.write_csv(ordered, path)
            self.status.set(f"Saved {path}")

    def save_png(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self.results or self.results[sel[0]].error:
            messagebox.showinfo("No spectrogram", "Select an analysed file first.")
            return
        r = self.results[sel[0]]
        name = os.path.splitext(os.path.basename(r.path))[0] + ".png"
        path = filedialog.asksaveasfilename(defaultextension=".png", initialfile=name,
                                            filetypes=[("PNG", "*.png")])
        if path:
            self.fig.savefig(path, dpi=110)
            self.status.set(f"Saved {path}")

    @staticmethod
    def _have_ffmpeg() -> bool:
        from shutil import which
        return bool(which("ffmpeg") and which("ffprobe"))


if __name__ == "__main__":
    App().mainloop()
