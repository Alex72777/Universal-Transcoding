# UL Transcoding

Universal Live Transcoding tool — converts video files to browser-compatible
formats for use with [CopyParty](https://github.com/9001/copyparty) (or any
other web-based file server).

---

## Requirements

| Dependency | Notes |
|---|---|
| **Python 3.8+** | Must include `tkinter` for GUI mode (see below) |
| **ffmpeg** | Must be in your system `PATH` |
| **ffprobe** | Bundled with ffmpeg, also needs to be in `PATH` |

No extra Python packages are needed — only the standard library is used.

### Installing FFmpeg

- **Linux (Debian/Ubuntu):** `sudo apt install ffmpeg`
- **Linux (Arch):** `sudo pacman -S ffmpeg`
- **macOS (Homebrew):** `brew install ffmpeg`
- **Windows:** Download from <https://ffmpeg.org/download.html> and add the
  `bin/` folder to your `PATH`

### tkinter note

`tkinter` is only required for **GUI mode**. CLI mode works without it.
On some Linux distros it is a separate package:

- **Debian/Ubuntu:** `sudo apt install python3-tk`
- **Arch:** `sudo pacman -S tk`

---

## Usage

### GUI mode

Run without any arguments to open the graphical interface:

```
python main.py
```

### CLI mode

Pass at least `-i` and `-o` to run headlessly:

```
python main.py -i <input_folder> -o <output_folder> [options]
```

#### Options

| Flag | Default | Description |
|---|---|---|
| `-i`, `--input DIR` | *(required)* | Input folder to scan for video files |
| `-o`, `--output DIR` | *(required)* | Output folder for converted files |
| `-f`, `--format FORMAT` | `mp4` | Output format alias (see table below) |
| `-q`, `--quality QUALITY` | `medium` | Quality preset alias (see table below) |
| `-t`, `--threads N` | CPU count | Encoder thread count |
| `--no-recursive` | off | Do not scan sub-folders |
| `--delete` | off | Delete original files after successful conversion or copy |
| `--list-formats` | — | Print format and quality options, then exit |

#### Format aliases (`-f`)

| Alias | Format |
|---|---|
| `mp4` / `mp4-aac` | MP4 — H.264 + AAC *(default)* |
| `mp4-opus` | MP4 — H.264 + Opus |
| `webm` / `webm-vp9` | WebM — VP9 + Opus |
| `webm-vp8` | WebM — VP8 + Vorbis |
| `mkv` | MKV — H.264 + AAC |

#### Quality presets (`-q`)

| Alias | CRF | Preset |
|---|---|---|
| `veryhigh` | 16 | slow |
| `high` | 20 | medium |
| `medium` | 23 | medium *(default)* |
| `low` | 28 | fast |
| `verylow` | 32 | veryfast |

#### Examples

```sh
# Convert everything in /videos to MP4, put results in /videos_transcoded
python main.py -i /videos -o /videos_transcoded

# WebM output, high quality, 8 threads, delete originals after conversion
python main.py -i /videos -o /out -f webm -q high -t 8 --delete

# Non-recursive scan, low quality (fast), pipe-friendly (no ANSI colours)
python main.py -i /videos -o /out --no-recursive -q low | tee transcode.log

# List all available format and quality options
python main.py --list-formats
```

---

## How it works

1. **Select / specify an input folder** — the folder containing your video files.
2. **Select / specify an output folder** — where converted files will be written.
   In GUI mode an auto-suggestion (`<input>_transcoded`) is filled in when you
   pick the input folder.
3. **Choose an output format** — defaults to *MP4 — H.264 + AAC*, the most
   widely supported format in browsers.
4. **Adjust quality, thread count, and other options** if needed.
5. In GUI mode, click **Scan Files** to preview the file list, then **▶ Start**
   to begin. In CLI mode, transcoding starts immediately after the summary is
   printed.

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

## Smart copy & stream copy

- Files that **already have the right codecs and container** are **copied
  as-is** (`shutil.copy2`) into the output folder — no re-encoding.
- If only the **video codec already matches**, the video stream is remuxed
  with `-c:v copy` and only the audio is re-encoded (fast, lossless for video).
- Same in reverse: if only the audio codec matches, the audio stream is copied
  and only the video is re-encoded.

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

## Delete originals

Enable **Delete original files after conversion** (GUI checkbox) or pass
`--delete` (CLI) to remove source files after a successful conversion or copy.
Failed jobs are never touched. Deletion errors are reported as warnings but do
not affect the job's success status or exit code.

---

## Stopping

**GUI:** click **■ Stop** to stop after the current file finishes. Any partial
output file is automatically deleted. Click **▶ Start** again to re-run the
full job list.

**CLI:** press `Ctrl+C` to interrupt. The current ffmpeg process is killed, the
partial output file is deleted, and the program exits with code `130`.

---

## Exit codes (CLI)

| Code | Meaning |
|---|---|
| `0` | All files converted or copied successfully |
| `1` | One or more files failed |
| `130` | Interrupted with Ctrl+C |
