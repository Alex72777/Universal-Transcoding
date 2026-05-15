# UL Transcoding

Universal Live Transcoding tool — converts video files to browser-compatible
formats for use with [CopyParty](https://github.com/9001/copyparty) (or any
other web-based file server).

---

## Requirements

| Dependency | Notes |
|---|---|
| **Python 3.8+** | Comes with `tkinter` on most distros |
| **ffmpeg** | Must be in your system `PATH` |
| **ffprobe** | Bundled with ffmpeg, also needs to be in `PATH` |

No extra Python packages are needed — only the standard library is used.

### Installing FFmpeg

- **Linux (Debian/Ubuntu):** `sudo apt install ffmpeg`
- **Linux (Arch):** `sudo pacman -S ffmpeg`
- **macOS (Homebrew):** `brew install ffmpeg`
- **Windows:** Download from <https://ffmpeg.org/download.html> and add the
  `bin/` folder to your `PATH`

---

## Running

```
python main.py
```

---

## How it works

1. **Select an input folder** — the folder containing your video files.
2. **Select an output folder** — where converted files will be written.
   An auto-suggestion (`<input>_transcoded`) is filled in when you pick the
   input folder.
3. **Choose output format** — defaults to *MP4 — H.264 + AAC*, the most
   widely supported format in browsers.
4. **Adjust quality and thread count** if needed.
5. Click **Scan Files** — the app lists all video files it found.
6. Click **▶ Start** — transcoding begins.

---

## Output formats

| Format | Video | Audio | Best for |
|---|---|---|---|
| MP4 — H.264 + AAC *(default)* | H.264 | AAC | Maximum browser compatibility |
| MP4 — H.264 + Opus | H.264 | Opus | Better audio quality/compression |
| WebM — VP9 + Opus | VP9 | Opus | Open format, great compression |
| WebM — VP8 + Vorbis | VP8 | Vorbis | Older open format |
| MKV — H.264 + AAC | H.264 | AAC | MKV container (not all browsers) |

---

## Smart skipping & stream copy

- Files that **already have the right codecs and container** are skipped
  entirely — no re-encoding, no copy.
- If only the **audio needs re-encoding** (video codec already matches),
  the video stream is copied without quality loss (fast).
- Same for the reverse: if only video needs re-encoding, audio is copied.

---

## Folder structure

The output folder mirrors the input folder's structure. For example:

```
Input:   /videos/movies/film.mkv
         /videos/shows/s01e01.avi

Output:  /videos_transcoded/movies/film.mp4
         /videos_transcoded/shows/s01e01.mp4
```

---

## Stopping

Click **■ Stop** to stop after the current file finishes encoding. Any
partial output file is automatically deleted. You can click **▶ Start**
again to re-run the full job list.
