# Adjust EXIF Timestamps with Timezone Correction

This Python script allows you to adjust the EXIF timestamps of image files, including JPEGs and RAW formats. You can modify the timestamps based on a specified timezone adjustment.

## Requirements

- **Python**: Python 3.9 or newer.
- **tqdm**: Used for the progress bar (`pip install tqdm`). On Windows, IANA timezone names also need `pip install tzdata`.
- **ExifTool**: This script requires `exiftool` 12.10 or newer to be installed. You can download it from [ExifTool's official website](https://exiftool.org/) or install it using a package manager.

## Installation Instructions

1. **Install Python**:
   - Download and install Python from [python.org](https://www.python.org/downloads/).
   - Make sure to check the box to add Python to your system PATH during installation.

2. **Install ExifTool**:
   - **Windows**: Download the Windows executable from [here](https://exiftool.org/) and follow the installation instructions.
   - **macOS**: You can install it via Homebrew:
     ```bash
     brew install exiftool
     ```
   - **Linux**: Install via your distribution's package manager (e.g., `apt`, `dnf`):
     ```bash
     sudo apt install libimage-exiftool-perl
     ```

3. **Install the Python dependencies** from the project directory:
   ```bash
   pip install tqdm
   ```
   Alternatively, `pip install .` installs the tool together with its dependencies and adds an
   `exif-timezone-correction` command to your PATH.

## Usage

To use the script, navigate to the directory containing `timezone-correction.py` and run the following command in your terminal:
```bash
python timezone-correction.py --dir /path/to/images/ --timezone ZONE [options]
```

Negative values must be written with `=` so they are not mistaken for flags: `--timezone=-06:00`, `--shift=-1:00`.

**What to change:**

- `--timezone` or `-t`: New timezone. Either a fixed offset between -12:00 and +14:00 (`+02:00`, `-06:30`) or an IANA
  name such as `Europe/Berlin` or `America/New_York`. With a name, the offset is chosen for each photo's own date, so a
  trip across a daylight saving change gets `+01:00` and `+02:00` as appropriate.
- `--mode`: `convert` (default) keeps the moment and changes clock time and offset: `10:00 +02:00` becomes
  `02:00 -06:00`. `relabel` keeps the clock time and only replaces the offset; use it when the camera clock showed the
  right local time but the recorded offset is wrong.
- `--shift`: Move the clock time by `[+|-]HH:MM[:SS]` before anything else, to fix a camera clock that was wrong, for
  example `--shift=-0:03:20` for a clock that ran 3 minutes 20 seconds fast. Each run shifts again, so do not repeat it.
- `--source-timezone` or `-s`: Timezone the camera clock was set to (offset or IANA name), used only for images that
  recorded no `OffsetTimeOriginal`. Without it (or `--gps`) those images are skipped. Given on its own, it fills in the
  missing offsets without moving any times.
- `--gps`: For images without a recorded offset, work out the offset from the GPS timestamp, which is always UTC.
  It is rounded to 15 minutes, so a camera clock a few minutes off is fine.
- `--all-dates`: Also correct `CreateDate` and `ModifyDate` with their offset tags where present, like ExifTool's
  `AllDates`. Without it only `DateTimeOriginal` is changed, and some photo apps that read `CreateDate` may show a
  different time.

**Which images:**

- `--dir` or `-d`: Directory containing images.
- `--recursive` or `-r`: Process images in subdirectories recursively. Symlinked folders are followed.
- `--from` / `--until`: Only images whose original local capture time is in this range, as `YYYY-MM-DD` or
  `"YYYY-MM-DD HH:MM"`. A bare `--until` date includes that whole day.
- `--camera`: Only images whose camera model contains this text (case-insensitive), for shoots with several cameras.
- `--no-sidecars`: Do not update XMP sidecar files (see below).

**Safety and output:**

- `--dry-run`: Show what would change without writing anything.
- `--undo-log PATH` / `--no-undo-log`: Every run that changes files writes an undo log, by default
  `exif-timezone-correction-undo-<date>-<time>.jsonl` in `--dir`.
- `--revert UNDO_LOG`: Restore the values recorded in an undo log. Files changed again since then are skipped.
  A log that several runs appended to (the same `--undo-log`) is undone newest run first.
  Combine with `--dry-run` to preview.
- `--backup`: Keep ExifTool's `<file>_original` backup copies.
- `--report PATH`: Write a CSV with the outcome for every file.
- `--quiet` or `-q`: Print only failures and the summary.
- `--workers` or `-w`: Number of files processed in parallel. Default is the number of CPUs, at most 4.
- `--version`: Print the version.
- `--negative` or `-n`: Deprecated. Marks an unsigned `--timezone` as negative; write the sign instead.

**Examples:**

```bash
# Preview, then move photos into UTC-06:00
python timezone-correction.py --dir /path/to/images/ --timezone=-06:00 --dry-run
python timezone-correction.py --dir /path/to/images/ --timezone=-06:00

# Trip photos from a camera still set to home time (Berlin, no offset recorded), shown in New York time
python timezone-correction.py -d trip/ -s Europe/Berlin -t America/New_York --from 2024-05-01 --until 2024-05-14

# Camera clock ran 1 hour 2 minutes slow on one of two bodies
python timezone-correction.py -d shoot/ --shift 1:02 --camera "EOS R5" --all-dates

# Undo a run
python timezone-correction.py --revert /path/to/images/exif-timezone-correction-undo-20241007-101500.jsonl
```

**Supported File Formats**

The script processes files with these extensions (case-insensitive):

- JPEG (.jpg, .jpeg)
- TIFF (.tif, .tiff)
- HEIC/HEIF and AVIF (.heic, .heif, .hif, .avif)
- PNG and WebP (.png, .webp)
- RAW formats (.raw, .cr2, .cr3, .nef, .nrw, .arw, .raf, .rw2, .orf, .dng, .pef, .srw)

Videos are not supported: QuickTime stores its dates in UTC under different tags.

## How It Works

For each image the script reads the EXIF tags `DateTimeOriginal` and `OffsetTimeOriginal`, and its XMP copy, and
rewrites them as requested. Sub-second values are kept. If the image also has an XMP `DateTimeOriginal`, it is updated
to match.

- Images that already have the requested values are skipped, so running the script twice with the same timezone is safe
  (`--shift` is the exception).
- Images that store the capture time only in XMP are read from XMP and get the EXIF tags added. Images whose offset is
  only in XMP get the EXIF `OffsetTimeOriginal` added.
- Images without any `DateTimeOriginal` are skipped.
- If an image has no `OffsetTimeOriginal` (common on older cameras, which record clock time), the offset from its XMP
  date is used when present, then the GPS time with `--gps`, then `--source-timezone`. Otherwise the image is skipped.
- XMP sidecars are updated together with their image: `photo.cr2.xmp` (darktable) for any image, and `photo.xmp`
  (Lightroom) for RAW files. Only date tags already present in a sidecar are changed.
- The file modification time is kept, so photo libraries and backup tools do not treat the files as new.
- A file reachable through several symlinks is processed once.
- Metadata is read in batches, and ExifTool is kept running per worker instead of being started for every image. A
  call that takes longer than two minutes (for example on a stalled network drive) fails those images.
- macOS `._*` metadata files are ignored.
- With an IANA `--source-timezone`, a clock time that occurs twice when the clocks go back is read as the first
  (daylight saving) one.

At the end the script prints how many images were updated, skipped and failed, and where the undo log is. It exits
with code 1 if any image failed. Pressing Ctrl+C stops after the images currently being written; the undo log covers
everything written up to then.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The end-to-end tests run only when `exiftool` is installed.

## Important Notes
- **Backup:** Files are modified in place. The undo log can restore the dates, and `--backup` keeps full copies, but back up your images before running the script.
- **EXIF Quality:** Modifying the EXIF data does not affect the image quality, as it only updates the metadata.