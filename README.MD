# Adjust EXIF Timestamps with Timezone Correction

This Python script allows you to adjust the EXIF timestamps of image files, including JPEGs and RAW formats. You can modify the timestamps based on a specified timezone adjustment.

## Requirements

- **Python**: Python 3.9 or newer.
- **tqdm**: Used for the progress bar (`pip install tqdm`).
- **ExifTool**: This script requires `exiftool` to be installed. You can download it from [ExifTool's official website](https://exiftool.org/) or install it using a package manager.

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
python timezone-correction.py --dir /path/to/images/ --timezone HH:MM [--negative] [--recursive] [--workers num]
```

**Parameters**:

- `--dir` or `-d`: Directory containing images.
- `--timezone` or `-t`: New timezone offset in HH:MM format, between -12:00 and +14:00. A signed value is also accepted, written with `=` so it is not mistaken for a flag: `--timezone=-06:00`.
- `--negative` or `-n`: Specify this flag for negative timezone offsets (e.g., UTC-06:00). Cannot be combined with a signed `--timezone`.
- `--recursive` or `-r`: Process images in subdirectories recursively.
- `--workers` or `-w`: Number of parallel workers/threads to speed up processing. Default is 1.

**Example:**
- To adjust the timestamps of images in the directory /path/to/images/ to a new timezone offset of UTC-06:00, you would run:

```bash
python timezone-correction.py --dir /path/to/images/ --timezone 06:00 --negative
```
- To specify a positive timezone offset of UTC+02:30, you would run:
    
```bash
python timezone-correction.py --dir /path/to/images/ --timezone 02:30
```

**Supported File Formats**

The script processes files with these extensions (case-insensitive):

- JPEG (.jpg, .jpeg)
- TIFF (.tif, .tiff)
- HEIC (.heic)
- RAW formats (.raw, .cr2, .cr3, .nef, .arw, .raf, .rw2, .orf)

## How It Works

For each image the script reads the EXIF tags `DateTimeOriginal` and `OffsetTimeOriginal` and rewrites both so they describe the
same moment in the new timezone. For example, `10:00 +02:00` becomes `01:30 -06:30`. Sub-second values are kept.

- Images that already have the target offset are skipped, so running the script twice is safe.
- Images without an EXIF `DateTimeOriginal` are skipped, including images that store it only in XMP.
- Other date tags such as `CreateDate` and `ModifyDate` are not changed.
- If an image has no `OffsetTimeOriginal`, its time is assumed to be UTC.
- macOS `._*` metadata files are ignored.

At the end the script prints how many images were updated, skipped and failed. It exits with code 1 if any image
failed.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The end-to-end tests run only when `exiftool` is installed.

## Important Notes
- **Backup:** Files are modified in place and ExifTool's `_original` backup copies are not kept. Back up your images before running the script.
- **EXIF Quality:** Modifying the EXIF data does not affect the image quality, as it only updates the metadata.