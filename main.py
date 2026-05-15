#!/usr/bin/env python3
"""
UL Transcoding — Universal Live Transcoding Tool
Browser-compatible video conversion for CopyParty file servers.

Requirements: Python 3.8+  ·  ffmpeg + ffprobe in PATH (https://ffmpeg.org)
No pip packages needed — pure stdlib + tkinter.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
    _HAS_TK = True
except ImportError:
    _HAS_TK = False
    tk = None          # type: ignore[assignment]
    ttk = None         # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ScrolledText = None  # type: ignore[assignment]

# ──────────────────────────────────────────────────────────────────── meta ───

APP_NAME = "UL Transcoding"
VERSION  = "1.0.1"

# ─────────────────────────────────────────────────────── file-type detection ──

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".wmv", ".flv",
    ".webm", ".ogv", ".ts",  ".m2ts", ".mts", ".3gp", ".3g2",
    ".asf", ".rm",  ".rmvb", ".vob",  ".mpg", ".mpeg", ".m2v",
    ".divx", ".f4v",
})

# ──────────────────────────────────────────────────────────── output formats ──

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

# ──────────────────────────────────────────────────────── hardware acceleration ──
#
# Maps (backend_key, software_encoder) → hardware_encoder.
# None means the backend does not support that codec.

HW_ACCEL_BACKENDS: dict[str, dict[str, Optional[str]]] = {
    "nvenc": {
        "libx264": "h264_nvenc",
        "libx265": "hevc_nvenc",
        "libvp9":  None,
        "libvp8":  None,
    },
    "vaapi": {
        "libx264": "h264_vaapi",
        "libx265": "hevc_vaapi",
        "libvp9":  "vp9_vaapi",
        "libvp8":  None,
    },
    "videotoolbox": {
        "libx264": "h264_videotoolbox",
        "libx265": "hevc_videotoolbox",
        "libvp9":  None,
        "libvp8":  None,
    },
    "qsv": {
        "libx264": "h264_qsv",
        "libx265": "hevc_qsv",
        "libvp9":  "vp9_qsv",
        "libvp8":  None,
    },
    "amf": {
        "libx264": "h264_amf",
        "libx265": "hevc_amf",
        "libvp9":  None,
        "libvp8":  None,
    },
}

# Human-readable labels for the GUI dropdown (key → display label)
HW_ACCEL_LABELS: dict[str, str] = {
    "":            "None (software)",
    "nvenc":       "NVIDIA NVENC",
    "vaapi":       "VAAPI  (Intel / AMD — Linux)",
    "videotoolbox":"Apple VideoToolbox  (macOS)",
    "qsv":         "Intel Quick Sync (QSV)",
    "amf":         "AMD AMF",
}

# ──────────────────────────────────────────────────────────────── CLI aliases ──

CLI_FORMAT_MAP: dict[str, str] = {
    "mp4":       DEFAULT_FORMAT,
    "mp4-aac":   DEFAULT_FORMAT,
    "mp4-opus":  "MP4 — H.264 + Opus",
    "webm":      "WebM — VP9 + Opus",
    "webm-vp9":  "WebM — VP9 + Opus",
    "webm-vp8":  "WebM — VP8 + Vorbis",
    "mkv":       "MKV — H.264 + AAC",
}

CLI_QUALITY_MAP: dict[str, str] = {
    "veryhigh": "Very High  (CRF 16)",
    "high":     "High       (CRF 20)",
    "medium":   DEFAULT_QUALITY,
    "low":      "Low        (CRF 28)",
    "verylow":  "Very Low   (CRF 32)",
}

_CLI_EPILOG = """\
Format aliases  (-f / --format):
  mp4        MP4 — H.264 + AAC   [default]
  mp4-aac    MP4 — H.264 + AAC
  mp4-opus   MP4 — H.264 + Opus
  webm       WebM — VP9 + Opus
  webm-vp9   WebM — VP9 + Opus
  webm-vp8   WebM — VP8 + Vorbis
  mkv        MKV — H.264 + AAC

Quality presets  (-q / --quality):
  veryhigh   CRF 16, slow preset
  high       CRF 20, medium preset
  medium     CRF 23, medium preset   [default]
  low        CRF 28, fast preset
  verylow    CRF 32, veryfast preset

Hardware acceleration  (--hw-accel):
  auto           Auto-detect best available backend   [default]
  none           Software encoding (disable HW accel)
  nvenc          NVIDIA NVENC
  vaapi          VAAPI — Intel / AMD (Linux)
  videotoolbox   Apple VideoToolbox (macOS)
  qsv            Intel Quick Sync
  amf            AMD AMF
"""

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
    progress:  float = 0.0
    error:     str   = ""
    vcodec:    str   = ""
    acodec:    str   = ""
    container: str   = ""
    duration:  float = 0.0
    out_size:  int   = 0    # bytes written to dst (filled on success)


@dataclass
class Msg:
    kind: str                    # log | job_start | job_progress | job_done | all_done
    job:  Optional[Job] = None
    text: str = ""
    tag:  str = ""               # optional log-colour override


# ──────────────────────────────────────────────────────────── ffmpeg helpers ──

def find_ffmpeg() -> tuple[str, str]:
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
    try:
        r = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=10)
        return r.stdout.splitlines()[0] if r.stdout else "unknown version"
    except Exception as e:
        return f"(could not read version: {e})"


def _find_vaapi_device() -> str:
    """Return the first available VAAPI render node, or empty string."""
    for i in range(8):
        p = f"/dev/dri/renderD{128 + i}"
        if os.path.exists(p):
            return p
    return ""


# Preference order for auto-detection (first match wins).
# Each tuple is (backend_key, encoder_name_to_look_for_in_ffmpeg_output).
_HW_DETECT_ORDER = [
    ("nvenc",        "h264_nvenc"),
    ("vaapi",        "h264_vaapi"),
    ("videotoolbox", "h264_videotoolbox"),
    ("qsv",          "h264_qsv"),
    ("amf",          "h264_amf"),
]


def _detect_hw_accel(ffmpeg: str) -> str:
    """Return the best available HW-accel backend key, or "" for software.

    Queries `ffmpeg -encoders` and returns the first backend whose H.264
    encoder appears in the output.  Being listed there means the encoder was
    compiled in; in practice this is a reliable proxy for hardware being present.
    """
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        listed = r.stdout
    except Exception:
        return ""
    for backend, encoder in _HW_DETECT_ORDER:
        if encoder in listed:
            return backend
    return ""


def probe(ffprobe_path: str, src: Path) -> tuple[str, str, str, float]:
    """Return (vcodec, acodec, container, duration_sec). Raises ValueError on error."""
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
        raise ValueError(r.stderr.strip()[:300] or "ffprobe returned non-zero")

    data      = json.loads(r.stdout)
    fmt_info  = data.get("format", {})
    streams   = data.get("streams", [])
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
    hw_accel:    str = "",
) -> list[str]:
    """Assemble the ffmpeg command for one transcoding job."""
    crf    = quality["crf"]
    preset = quality["preset"]
    sw_vcodec = fmt["vcodec"]

    # Resolve hardware encoder (None = fall back to software)
    hw_enc: Optional[str] = None
    if hw_accel and hw_accel in HW_ACCEL_BACKENDS:
        hw_enc = HW_ACCEL_BACKENDS[hw_accel].get(sw_vcodec)

    # ── video args ─────────────────────────────────────────────────────────
    extra_input: list[str] = []
    vf_args:     list[str] = []

    if job.vcodec in fmt["compat_vcodec"]:
        v_args: list[str] = ["-c:v", "copy"]

    elif hw_enc:
        if hw_accel == "nvenc":
            v_args = ["-c:v", hw_enc, "-rc:v", "vbr", "-cq:v", crf, "-preset:v", "p5"]
        elif hw_accel == "vaapi":
            dev = _find_vaapi_device()
            if dev:
                extra_input = ["-vaapi_device", dev]
            vf_args = ["-vf", "format=nv12|vaapi,hwupload"]
            v_args  = ["-c:v", hw_enc, "-qp", crf]
        elif hw_accel == "videotoolbox":
            # VideoToolbox quality is 1–100 (higher = better); invert CRF scale
            q = max(1, min(100, round((51 - int(crf)) / 51 * 100)))
            v_args = ["-c:v", hw_enc, "-q:v", str(q)]
        elif hw_accel == "qsv":
            v_args = ["-c:v", hw_enc, "-global_quality", crf]
        elif hw_accel == "amf":
            v_args = ["-c:v", hw_enc, "-qp_i", crf, "-qp_p", crf]
        else:
            v_args = ["-c:v", hw_enc]
    elif sw_vcodec == "libvp9":
        v_args = ["-c:v", "libvp9", "-crf", crf, "-b:v", "0",
                  "-deadline", "good", "-cpu-used", "2", "-row-mt", "1"]
    elif sw_vcodec == "libvp8":
        v_args = ["-c:v", "libvp8", "-crf", crf, "-b:v", "0"]
    else:
        v_args = ["-c:v", sw_vcodec, "-crf", crf, "-preset", preset]

    # ── audio args ─────────────────────────────────────────────────────────
    if job.acodec in fmt["compat_acodec"]:
        a_args: list[str] = ["-c:a", "copy"]
    else:
        ac     = fmt["acodec"]
        bitrate = "192k" if ac == "aac" else "128k"
        a_args  = ["-c:a", ac, "-b:a", bitrate]

    # ── mux args: drop -pix_fmt when VAAPI (output is already in hw format) ──
    mux = [a for a in fmt["mux_args"] if not (hw_accel == "vaapi" and a == "yuv420p")]

    return [
        ffmpeg_path, "-y",
        *extra_input,
        "-i", str(job.src),
        "-map", "0:v:0?",
        "-map", "0:a?",
        *v_args,
        *a_args,
        *vf_args,
        *mux,
        "-threads", str(threads),
        "-progress", "pipe:1",
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
        dry_run:          bool = False,
        in_place:         bool = False,
        hw_accel:         str  = "",
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
        self.dry_run          = dry_run
        self.in_place         = in_place
        self.hw_accel         = hw_accel
        # NOTE: named _halt (not _stop) to avoid shadowing threading.Thread._stop()
        # which Python 3.12 calls internally — shadowing it with an Event causes
        # "TypeError: 'Event' object is not callable" on worker.join().
        self._halt = threading.Event()
        self._proc: Optional[subprocess.Popen] = None

    # ── helpers ───────────────────────────────────────────────────────────

    def _maybe_delete(self, path: Path) -> None:
        if not self.delete_originals:
            return
        try:
            path.unlink()
        except Exception as exc:
            self.send(Msg("log", text=f"WARN  Could not delete {path.name}: {exc}",
                          tag="warn"))

    # ── public control ─────────────────────────────────────────────────────

    def stop(self) -> None:
        self._halt.set()
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    # ── thread entry ───────────────────────────────────────────────────────

    def run(self) -> None:
        for job in self.jobs:
            if self._halt.is_set():
                break
            self._process(job)
        self.send(Msg("all_done"))

    # ── per-job processing ─────────────────────────────────────────────────

    def _process(self, job: Job) -> None:  # noqa: C901
        fmt = self.fmt

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

        if not job.vcodec:
            job.status = Status.SKIPPED
            self.send(Msg("job_done", job,
                          f"WARN  {job.src.name} — no video stream, skipping",
                          tag="warn"))
            return

        compatible = is_compatible(job, fmt)

        # 2. Dry-run — report intent, touch nothing ─────────────────────────
        if self.dry_run:
            src_sz = _fmt_size(job.src.stat().st_size)
            if compatible:
                if self.in_place and job.dst == job.src:
                    action = (f"DRY   {job.src.name}  [{src_sz}]"
                              f"  → already in place & compatible, would skip")
                else:
                    action = (f"DRY   {job.src.name}  [{src_sz}]"
                              f"  → would COPY  ({job.vcodec}/{job.acodec})")
            else:
                action = (f"DRY   {job.src.name}  [{src_sz}]"
                          f"  → would TRANSCODE"
                          f"  [{job.vcodec}/{job.acodec}"
                          f" → {fmt['vcodec']}/{fmt['acodec']}]")
            job.status   = Status.DONE
            job.progress = 100.0
            self.send(Msg("job_done", job, action, tag="dry"))
            return

        # 3. Compatible file — copy (or skip if already in place) ────────────
        if compatible:
            if self.in_place and job.dst == job.src:
                job.status   = Status.DONE
                job.progress = 100.0
                self.send(Msg("job_done", job,
                              f"SKIP  {job.src.name}  (in-place, already compatible)",
                              tag="skip"))
                return
            try:
                job.dst.parent.mkdir(parents=True, exist_ok=True)
                _copy_file(job.src, job.dst)
                job.status   = Status.DONE
                job.progress = 100.0
                job.out_size = job.dst.stat().st_size
                self.send(Msg("job_done", job,
                              f"COPY  {job.src.name}  "
                              f"(already compatible: {job.vcodec}/{job.acodec}"
                              f" in {job.container})",
                              tag="skip"))
                self._maybe_delete(job.src)
            except Exception as exc:
                job.status = Status.FAILED
                job.error  = str(exc)
                self.send(Msg("job_done", job, f"FAIL  {job.src.name} — copy error: {exc}"))
            return

        # 4. Create output directory ────────────────────────────────────────
        try:
            job.dst.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            job.status = Status.FAILED
            job.error  = str(exc)
            self.send(Msg("job_done", job, f"FAIL  {job.src.name} — mkdir: {exc}"))
            return

        # 5. In-place with same extension → transcode to temp, then replace ──
        real_dst  = job.dst
        use_tmp   = self.in_place and job.dst == job.src
        if use_tmp:
            job.dst = job.src.with_name(job.src.stem + "._ult_." + fmt["ext"])

        # 6. Build command and start ────────────────────────────────────────
        cmd = build_cmd(self.ffmpeg, job, fmt, self.quality,
                        self.threads, self.hw_accel)
        job.status = Status.RUNNING

        hw_label = f" [{self.hw_accel.upper()}]" if self.hw_accel else ""
        self.send(Msg("job_start", job,
                      f"START {job.src.name}  →  {real_dst.name}{hw_label}"
                      f"  [{job.vcodec}/{job.acodec}"
                      f" → {fmt['vcodec']}/{fmt['acodec']}]"))

        # 7. Run ffmpeg, stream progress ─────────────────────────────────────
        # stderr is captured in a background thread so the pipe never blocks
        # while we read stdout for progress updates.
        stderr_lines: list[str] = []

        def _drain_stderr(pipe: Any) -> None:
            for ln in pipe:
                stderr_lines.append(ln.rstrip())

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,   # captured, not discarded
                text=True,
                bufsize=1,
            )
            _et = threading.Thread(
                target=_drain_stderr, args=(self._proc.stderr,), daemon=True
            )
            _et.start()

            dur_us = job.duration * 1_000_000

            for line in self._proc.stdout:       # type: ignore[union-attr]
                if self._halt.is_set():
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
            _et.join(timeout=3)
            rc = self._proc.returncode
        except Exception as exc:
            job.status = Status.FAILED
            job.error  = str(exc)
            self.send(Msg("job_done", job, f"FAIL  {job.src.name}: {exc}"))
            return
        finally:
            self._proc = None

        # 8. Handle stop / result ───────────────────────────────────────────
        if self._halt.is_set():
            try:
                if job.dst.exists():
                    job.dst.unlink()
            except Exception:
                pass
            return

        if rc == 0:
            # Atomic replace for in-place same-extension case
            if use_tmp:
                try:
                    real_dst.unlink()
                    job.dst.replace(real_dst)
                except Exception as exc:
                    job.status = Status.FAILED
                    job.error  = str(exc)
                    self.send(Msg("job_done", job,
                                  f"FAIL  {job.src.name} — replace failed: {exc}"))
                    return
            job.dst      = real_dst
            job.status   = Status.DONE
            job.progress = 100.0
            try:
                job.out_size = job.dst.stat().st_size
            except OSError:
                pass
            self.send(Msg("job_done", job, f"DONE  {job.src.name}"))
            if not use_tmp:   # original already gone for same-ext in-place
                self._maybe_delete(job.src)
        else:
            if use_tmp and job.dst.exists():
                try:
                    job.dst.unlink()
                except Exception:
                    pass
            job.dst    = real_dst
            job.status = Status.FAILED
            job.error  = f"ffmpeg exit code {rc}"
            # Emit the last few non-empty stderr lines so the user can see
            # exactly why ffmpeg failed (e.g. "No NVENC capable devices found").
            relevant = [l for l in stderr_lines if l.strip()][-6:]
            for l in relevant:
                self.send(Msg("log", text=f"       {l}", tag="fail"))
            self.send(Msg("job_done", job, f"FAIL  {job.src.name}  (exit code {rc})"))


# ──────────────────────────────────────────────────────────── shared helpers ──

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _fmt_size(n: int) -> str:
    """Human-readable size using SI units (1 KB = 1000 B)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000:
            return f"{n:.1f} {unit}"
        n /= 1000.0  # type: ignore[assignment]
    return f"{n:.1f} PB"


def _copy_file(src: Path, dst: Path) -> None:
    """Copy src → dst. Tries a hard link first (instant on same filesystem),
    falls back to a full byte copy."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _open_folder(path: str) -> None:
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(path)                   # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


# ──────────────────────────────────────────────────────────── CLI handler ───

class CLIHandler:
    """Translates Worker Msg objects into coloured terminal output."""

    _ANSI: dict[str, str] = {
        "info":  "\033[93m",    # yellow
        "start": "\033[94m",    # blue
        "skip":  "\033[90m",    # grey
        "done":  "\033[92m",    # green
        "fail":  "\033[91m",    # red
        "warn":  "\033[33m",    # orange
        "dry":   "\033[96m",    # cyan
        "reset": "\033[0m",
    }

    def __init__(self, total: int, use_color: bool) -> None:
        self.total          = total
        self.n_done         = 0
        self.n_skip         = 0
        self.n_fail         = 0
        self.in_bytes       = 0
        self.out_bytes      = 0
        self._color         = use_color
        self._in_prog       = False
        self._lock          = threading.Lock()

    def handle(self, msg: Msg) -> None:
        with self._lock:
            self._handle(msg)

    def _c(self, tag: str, text: str) -> str:
        if not self._color or tag not in self._ANSI:
            return text
        return f"{self._ANSI[tag]}{text}{self._ANSI['reset']}"

    def _erase_progress(self) -> None:
        if self._in_prog:
            print("\r" + " " * 55 + "\r", end="", flush=True)
            self._in_prog = False

    def _handle(self, msg: Msg) -> None:    # noqa: C901
        job = msg.job

        if msg.kind == "log":
            self._erase_progress()
            print(self._c(msg.tag or "", f"[{_ts()}] {msg.text}"))

        elif msg.kind == "job_start":
            self._erase_progress()
            print(self._c("start", f"[{_ts()}] {msg.text}"))

        elif msg.kind == "job_progress":
            assert job is not None
            pct    = job.progress
            w      = 32
            filled = int(w * pct / 100)
            bar    = "\u2588" * filled + "\u2591" * (w - filled)
            print(f"\r  [{bar}] {pct:5.1f}%", end="", flush=True)
            self._in_prog = True

        elif msg.kind == "job_done":
            assert job is not None
            self._erase_progress()

            if msg.tag and job.status == Status.DONE:
                tag = msg.tag       # COPY, DRY, or other override
                self.n_done += 1
            elif msg.tag:
                tag = msg.tag       # WARN or other skip-level override
                self.n_skip += 1
            elif job.status == Status.SKIPPED:
                tag = "skip"
                self.n_skip += 1
            elif job.status == Status.DONE:
                tag = "done"
                self.n_done += 1
            else:
                tag = "fail"
                self.n_fail += 1

            # Track input/output sizes for done jobs
            if job.status == Status.DONE and not msg.tag == "dry":
                try:
                    self.in_bytes += job.src.stat().st_size
                except OSError:
                    pass
                self.out_bytes += job.out_size

            completed = self.n_done + self.n_skip + self.n_fail
            print(self._c(tag, f"[{_ts()}] {msg.text}  [{completed}/{self.total}]"))

        elif msg.kind == "all_done":
            self._erase_progress()
            lines = [
                f"\n\u2500\u2500 All done \u2500\u2500  "
                f"Converted: {self.n_done}   "
                f"Skipped: {self.n_skip}   "
                f"Failed: {self.n_fail}",
            ]
            if self.in_bytes or self.out_bytes:
                lines.append(
                    f"Input: {_fmt_size(self.in_bytes)}"
                    f"  →  Output: {_fmt_size(self.out_bytes)}"
                )
            print(self._c("info", "\n".join(lines)))


# _AppBase: tk.Tk when tkinter available, plain object otherwise.
_AppBase: type = tk.Tk if _HAS_TK else object  # type: ignore[union-attr]


# ──────────────────────────────────────────────────────────────────── App ────

class App(_AppBase):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME}  v{VERSION}")
        self.resizable(True, True)
        self.minsize(720, 600)

        try:
            self._ffmpeg, self._ffprobe = find_ffmpeg()
        except RuntimeError as exc:
            self.withdraw()
            messagebox.showerror("FFmpeg not found", str(exc))
            self.destroy()
            return

        self._jobs:           List[Job]              = []
        self._file_selection: List[Path]             = []  # explicitly picked files
        self._worker:         Optional[Worker]       = None
        self._queue:          queue.SimpleQueue[Msg] = queue.SimpleQueue()
        self._n_done = self._n_skip = self._n_fail = 0
        self._in_bytes = self._out_bytes = 0

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        ver = ffmpeg_version(self._ffmpeg)
        self._log_write(f"[{_ts()}] {APP_NAME} v{VERSION} ready\n", "info")
        self._log_write(f"[{_ts()}] {ver}\n", "info")
        self._log_write(f"[{_ts()}] ffmpeg:  {self._ffmpeg}\n", "info")
        self._log_write(f"[{_ts()}] ffprobe: {self._ffprobe}\n", "info")

        # Auto-detect hardware acceleration and pre-select in the dropdown.
        detected = _detect_hw_accel(self._ffmpeg)
        if detected:
            self._hw_var.set(HW_ACCEL_LABELS[detected])
            self._log_write(
                f"[{_ts()}] HW accel auto-detected: {HW_ACCEL_LABELS[detected]}\n",
                "info",
            )
        else:
            self._log_write(
                f"[{_ts()}] No hardware acceleration detected — using software encoding\n",
                "info",
            )
        self._log_write("\n", "info")

        self.after(80, self._poll)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        PAD = {"padx": 8, "pady": 4}

        # ── Folders ─────────────────────────────────────────────────────────
        pf = ttk.LabelFrame(self, text=" Folders ")
        pf.pack(fill="x", **PAD)

        self._in_var  = tk.StringVar()
        self._out_var = tk.StringVar()

        ttk.Label(pf, text="Input:").grid(
            row=0, column=0, sticky="w", padx=8, pady=3)
        self._inp_entry = ttk.Entry(pf, textvariable=self._in_var)
        self._inp_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        # Two browse buttons in a sub-frame so the column width stays fixed
        _ibf = ttk.Frame(pf)
        _ibf.grid(row=0, column=2, padx=8, pady=3)
        ttk.Button(_ibf, text="Folder…",  width=8,
                   command=self._browse_in_folder).pack(side="left", padx=(0, 2))
        ttk.Button(_ibf, text="File(s)…", width=8,
                   command=self._browse_in_files).pack(side="left")

        ttk.Label(pf, text="Output folder:").grid(
            row=1, column=0, sticky="w", padx=8, pady=3)
        self._out_entry = ttk.Entry(pf, textvariable=self._out_var)
        self._out_entry.grid(row=1, column=1, sticky="ew", padx=4, pady=3)
        self._btn_browse_out = ttk.Button(pf, text="Browse…", width=9,
                                          command=self._browse_out)
        self._btn_browse_out.grid(row=1, column=2, padx=8, pady=3)

        pf.columnconfigure(1, weight=1)

        # ── Settings ────────────────────────────────────────────────────────
        sf = ttk.LabelFrame(self, text=" Settings ")
        sf.pack(fill="x", **PAD)

        self._fmt_var  = tk.StringVar(value=DEFAULT_FORMAT)
        self._qual_var = tk.StringVar(value=DEFAULT_QUALITY)
        self._thr_var  = tk.IntVar(value=DEFAULT_THREADS)
        self._rec_var  = tk.BooleanVar(value=True)
        self._del_var  = tk.BooleanVar(value=False)
        self._inp_var  = tk.BooleanVar(value=False)   # in-place
        self._dry_var  = tk.BooleanVar(value=False)   # dry-run
        self._hw_var   = tk.StringVar(value="")       # hw-accel key

        ttk.Label(sf, text="Output format:").grid(
            row=0, column=0, sticky="w", padx=8, pady=3)
        ttk.Combobox(
            sf, textvariable=self._fmt_var,
            values=list(OUTPUT_FORMATS), state="readonly", width=44,
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

        ttk.Label(sf, text="HW Accel:").grid(
            row=2, column=0, sticky="w", padx=8, pady=3)
        ttk.Combobox(
            sf, textvariable=self._hw_var,
            values=list(HW_ACCEL_LABELS.values()), state="readonly", width=34,
        ).grid(row=2, column=1, columnspan=3, sticky="ew", padx=4, pady=3)
        self._hw_var.set(HW_ACCEL_LABELS[""])   # default: None (software)

        # Row 3: two checkboxes side by side
        ttk.Checkbutton(sf, text="Recursive scan",
                        variable=self._rec_var).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 0))
        ttk.Checkbutton(sf, text="In-place (overwrite originals)",
                        variable=self._inp_var,
                        command=self._on_inplace_toggle).grid(
            row=3, column=2, columnspan=2, sticky="w", padx=8, pady=(2, 0))

        # Row 4: two checkboxes side by side
        self._chk_del = ttk.Checkbutton(sf, text="Delete originals after conversion",
                                        variable=self._del_var)
        self._chk_del.grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))
        ttk.Checkbutton(sf, text="Dry run (no files written)",
                        variable=self._dry_var).grid(
            row=4, column=2, columnspan=2, sticky="w", padx=8, pady=(0, 4))

        sf.columnconfigure(1, weight=1)

        # ── Action buttons ───────────────────────────────────────────────────
        bf = ttk.Frame(self)
        bf.pack(fill="x", padx=8, pady=(2, 0))

        self._btn_scan  = ttk.Button(bf, text="  Scan Files  ", command=self._scan)
        self._btn_start = ttk.Button(bf, text="  ▶  Start  ",   command=self._start,
                                     state="disabled")
        self._btn_stop  = ttk.Button(bf, text="  ■  Stop  ",    command=self._gui_stop,
                                     state="disabled")
        self._btn_open  = ttk.Button(bf, text="  Open Output Folder  ",
                                     command=self._open_output, state="disabled")

        self._btn_scan.pack(side="left", padx=(0, 4))
        self._btn_start.pack(side="left", padx=4)
        self._btn_stop.pack(side="left", padx=4)
        self._btn_open.pack(side="right", padx=(4, 0))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=8, pady=6)

        # ── Progress ─────────────────────────────────────────────────────────
        pf2 = ttk.Frame(self)
        pf2.pack(fill="x", padx=8)

        self._stat_var = tk.StringVar(value="Ready.  Select a folder and click Scan Files.")
        ttk.Label(pf2, textvariable=self._stat_var, anchor="w").pack(fill="x")

        overall_row = ttk.Frame(pf2)
        overall_row.pack(fill="x", pady=(4, 0))
        ttk.Label(overall_row, text="Overall: ", width=10, anchor="w").pack(side="left")
        self._bar_overall = ttk.Progressbar(overall_row, mode="determinate")
        self._bar_overall.pack(side="left", fill="x", expand=True)
        self._bar_overall_lbl = tk.StringVar(value="")
        ttk.Label(overall_row, textvariable=self._bar_overall_lbl,
                  width=10, anchor="e").pack(side="left", padx=(6, 0))

        self._cur_var = tk.StringVar(value="")
        ttk.Label(pf2, textvariable=self._cur_var,
                  anchor="w", foreground="#666666").pack(fill="x", pady=(2, 0))

        file_row = ttk.Frame(pf2)
        file_row.pack(fill="x", pady=(2, 0))
        ttk.Label(file_row, text="File:    ", width=10, anchor="w").pack(side="left")
        self._bar_file = ttk.Progressbar(file_row, mode="determinate", maximum=100)
        self._bar_file.pack(side="left", fill="x", expand=True)
        self._bar_file_lbl = tk.StringVar(value="")
        ttk.Label(file_row, textvariable=self._bar_file_lbl,
                  width=10, anchor="e").pack(side="left", padx=(6, 0))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=8, pady=6)

        # ── Log ──────────────────────────────────────────────────────────────
        lf = ttk.LabelFrame(self, text=" Log ")
        lf.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        ttk.Button(lf, text="Clear", width=6,
                   command=self._log_clear).pack(anchor="ne", padx=4, pady=2)

        self._log = ScrolledText(
            lf, height=10, wrap="none",
            font=("Courier New", 9), state="disabled",
            background="#1e1e1e", foreground="#d4d4d4",
            insertbackground="#d4d4d4", selectbackground="#264f78",
        )
        self._log.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        self._log.tag_config("info",  foreground="#dcdcaa")
        self._log.tag_config("start", foreground="#569cd6")
        self._log.tag_config("skip",  foreground="#888888")
        self._log.tag_config("done",  foreground="#4ec94e")
        self._log.tag_config("fail",  foreground="#f44747")
        self._log.tag_config("warn",  foreground="#ce9178")
        self._log.tag_config("dry",   foreground="#4fc1ff")

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_inplace_toggle(self) -> None:
        """Grey out output folder and 'delete originals' when in-place is active."""
        in_place = self._inp_var.get()
        folder_state = "disabled" if in_place else "normal"
        self._out_entry.config(state=folder_state)
        self._btn_browse_out.config(state=folder_state)
        # "Delete originals" is redundant in-place (the original is already
        # overwritten), so disable it and uncheck it to avoid confusion.
        if in_place:
            self._del_var.set(False)
            self._chk_del.config(state="disabled")
        else:
            self._chk_del.config(state="normal")

    def _browse_in_folder(self) -> None:
        d = filedialog.askdirectory(title="Select input folder")
        if not d:
            return
        # Clear any previous file selection and restore the entry to normal
        self._file_selection = []
        self._inp_entry.config(state="normal")
        self._in_var.set(d)
        if not self._out_var.get().strip() and not self._inp_var.get():
            p = Path(d)
            self._out_var.set(str(p.parent / f"{p.name}_transcoded"))

    def _browse_in_files(self) -> None:
        raw = filedialog.askopenfilenames(
            title="Select video file(s)",
            filetypes=[
                ("Video files",
                 " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))),
                ("All files", "*.*"),
            ],
        )
        if not raw:
            return
        paths = [Path(f) for f in raw
                 if Path(f).suffix.lower() in VIDEO_EXTENSIONS]
        if not paths:
            messagebox.showwarning(
                "No video files",
                "None of the selected files are recognised video formats."
            )
            return

        self._file_selection = paths
        # Make the entry read-only — content is a display summary only
        self._inp_entry.config(state="readonly")

        if len(paths) == 1:
            self._in_var.set(str(paths[0]))
        else:
            try:
                common = Path(os.path.commonpath([str(p.parent) for p in paths]))
            except ValueError:
                common = paths[0].parent
            self._in_var.set(f"{len(paths)} files  —  {common}")

        # Auto-suggest output folder if not already set
        if not self._out_var.get().strip() and not self._inp_var.get():
            try:
                base = Path(os.path.commonpath([str(p.parent) for p in paths]))
            except ValueError:
                base = paths[0].parent
            self._out_var.set(str(base.parent / f"{base.name}_transcoded"))

    def _browse_out(self) -> None:
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self._out_var.set(d)

    def _open_output(self) -> None:
        if self._inp_var.get():   # in-place: open the source location
            if self._file_selection:
                _open_folder(str(self._file_selection[0].parent))
            else:
                _open_folder(self._in_var.get().strip())
        else:
            _open_folder(self._out_var.get().strip())

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _scan(self) -> None:   # noqa: C901
        in_place = self._inp_var.get()
        fmt      = OUTPUT_FORMATS[self._fmt_var.get()]
        ext      = fmt["ext"]

        if self._file_selection:
            # ── File-selection mode ───────────────────────────────────────
            sources = [f for f in self._file_selection if f.is_file()]
            if not sources:
                messagebox.showerror("Error",
                                     "None of the selected files could be found.")
                return

            if in_place:
                out_dir: Optional[Path] = None
            else:
                out_str = self._out_var.get().strip()
                if not out_str:
                    messagebox.showerror("Error",
                                         "Please specify an output folder.")
                    return
                out_dir = Path(out_str)

            self._jobs = []
            for src in sorted(sources):
                dst = (src.parent if in_place
                       else out_dir) / src.with_suffix(f".{ext}").name  # type: ignore[operator]
                self._jobs.append(Job(src=src, dst=dst))

            log_header = (f"[{_ts()}] {len(sources)} file(s) selected\n"
                          if len(sources) > 1 else
                          f"[{_ts()}] File: {sources[0]}\n")
            log_show = lambda j: str(j.src)   # show full path for explicit files

        else:
            # ── Folder mode ───────────────────────────────────────────────
            in_dir = Path(self._in_var.get().strip())
            if not in_dir.is_dir():
                messagebox.showerror("Error", "Input folder does not exist.")
                return

            if in_place:
                out_dir = None
                _out_for_check = in_dir
            else:
                out_str = self._out_var.get().strip()
                if not out_str:
                    messagebox.showerror("Error",
                                         "Please specify an output folder.")
                    return
                out_dir = Path(out_str)
                _out_for_check = out_dir
                if in_dir == out_dir:
                    messagebox.showerror(
                        "Error", "Input and output folders must be different.")
                    return
                try:
                    out_dir.relative_to(in_dir)
                    messagebox.showerror(
                        "Error",
                        "Output folder must not be inside the input folder.")
                    return
                except ValueError:
                    pass

            pattern = "**/*" if self._rec_var.get() else "*"
            files   = sorted(
                p for p in in_dir.glob(pattern)
                if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
            )
            if not files:
                messagebox.showinfo(
                    "No files found",
                    "No video files were found in the selected folder.")
                return

            self._jobs = []
            for src in files:
                rel = src.relative_to(in_dir)
                dst = (in_dir if in_place
                       else out_dir) / rel.with_suffix(f".{ext}")  # type: ignore[operator]
                self._jobs.append(Job(src=src, dst=dst))

            log_header = f"[{_ts()}] Scanned:  {in_dir}\n"
            log_show = lambda j: str(j.src.relative_to(in_dir))
            out_dir = out_dir  # may be None for in-place

        # ── Shared post-scan UI update ─────────────────────────────────────
        self._n_done = self._n_skip = self._n_fail = 0
        self._in_bytes = self._out_bytes = 0
        self._bar_overall["value"]   = 0
        self._bar_overall["maximum"] = len(self._jobs)
        self._bar_overall_lbl.set(f"0 / {len(self._jobs)}")
        self._bar_file["value"] = 0
        self._bar_file_lbl.set("")
        self._cur_var.set("")

        total_bytes = sum(j.src.stat().st_size for j in self._jobs)

        self._log_clear()
        self._log_write(log_header, "info")
        if not in_place and out_dir:
            self._log_write(f"[{_ts()}] Output:   {out_dir}\n", "info")
        self._log_write(
            f"[{_ts()}] Found {len(self._jobs)} file(s)  "
            f"({_fmt_size(total_bytes)} total):\n\n",
            "info",
        )
        for j in self._jobs:
            self._log_write(
                f"  {log_show(j)}  ({_fmt_size(j.src.stat().st_size)})\n"
            )

        mode = " [DRY RUN]" if self._dry_var.get() else ""
        self._stat_var.set(
            f"{len(self._jobs)} file(s)  ({_fmt_size(total_bytes)})"
            f"{mode} — click  ▶ Start  to begin."
        )
        self._btn_start.config(state="normal")
        self._btn_open.config(state="disabled")

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def _start(self) -> None:
        if not self._jobs:
            return

        fmt      = OUTPUT_FORMATS[self._fmt_var.get()]
        quality  = QUALITY_PRESETS[self._qual_var.get()]
        threads  = max(1, self._thr_var.get())
        in_place = self._inp_var.get()
        dry_run  = self._dry_var.get()
        delete   = self._del_var.get()

        # Resolve HW accel key from display label
        label_to_key = {v: k for k, v in HW_ACCEL_LABELS.items()}
        hw_accel = label_to_key.get(self._hw_var.get(), "")

        self._n_done = self._n_skip = self._n_fail = 0
        self._in_bytes = self._out_bytes = 0
        self._bar_overall["value"]   = 0
        self._bar_overall["maximum"] = len(self._jobs)
        self._bar_overall_lbl.set(f"0 / {len(self._jobs)}")
        self._bar_file["value"] = 0
        self._bar_file_lbl.set("")

        flags = []
        if dry_run:   flags.append("DRY RUN")
        if in_place:  flags.append("in-place")
        if delete:    flags.append("delete originals")
        if hw_accel:  flags.append(f"HW: {hw_accel.upper()}")
        flag_str = f"  [{', '.join(flags)}]" if flags else ""

        self._log_clear()
        self._log_write(
            f"[{_ts()}] Starting — {len(self._jobs)} file(s){flag_str}\n"
            f"[{_ts()}] Format: {self._fmt_var.get()}  |  "
            f"Quality: {self._qual_var.get()}  |  Threads: {threads}\n\n",
            "info",
        )

        if delete and not dry_run:
            self._log_write(f"[{_ts()}] ⚠  Delete originals is ON\n", "warn")

        self._btn_scan.config(state="disabled")
        self._btn_start.config(state="disabled")
        self._btn_stop.config(state="normal")
        self._btn_open.config(state="disabled")
        self._stat_var.set("Running…")

        for job in self._jobs:
            job.status   = Status.PENDING
            job.progress = 0.0
            job.error    = ""
            job.out_size = 0

        self._worker = Worker(
            jobs             = list(self._jobs),
            ffmpeg           = self._ffmpeg,
            ffprobe          = self._ffprobe,
            fmt              = fmt,
            quality          = quality,
            threads          = threads,
            send             = self._queue.put,
            delete_originals = delete,
            dry_run          = dry_run,
            in_place         = in_place,
            hw_accel         = hw_accel,
        )
        self._worker.start()

    def _gui_stop(self) -> None:
        if self._worker and self._worker.is_alive():
            self._worker.stop()
        self._log_write(f"\n[{_ts()}] Stop requested — waiting for current file…\n", "warn")
        self._btn_stop.config(state="disabled")

    # ── Message polling ───────────────────────────────────────────────────────

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self._queue.get_nowait())
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _handle(self, msg: Msg) -> None:   # noqa: C901
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
            if msg.tag and job.status == Status.DONE:
                tag = msg.tag          # COPY / DRY / override
                self._n_done += 1
                if msg.tag != "dry":
                    self._out_bytes += job.out_size
                    try:
                        self._in_bytes += job.src.stat().st_size
                    except OSError:
                        pass
                self._bar_file["value"] = 100
                self._bar_file_lbl.set("100%")
            elif msg.tag:
                tag = msg.tag          # WARN / override
                self._n_skip += 1
            elif job.status == Status.SKIPPED:
                tag = "skip"
                self._n_skip += 1
            elif job.status == Status.DONE:
                tag = "done"
                self._n_done += 1
                self._out_bytes += job.out_size
                try:
                    self._in_bytes += job.src.stat().st_size
                except OSError:
                    pass
                self._bar_file["value"] = 100
                self._bar_file_lbl.set("100%")
            else:
                tag = "fail"
                self._n_fail += 1

            self._log_write(f"[{_ts()}] {msg.text}\n", tag)

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

            size_line = ""
            if self._in_bytes or self._out_bytes:
                size_line = (f"  Input: {_fmt_size(self._in_bytes)}"
                             f"  →  Output: {_fmt_size(self._out_bytes)}\n")

            self._log_write(
                f"\n[{_ts()}] \u2500\u2500 All done \u2500\u2500   "
                f"Converted: {self._n_done}   "
                f"Skipped: {self._n_skip}   "
                f"Failed: {self._n_fail}\n"
                + size_line,
                "info",
            )
            self._stat_var.set(
                f"Finished — "
                f"✓ {self._n_done} converted   "
                f"⤏ {self._n_skip} skipped   "
                f"✗ {self._n_fail} failed"
                + (f"   ({_fmt_size(self._in_bytes)} → {_fmt_size(self._out_bytes)})"
                   if self._in_bytes else "")
            )
            self._btn_scan.config(state="normal")
            self._btn_start.config(state="normal")
            self._btn_stop.config(state="disabled")
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
            if messagebox.askyesno("Quit", "Transcoding is in progress.\nStop and quit?"):
                self._worker.stop()
                self.destroy()
        else:
            self.destroy()


# ───────────────────────────────────────────────────────────────── CLI entry ───

def _collect_jobs(inputs: List[str], out_dir: Optional[Path],
                  fmt: dict, recursive: bool, in_place: bool) -> List[Job]:
    """Expand a list of file/dir paths into a flat Job list."""
    ext   = fmt["ext"]
    jobs: List[Job] = []
    seen: set[Path] = set()

    for raw in inputs:
        p = Path(raw)
        if p.is_file():
            if p.suffix.lower() in VIDEO_EXTENSIONS and p not in seen:
                seen.add(p)
                dst = (p.parent if in_place else out_dir) / p.with_suffix(f".{ext}").name  # type: ignore[operator]
                jobs.append(Job(src=p, dst=dst))
        elif p.is_dir():
            pattern = "**/*" if recursive else "*"
            for src in sorted(p.glob(pattern)):
                if src.is_file() and src.suffix.lower() in VIDEO_EXTENSIONS \
                        and src not in seen:
                    seen.add(src)
                    if in_place:
                        dst = src.parent / src.with_suffix(f".{ext}").name
                    else:
                        assert out_dir is not None
                        dst = out_dir / src.relative_to(p).with_suffix(f".{ext}")
                    jobs.append(Job(src=src, dst=dst))
        else:
            print(f"Warning: '{raw}' is not a file or directory, skipping.",
                  file=sys.stderr)

    return jobs


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description=f"{APP_NAME} v{VERSION}\nConvert video files to browser-compatible formats.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_CLI_EPILOG,
    )
    p.add_argument("-i", "--input",   nargs="+", default=None, metavar="PATH",
                   help="Input file(s) and/or folder(s)")
    p.add_argument("-o", "--output",  default=None, metavar="DIR",
                   help="Output folder (required unless --in-place is set)")
    p.add_argument("-f", "--format",  default="mp4",    metavar="FORMAT",
                   help="Output format alias (default: mp4)")
    p.add_argument("-q", "--quality", default="medium", metavar="QUALITY",
                   help="Quality preset alias (default: medium)")
    p.add_argument("-t", "--threads", default=DEFAULT_THREADS, type=int, metavar="N",
                   help=f"Encoder thread count (default: {DEFAULT_THREADS})")
    p.add_argument("--no-recursive",  action="store_true",
                   help="Do not scan sub-folders (default: recursive)")
    p.add_argument("--delete",        action="store_true",
                   help="Delete original files after successful conversion or copy")
    p.add_argument("--in-place",      action="store_true",
                   help="Convert files in their source directory (no --output needed)")
    p.add_argument("--dry-run",       action="store_true",
                   help="Probe files and report what would happen; write nothing")
    p.add_argument("--hw-accel",      default="auto", metavar="BACKEND",
                   help="Hardware acceleration backend (default: auto)")
    p.add_argument("--list-formats",  action="store_true",
                   help="Print available format/quality/hw options and exit")
    return p.parse_args()


def run_cli(args: argparse.Namespace) -> int:
    """CLI entry point. Returns a POSIX exit code."""

    if args.list_formats:
        print(_CLI_EPILOG)
        return 0

    # ── validate inputs ──────────────────────────────────────────────────────
    if not args.input:
        print("Error: -i/--input is required.  Run with --help for usage.",
              file=sys.stderr)
        return 1

    if args.in_place and args.output:
        print("Error: --in-place and --output are mutually exclusive.", file=sys.stderr)
        return 1

    if not args.in_place and not args.output:
        print("Error: -o/--output is required unless --in-place is set.", file=sys.stderr)
        return 1

    # ── locate ffmpeg ─────────────────────────────────────────────────────────
    try:
        ffmpeg, ffprobe = find_ffmpeg()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"{APP_NAME} v{VERSION}  |  {ffmpeg_version(ffmpeg)}\n")

    # ── resolve output dir ────────────────────────────────────────────────────
    out_dir: Optional[Path] = None
    if not args.in_place:
        out_dir = Path(args.output)
        # Safety: check none of the inputs is inside out_dir and vice-versa
        for raw in args.input:
            p = Path(raw)
            if p.is_dir():
                if p == out_dir:
                    print("Error: Input and output folders must be different.", file=sys.stderr)
                    return 1
                try:
                    out_dir.relative_to(p)
                    print(f"Error: Output folder must not be inside input '{p}'.",
                          file=sys.stderr)
                    return 1
                except ValueError:
                    pass

    # ── resolve format / quality / hw ─────────────────────────────────────────
    fmt_key = CLI_FORMAT_MAP.get(args.format.lower())
    if fmt_key is None:
        print(f"Error: Unknown format '{args.format}'.  Run with --list-formats.",
              file=sys.stderr)
        return 1
    fmt = OUTPUT_FORMATS[fmt_key]

    qual_key = CLI_QUALITY_MAP.get(args.quality.lower())
    if qual_key is None:
        print(f"Error: Unknown quality '{args.quality}'.  Run with --list-formats.",
              file=sys.stderr)
        return 1
    quality = QUALITY_PRESETS[qual_key]
    threads = max(1, args.threads)

    raw_hw = args.hw_accel.lower()
    if raw_hw in ("auto", ""):
        hw_accel = _detect_hw_accel(ffmpeg)
        label = HW_ACCEL_LABELS.get(hw_accel, "software")
        print(f"HW accel: auto-detected → {label}")
    elif raw_hw == "none":
        hw_accel = ""
    else:
        hw_accel = raw_hw
    if hw_accel and hw_accel not in HW_ACCEL_BACKENDS:
        print(f"Error: Unknown hw-accel backend '{hw_accel}'.  "
              f"Valid: auto, none, {', '.join(HW_ACCEL_BACKENDS)}.", file=sys.stderr)
        return 1

    # ── collect jobs ──────────────────────────────────────────────────────────
    jobs = _collect_jobs(
        inputs    = args.input,
        out_dir   = out_dir,
        fmt       = fmt,
        recursive = not args.no_recursive,
        in_place  = args.in_place,
    )
    if not jobs:
        print("No video files found in the specified input(s).")
        return 0

    total_in = sum(j.src.stat().st_size for j in jobs)

    # ── summary ───────────────────────────────────────────────────────────────
    print(f"Input(s): {', '.join(args.input)}")
    if not args.in_place:
        print(f"Output:   {out_dir}")
    print(f"Format:   {args.format}  →  {fmt_key}")
    print(f"Quality:  {args.quality}  →  {qual_key}")
    print(f"Threads:  {threads}")
    if hw_accel:
        print(f"HW accel: {hw_accel.upper()}")
    if args.dry_run:
        print("Mode:     DRY RUN — no files will be written")
    if args.in_place:
        print("Mode:     IN-PLACE — originals will be overwritten")
    if args.delete:
        print("⚠  Delete originals: ON")
    print(f"\nFound {len(jobs)} file(s)  ({_fmt_size(total_in)} total):")
    for j in jobs:
        print(f"  {j.src}  ({_fmt_size(j.src.stat().st_size)})")
    print()

    # ── run worker ────────────────────────────────────────────────────────────
    handler = CLIHandler(total=len(jobs), use_color=sys.stdout.isatty())
    done_ev = threading.Event()

    def _send(msg: Msg) -> None:
        handler.handle(msg)
        if msg.kind == "all_done":
            done_ev.set()

    worker = Worker(
        jobs             = jobs,
        ffmpeg           = ffmpeg,
        ffprobe          = ffprobe,
        fmt              = fmt,
        quality          = quality,
        threads          = threads,
        send             = _send,
        delete_originals = args.delete,
        dry_run          = args.dry_run,
        in_place         = args.in_place,
        hw_accel         = hw_accel,
    )

    worker.start()
    try:
        while not done_ev.wait(timeout=0.25):
            pass
    except KeyboardInterrupt:
        print("\nInterrupted — stopping…")
        worker.stop()
        # Wait for the worker without holding the GIL in a way that blocks signals
        worker.join(timeout=15)
        return 130

    return 1 if handler.n_fail > 0 else 0


# ────────────────────────────────────────────────────────────────────── entry ───

def _has_display() -> bool:
    if platform.system() in ("Windows", "Darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main() -> None:
    if len(sys.argv) > 1:
        sys.exit(run_cli(_parse_args()))

    if not _HAS_TK:
        print(
            f"{APP_NAME}: tkinter is not available.\n"
            "Install it (e.g. 'apt install python3-tk') or use CLI mode.\n"
            "Run with --help for CLI usage.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not _has_display():
        print(
            f"{APP_NAME}: no display found ($DISPLAY / $WAYLAND_DISPLAY not set).\n"
            "Use CLI mode in headless environments.\n"
            "Run with --help for CLI usage.",
            file=sys.stderr,
        )
        sys.exit(1)

    App().mainloop()


if __name__ == "__main__":
    main()
