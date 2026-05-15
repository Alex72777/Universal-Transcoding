#!/usr/bin/env python3
"""
UL Transcoding — Universal Live Transcoding Tool
Browser-compatible video conversion for CopyParty file servers.

Requirements: Python 3.8+  ·  ffmpeg + ffprobe in PATH (https://ffmpeg.org)
No pip packages needed — pure stdlib + tkinter.
"""
from __future__ import annotations

import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# ──────────────────────────────────────────────────────────────────── meta ───

APP_NAME = "UL Transcoding"
VERSION  = "1.0.0"

# ─────────────────────────────────────────────────────── file-type detection ──

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".wmv", ".flv",
    ".webm", ".ogv", ".ts",  ".m2ts", ".mts", ".3gp", ".3g2",
    ".asf", ".rm",  ".rmvb", ".vob",  ".mpg", ".mpeg", ".m2v",
    ".divx", ".f4v",
})

# ──────────────────────────────────────────────────────────── output formats ──
#
# Each entry:
#   ext           – output file extension
#   vcodec        – ffmpeg video encoder name
#   acodec        – ffmpeg audio encoder name
#   mux_args      – extra muxer / container-level ffmpeg args
#   compat_vcodec – video codec names (from ffprobe) that are already OK
#   compat_acodec – audio codec names (from ffprobe) that are already OK
#   compat_fmt    – container format names (from ffprobe) that are already OK
#
# A file is skipped entirely when ALL three compat sets match.

OUTPUT_FORMATS: dict[str, dict] = {
    "MP4 — H.264 + AAC  (recommended)": {
        "ext":           "mp4",
        "vcodec":        "libx264",
        "acodec":        "aac",
        "mux_args":      ["-movflags", "+faststart", "-pix_fmt", "yuv420p"],
        "compat_vcodec": {"h264"},
        "compat_acodec": {"aac"},
        "compat_fmt":    {"mp4", "mov", "m4v"},
    },
    "MP4 — H.264 + Opus": {
        "ext":           "mp4",
        "vcodec":        "libx264",
        "acodec":        "libopus",
        "mux_args":      ["-movflags", "+faststart", "-pix_fmt", "yuv420p"],
        "compat_vcodec": {"h264"},
        "compat_acodec": {"opus"},
        "compat_fmt":    {"mp4"},
    },
    "WebM — VP9 + Opus": {
        "ext":           "webm",
        "vcodec":        "libvp9",
        "acodec":        "libopus",
        "mux_args":      [],
        "compat_vcodec": {"vp9"},
        "compat_acodec": {"opus"},
        "compat_fmt":    {"webm"},
    },
    "WebM — VP8 + Vorbis": {
        "ext":           "webm",
        "vcodec":        "libvp8",
        "acodec":        "libvorbis",
        "mux_args":      [],
        "compat_vcodec": {"vp8"},
        "compat_acodec": {"vorbis"},
        "compat_fmt":    {"webm"},
    },
    "MKV — H.264 + AAC": {
        "ext":           "mkv",
        "vcodec":        "libx264",
        "acodec":        "aac",
        "mux_args":      ["-pix_fmt", "yuv420p"],
        "compat_vcodec": {"h264"},
        "compat_acodec": {"aac"},
        "compat_fmt":    {"matroska"},
    },
}

QUALITY_PRESETS: dict[str, dict] = {
    "Very High  (CRF 16)": {"crf": "16", "preset": "slow"},
    "High       (CRF 20)": {"crf": "20", "preset": "medium"},
    "Medium     (CRF 23)": {"crf": "23", "preset": "medium"},
    "Low        (CRF 28)": {"crf": "28", "preset": "fast"},
    "Very Low   (CRF 32)": {"crf": "32", "preset": "veryfast"},
}

DEFAULT_FORMAT  = "MP4 — H.264 + AAC  (recommended)"
DEFAULT_QUALITY = "Medium     (CRF 23)"
DEFAULT_THREADS = min(os.cpu_count() or 4, 16)

# ──────────────────────────────────────────────────────────────── data types ──

class Status:
    PENDING  = "pending"
    SKIPPED  = "skipped"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"


@dataclass
class Job:
    src:       Path
    dst:       Path
    status:    str   = Status.PENDING
    progress:  float = 0.0          # 0–100 for current file
    error:     str   = ""
    # filled after probe
    vcodec:    str   = ""
    acodec:    str   = ""
    container: str   = ""
    duration:  float = 0.0          # seconds


@dataclass
class Msg:
    """Messages sent from the worker thread to the UI via a SimpleQueue."""
    kind: str                       # job_start | job_progress | job_done | all_done
    job:  Optional[Job] = None
    text: str = ""
    tag:  str = ""                  # optional log-colour override


# ──────────────────────────────────────────────────────────── ffmpeg helpers ──

def find_ffmpeg() -> tuple[str, str]:
    """Return (ffmpeg, ffprobe) absolute paths, or raise RuntimeError."""
    ff  = shutil.which("ffmpeg")
    ffp = shutil.which("ffprobe")
    if not ff or not ffp:
        raise RuntimeError(
            "ffmpeg / ffprobe not found in PATH.\n\n"
            "Install FFmpeg from  https://ffmpeg.org/download.html\n"
            "and make sure both ffmpeg and ffprobe are on your system PATH."
        )
    return ff, ffp


def ffmpeg_version(ffmpeg: str) -> str:
    """Return the first line of `ffmpeg -version`, or an error string."""
    try:
        r = subprocess.run(
            [ffmpeg, "-version"], capture_output=True, text=True, timeout=10
        )
        return r.stdout.splitlines()[0] if r.stdout else "unknown version"
    except Exception as e:
        return f"(could not read version: {e})"


def probe(ffprobe_path: str, src: Path) -> tuple[str, str, str, float]:
    """
    Run ffprobe on *src* and return (vcodec, acodec, container, duration_sec).
    Raises ValueError on failure.
    """
    cmd = [
        ffprobe_path, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(src),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise ValueError("ffprobe timed out")

    if r.returncode != 0:
        msg = r.stderr.strip()[:300] or "ffprobe returned non-zero"
        raise ValueError(msg)

    data      = json.loads(r.stdout)
    fmt_info  = data.get("format", {})
    streams   = data.get("streams", [])

    # e.g. "matroska,webm" → "matroska"
    container = fmt_info.get("format_name", "").lower().split(",")[0]
    duration  = float(fmt_info.get("duration") or 0)

    vcodec = acodec = ""
    for s in streams:
        ct = s.get("codec_type", "")
        if ct == "video" and not vcodec:
            vcodec = s.get("codec_name", "").lower()
        elif ct == "audio" and not acodec:
            acodec = s.get("codec_name", "").lower()

    return vcodec, acodec, container, duration


def is_compatible(job: Job, fmt: dict) -> bool:
    """Return True if the file already matches the target format (skip it)."""
    return (
        job.vcodec    in fmt["compat_vcodec"]
        and job.acodec    in fmt["compat_acodec"]
        and job.container in fmt["compat_fmt"]
    )


def build_cmd(
    ffmpeg_path: str,
    job:         Job,
    fmt:         dict,
    quality:     dict,
    threads:     int,
) -> list[str]:
    """Assemble the ffmpeg command list for one transcoding job."""
    crf    = quality["crf"]
    preset = quality["preset"]

    # ── video codec args ───────────────────────────────────────────────────
    if job.vcodec in fmt["compat_vcodec"]:
        # Codec already matches — just remux the video stream
        v_args: list[str] = ["-c:v", "copy"]
    elif fmt["vcodec"] == "libvp9":
        # VP9 CRF mode requires -b:v 0
        v_args = [
            "-c:v", "libvp9",
            "-crf", crf, "-b:v", "0",
            "-deadline", "good", "-cpu-used", "2", "-row-mt", "1",
        ]
    elif fmt["vcodec"] == "libvp8":
        v_args = ["-c:v", "libvp8", "-crf", crf, "-b:v", "0"]
    else:
        # libx264 / libx265 / etc.
        v_args = ["-c:v", fmt["vcodec"], "-crf", crf, "-preset", preset]

    # ── audio codec args ───────────────────────────────────────────────────
    if job.acodec in fmt["compat_acodec"]:
        a_args: list[str] = ["-c:a", "copy"]
    else:
        ac     = fmt["acodec"]
        bitrate = "192k" if ac == "aac" else "128k"
        a_args  = ["-c:a", ac, "-b:a", bitrate]

    return [
        ffmpeg_path, "-y",
        "-i", str(job.src),
        "-map", "0:v:0?",   # first video stream  (? = don't fail if absent)
        "-map", "0:a?",     # all audio streams   (? = don't fail if absent)
        *v_args,
        *a_args,
        *fmt["mux_args"],
        "-threads", str(threads),
        "-progress", "pipe:1",   # write progress key=value pairs to stdout
        "-nostats",
        str(job.dst),
    ]


# ─────────────────────────────────────────────────────────── worker thread ───

class Worker(threading.Thread):
    """Background thread that processes a list of Jobs sequentially."""

    def __init__(
        self,
        jobs:             List[Job],
        ffmpeg:           str,
        ffprobe:          str,
        fmt:              dict,
        quality:          dict,
        threads:          int,
        send:             Callable[[Msg], None],
        delete_originals: bool = False,
    ) -> None:
        super().__init__(daemon=True)
        self.jobs             = jobs
        self.ffmpeg           = ffmpeg
        self.ffprobe          = ffprobe
        self.fmt              = fmt
        self.quality          = quality
        self.threads          = threads
        self.send             = send
        self.delete_originals = delete_originals
        self._stop   = threading.Event()
        self._proc: Optional[subprocess.Popen] = None

    # ── helpers ───────────────────────────────────────────────────────────

    def _maybe_delete(self, path: Path) -> None:
        """Delete the source file if the user opted in."""
        if not self.delete_originals:
            return
        try:
            path.unlink()
        except Exception as exc:
            # Non-fatal: report via queue but don't change job status
            self.send(Msg("log", text=f"WARN  Could not delete {path.name}: {exc}",
                          tag="warn"))

    # ── public control ─────────────────────────────────────────────────────

    def stop(self) -> None:
        """Signal the worker to stop and kill any running ffmpeg process."""
        self._stop.set()
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    # ── thread entry point ─────────────────────────────────────────────────

    def run(self) -> None:
        for job in self.jobs:
            if self._stop.is_set():
                break
            self._process(job)
        self.send(Msg("all_done"))

    # ── per-job processing ─────────────────────────────────────────────────

    def _process(self, job: Job) -> None:
        # 1. Probe ──────────────────────────────────────────────────────────
        try:
            job.vcodec, job.acodec, job.container, job.duration = probe(
                self.ffprobe, job.src
            )
        except Exception as exc:
            job.status = Status.FAILED
            job.error  = str(exc)
            self.send(Msg("job_done", job, f"FAIL  {job.src.name} — probe error: {exc}"))
            return

        # Warn about files with no detectable video stream and skip them
        if not job.vcodec:
            job.status = Status.SKIPPED
            self.send(Msg("job_done", job,
                          f"WARN  {job.src.name} — no video stream found, skipping",
                          tag="warn"))
            return

        # 2. Compatibility check — copy as-is if already compatible ──────────
        if is_compatible(job, self.fmt):
            try:
                job.dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(job.src, job.dst)
                job.status   = Status.DONE
                job.progress = 100.0
                self.send(Msg("job_done", job,
                              f"COPY  {job.src.name}  (already compatible: "
                              f"{job.vcodec}/{job.acodec} in {job.container})",
                              tag="skip"))
                self._maybe_delete(job.src)
            except Exception as exc:
                job.status = Status.FAILED
                job.error  = str(exc)
                self.send(Msg("job_done", job, f"FAIL  {job.src.name} — copy error: {exc}"))
            return

        # 3. Create output directory ────────────────────────────────────────
        try:
            job.dst.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            job.status = Status.FAILED
            job.error  = str(exc)
            self.send(Msg("job_done", job, f"FAIL  {job.src.name} — mkdir: {exc}"))
            return

        # 4. Build command and notify UI ────────────────────────────────────
        cmd = build_cmd(self.ffmpeg, job, self.fmt, self.quality, self.threads)
        job.status = Status.RUNNING
        self.send(Msg("job_start", job,
                      f"START {job.src.name}  →  {job.dst.name}  "
                      f"[{job.vcodec}/{job.acodec} → "
                      f"{self.fmt['vcodec']}/{self.fmt['acodec']}]"))

        # 5. Run ffmpeg, parse progress ─────────────────────────────────────
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            dur_us = job.duration * 1_000_000  # total microseconds

            for line in self._proc.stdout:          # type: ignore[union-attr]
                if self._stop.is_set():
                    break
                line = line.strip()
                if line.startswith("out_time_us="):
                    try:
                        us = float(line.split("=", 1)[1])
                        if dur_us > 0:
                            job.progress = min(100.0, us / dur_us * 100.0)
                            self.send(Msg("job_progress", job))
                    except ValueError:
                        pass

            self._proc.wait()
            rc = self._proc.returncode

        except Exception as exc:
            job.status = Status.FAILED
            job.error  = str(exc)
            self.send(Msg("job_done", job, f"FAIL  {job.src.name}: {exc}"))
            return
        finally:
            self._proc = None

        # 6. Handle stop / result ───────────────────────────────────────────
        if self._stop.is_set():
            # Delete partial output file
            try:
                if job.dst.exists():
                    job.dst.unlink()
            except Exception:
                pass
            return

        if rc == 0:
            job.status   = Status.DONE
            job.progress = 100.0
            self.send(Msg("job_done", job, f"DONE  {job.src.name}"))
            self._maybe_delete(job.src)
        else:
            job.status = Status.FAILED
            job.error  = f"ffmpeg exit code {rc}"
            self.send(Msg("job_done", job, f"FAIL  {job.src.name}  (exit code {rc})"))


# ──────────────────────────────────────────────────────────────── UI helpers ──

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _open_folder(path: str) -> None:
    """Open a folder in the system's file manager."""
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(path)                          # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────── App ────

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME}  v{VERSION}")
        self.resizable(True, True)
        self.minsize(700, 580)

        # Locate ffmpeg/ffprobe before building the UI
        try:
            self._ffmpeg, self._ffprobe = find_ffmpeg()
        except RuntimeError as exc:
            # Build a minimal root so messagebox works, then exit
            self.withdraw()
            messagebox.showerror("FFmpeg not found", str(exc))
            self.destroy()
            return

        # Runtime state
        self._jobs:   List[Job]            = []
        self._worker: Optional[Worker]     = None
        self._queue:  queue.SimpleQueue[Msg] = queue.SimpleQueue()
        self._n_done = self._n_skip = self._n_fail = 0

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Log ffmpeg version on startup
        ver = ffmpeg_version(self._ffmpeg)
        self._log_write(f"[{_ts()}] {APP_NAME} v{VERSION} ready\n", "info")
        self._log_write(f"[{_ts()}] {ver}\n", "info")
        self._log_write(f"[{_ts()}] ffmpeg: {self._ffmpeg}\n", "info")
        self._log_write(f"[{_ts()}] ffprobe: {self._ffprobe}\n\n", "info")

        # Start the polling loop (runs forever)
        self.after(80, self._poll)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        PAD = {"padx": 8, "pady": 4}

        # ── Folders ────────────────────────────────────────────────────────
        pf = ttk.LabelFrame(self, text=" Folders ")
        pf.pack(fill="x", **PAD)

        self._in_var  = tk.StringVar()
        self._out_var = tk.StringVar()

        ttk.Label(pf, text="Input folder:").grid(
            row=0, column=0, sticky="w", padx=8, pady=3)
        ttk.Entry(pf, textvariable=self._in_var).grid(
            row=0, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(pf, text="Browse…", width=9, command=self._browse_in).grid(
            row=0, column=2, padx=8, pady=3)

        ttk.Label(pf, text="Output folder:").grid(
            row=1, column=0, sticky="w", padx=8, pady=3)
        ttk.Entry(pf, textvariable=self._out_var).grid(
            row=1, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(pf, text="Browse…", width=9, command=self._browse_out).grid(
            row=1, column=2, padx=8, pady=3)

        pf.columnconfigure(1, weight=1)

        # ── Settings ───────────────────────────────────────────────────────
        sf = ttk.LabelFrame(self, text=" Settings ")
        sf.pack(fill="x", **PAD)

        self._fmt_var  = tk.StringVar(value=DEFAULT_FORMAT)
        self._qual_var = tk.StringVar(value=DEFAULT_QUALITY)
        self._thr_var  = tk.IntVar(value=DEFAULT_THREADS)
        self._rec_var  = tk.BooleanVar(value=True)

        ttk.Label(sf, text="Output format:").grid(
            row=0, column=0, sticky="w", padx=8, pady=3)
        ttk.Combobox(
            sf, textvariable=self._fmt_var,
            values=list(OUTPUT_FORMATS), state="readonly", width=42,
        ).grid(row=0, column=1, columnspan=3, sticky="ew", padx=4, pady=3)

        ttk.Label(sf, text="Quality:").grid(
            row=1, column=0, sticky="w", padx=8, pady=3)
        ttk.Combobox(
            sf, textvariable=self._qual_var,
            values=list(QUALITY_PRESETS), state="readonly", width=22,
        ).grid(row=1, column=1, sticky="ew", padx=4, pady=3)

        ttk.Label(sf, text="Threads:").grid(
            row=1, column=2, sticky="w", padx=(16, 2), pady=3)
        ttk.Spinbox(sf, from_=1, to=128, textvariable=self._thr_var, width=5).grid(
            row=1, column=3, sticky="w", padx=4, pady=3)

        ttk.Checkbutton(sf, text="Recursive scan (include sub-folders)",
                        variable=self._rec_var).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))

        self._del_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(sf, text="Delete original files after conversion",
                        variable=self._del_var).grid(
            row=3, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 4))

        sf.columnconfigure(1, weight=1)

        # ── Action buttons ─────────────────────────────────────────────────
        bf = ttk.Frame(self)
        bf.pack(fill="x", padx=8, pady=(2, 0))

        self._btn_scan  = ttk.Button(bf, text="  Scan Files  ",
                                     command=self._scan)
        self._btn_start = ttk.Button(bf, text="  ▶  Start  ",
                                     command=self._start, state="disabled")
        self._btn_stop  = ttk.Button(bf, text="  ■  Stop  ",
                                     command=self._stop,  state="disabled")
        self._btn_open  = ttk.Button(bf, text="  Open Output Folder  ",
                                     command=self._open_output, state="disabled")

        self._btn_scan.pack(side="left", padx=(0, 4))
        self._btn_start.pack(side="left", padx=4)
        self._btn_stop.pack(side="left", padx=4)
        self._btn_open.pack(side="right", padx=(4, 0))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=8, pady=6)

        # ── Progress / status ──────────────────────────────────────────────
        prog_frame = ttk.Frame(self)
        prog_frame.pack(fill="x", padx=8)

        self._stat_var = tk.StringVar(value="Ready.  Select a folder and click Scan Files.")
        ttk.Label(prog_frame, textvariable=self._stat_var, anchor="w").pack(fill="x")

        # Overall bar
        overall_row = ttk.Frame(prog_frame)
        overall_row.pack(fill="x", pady=(4, 0))
        ttk.Label(overall_row, text="Overall: ", width=10, anchor="w").pack(side="left")
        self._bar_overall = ttk.Progressbar(overall_row, mode="determinate")
        self._bar_overall.pack(side="left", fill="x", expand=True)
        self._bar_overall_lbl = tk.StringVar(value="")
        ttk.Label(overall_row, textvariable=self._bar_overall_lbl,
                  width=10, anchor="e").pack(side="left", padx=(6, 0))

        # Current file label
        self._cur_var = tk.StringVar(value="")
        ttk.Label(prog_frame, textvariable=self._cur_var,
                  anchor="w", foreground="#666666").pack(fill="x", pady=(2, 0))

        # Per-file bar
        file_row = ttk.Frame(prog_frame)
        file_row.pack(fill="x", pady=(2, 0))
        ttk.Label(file_row, text="File:    ", width=10, anchor="w").pack(side="left")
        self._bar_file = ttk.Progressbar(file_row, mode="determinate", maximum=100)
        self._bar_file.pack(side="left", fill="x", expand=True)
        self._bar_file_lbl = tk.StringVar(value="")
        ttk.Label(file_row, textvariable=self._bar_file_lbl,
                  width=10, anchor="e").pack(side="left", padx=(6, 0))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=8, pady=6)

        # ── Log ────────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text=" Log ")
        log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Clear button inside label frame header area (placed top-right)
        ttk.Button(log_frame, text="Clear", width=6,
                   command=self._log_clear).pack(anchor="ne", padx=4, pady=2)

        self._log = ScrolledText(
            log_frame,
            height=10,
            wrap="none",
            font=("Courier New", 9),
            state="disabled",
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
            selectbackground="#264f78",
        )
        self._log.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # Colour tags for log lines
        self._log.tag_config("info",  foreground="#dcdcaa")   # yellow
        self._log.tag_config("start", foreground="#569cd6")   # blue
        self._log.tag_config("skip",  foreground="#888888")   # grey
        self._log.tag_config("done",  foreground="#4ec94e")   # green
        self._log.tag_config("fail",  foreground="#f44747")   # red
        self._log.tag_config("warn",  foreground="#ce9178")   # orange

    # ── Browsing ──────────────────────────────────────────────────────────────

    def _browse_in(self) -> None:
        d = filedialog.askdirectory(title="Select input folder")
        if not d:
            return
        self._in_var.set(d)
        # Auto-suggest an output folder next to the input folder
        if not self._out_var.get().strip():
            p    = Path(d)
            auto = p.parent / f"{p.name}_transcoded"
            self._out_var.set(str(auto))

    def _browse_out(self) -> None:
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self._out_var.set(d)

    def _open_output(self) -> None:
        out = self._out_var.get().strip()
        if out:
            _open_folder(out)

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _scan(self) -> None:
        in_dir  = Path(self._in_var.get().strip())
        out_str = self._out_var.get().strip()

        if not in_dir.is_dir():
            messagebox.showerror("Error", "Input folder does not exist.")
            return
        if not out_str:
            messagebox.showerror("Error", "Please specify an output folder.")
            return

        out_dir = Path(out_str)

        if in_dir == out_dir:
            messagebox.showerror("Error",
                                 "Input and output folders must be different.")
            return
        # Prevent output inside input (would cause infinite recursion / re-scan)
        try:
            out_dir.relative_to(in_dir)
            messagebox.showerror("Error",
                                 "Output folder must not be inside the input folder.")
            return
        except ValueError:
            pass

        fmt     = OUTPUT_FORMATS[self._fmt_var.get()]
        ext     = fmt["ext"]
        recurse = self._rec_var.get()

        # Collect video files
        pattern = "**/*" if recurse else "*"
        files   = sorted(
            p for p in in_dir.glob(pattern)
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        )

        if not files:
            messagebox.showinfo("No files found",
                                "No video files were found in the selected folder.")
            return

        # Build Job list
        self._jobs = []
        for src in files:
            rel = src.relative_to(in_dir)
            dst = out_dir / rel.with_suffix(f".{ext}")
            self._jobs.append(Job(src=src, dst=dst))

        # Reset counters and UI
        self._n_done = self._n_skip = self._n_fail = 0
        self._bar_overall["value"]   = 0
        self._bar_overall["maximum"] = len(self._jobs)
        self._bar_overall_lbl.set(f"0 / {len(self._jobs)}")
        self._bar_file["value"] = 0
        self._bar_file_lbl.set("")
        self._cur_var.set("")

        total_bytes = sum(j.src.stat().st_size for j in self._jobs)
        total_mb    = total_bytes / (1024 * 1024)

        self._log_clear()
        self._log_write(f"[{_ts()}] Scanned: {in_dir}\n", "info")
        self._log_write(f"[{_ts()}] Output:  {out_dir}\n", "info")
        self._log_write(
            f"[{_ts()}] Found {len(self._jobs)} video file(s)  "
            f"({total_mb:.1f} MB total):\n\n",
            "info",
        )
        for j in self._jobs:
            size_mb = j.src.stat().st_size / (1024 * 1024)
            self._log_write(f"  {j.src.relative_to(in_dir)}  ({size_mb:.1f} MB)\n")

        self._stat_var.set(
            f"{len(self._jobs)} file(s) found — click  ▶ Start  to begin."
        )
        self._btn_start.config(state="normal")
        self._btn_open.config(state="disabled")

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def _start(self) -> None:
        if not self._jobs:
            return

        fmt     = OUTPUT_FORMATS[self._fmt_var.get()]
        quality = QUALITY_PRESETS[self._qual_var.get()]
        threads = max(1, self._thr_var.get())

        # Reset counters
        self._n_done = self._n_skip = self._n_fail = 0
        self._bar_overall["value"]   = 0
        self._bar_overall["maximum"] = len(self._jobs)
        self._bar_overall_lbl.set(f"0 / {len(self._jobs)}")
        self._bar_file["value"] = 0
        self._bar_file_lbl.set("")

        self._log_clear()
        self._log_write(
            f"[{_ts()}] Starting — {len(self._jobs)} file(s)  |  "
            f"Format: {self._fmt_var.get()}  |  "
            f"Quality: {self._qual_var.get()}  |  "
            f"Threads: {threads}\n\n",
            "info",
        )

        self._btn_scan.config(state="disabled")
        self._btn_start.config(state="disabled")
        self._btn_stop.config(state="normal")
        self._btn_open.config(state="disabled")
        self._stat_var.set("Transcoding…")

        # Reset job statuses so they can be re-run
        for job in self._jobs:
            job.status   = Status.PENDING
            job.progress = 0.0
            job.error    = ""

        delete_originals = self._del_var.get()
        if delete_originals:
            self._log_write(f"[{_ts()}] ⚠  Delete originals is ON — source files will be "
                            f"removed after successful conversion or copy.\n", "warn")

        self._worker = Worker(
            jobs             = list(self._jobs),
            ffmpeg           = self._ffmpeg,
            ffprobe          = self._ffprobe,
            fmt              = fmt,
            quality          = quality,
            threads          = threads,
            send             = self._queue.put,
            delete_originals = delete_originals,
        )
        self._worker.start()

    def _stop(self) -> None:
        if self._worker and self._worker.is_alive():
            self._worker.stop()
        self._log_write(f"\n[{_ts()}] Stop requested — waiting for current file…\n", "warn")
        self._btn_stop.config(state="disabled")

    # ── Message polling (runs on the main/UI thread) ───────────────────────────

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self._queue.get_nowait())
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _handle(self, msg: Msg) -> None:  # noqa: C901  (acceptable complexity)
        job = msg.job

        if msg.kind == "log":
            self._log_write(f"[{_ts()}] {msg.text}\n", msg.tag or "")

        elif msg.kind == "job_start":
            assert job is not None
            self._bar_file["value"] = 0
            self._bar_file_lbl.set("0%")
            self._cur_var.set(f"Encoding: {job.src.name}")
            self._log_write(f"[{_ts()}] {msg.text}\n", "start")

        elif msg.kind == "job_progress":
            assert job is not None
            pct = job.progress
            self._bar_file["value"] = pct
            self._bar_file_lbl.set(f"{pct:.0f}%")
            self._cur_var.set(f"Encoding: {job.src.name}  ({pct:.1f}%)")

        elif msg.kind == "job_done":
            assert job is not None
            # Choose log tag (explicit override takes priority)
            if msg.tag and job.status != Status.DONE:
                tag = msg.tag
                self._n_skip += 1
            elif job.status == Status.DONE and msg.tag:
                # COPY case: compatible file — tag it grey but count as done
                tag = msg.tag
                self._n_done += 1
                self._bar_file["value"] = 100
                self._bar_file_lbl.set("100%")
            elif job.status == Status.SKIPPED:
                tag = "skip"
                self._n_skip += 1
            elif job.status == Status.DONE:
                tag = "done"
                self._n_done += 1
                self._bar_file["value"] = 100
                self._bar_file_lbl.set("100%")
            else:
                tag = "fail"
                self._n_fail += 1

            self._log_write(f"[{_ts()}] {msg.text}\n", tag)

            # Advance overall bar
            completed = self._n_done + self._n_skip + self._n_fail
            self._bar_overall["value"] = completed
            total = len(self._jobs)
            self._bar_overall_lbl.set(f"{completed} / {total}")
            self._stat_var.set(
                f"{completed} / {total} files   "
                f"✓ {self._n_done} done   "
                f"⤏ {self._n_skip} skipped   "
                f"✗ {self._n_fail} failed"
            )

        elif msg.kind == "all_done":
            total = len(self._jobs)
            self._cur_var.set("")
            self._bar_file["value"] = 0
            self._bar_file_lbl.set("")
            self._bar_overall["value"] = total
            self._bar_overall_lbl.set(f"{total} / {total}")
            self._log_write(
                f"\n[{_ts()}] ── All done ──   "
                f"Converted: {self._n_done}   "
                f"Skipped: {self._n_skip}   "
                f"Failed: {self._n_fail}\n",
                "info",
            )
            self._stat_var.set(
                f"Finished — "
                f"✓ {self._n_done} converted   "
                f"⤏ {self._n_skip} skipped   "
                f"✗ {self._n_fail} failed"
            )
            self._btn_scan.config(state="normal")
            self._btn_start.config(state="normal")
            self._btn_stop.config(state="disabled")
            out = self._out_var.get().strip()
            if out:
                self._btn_open.config(state="normal")

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _log_clear(self) -> None:
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")

    def _log_write(self, text: str, tag: str = "") -> None:
        self._log.config(state="normal")
        if tag:
            self._log.insert("end", text, tag)
        else:
            self._log.insert("end", text)
        self._log.see("end")
        self._log.config(state="disabled")

    # ── Window close ──────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        if self._worker and self._worker.is_alive():
            if messagebox.askyesno(
                "Quit",
                "Transcoding is in progress.\nStop and quit?",
            ):
                self._worker.stop()
                self.destroy()
        else:
            self.destroy()


# ─────────────────────────────────────────────────────────────────── entry ───

def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
