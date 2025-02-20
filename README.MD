# Adjust EXIF Timestamps with Timezone Correction

This Python script allows you to adjust the EXIF timestamps of image files, including JPEGs and RAW formats. You can modify the timestamps based on a specified timezone adjustment.

## Requirements

- **Python**: Ensure Python 3.x is installed on your system.
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

## Usage

To use the script, navigate to the directory containing `timezone-correction.py` and run the following command in your terminal:
```bash
python timezone-correction.py --dir /path/to/images/ --timezone HH:MM [--negative] [--recursive] [--workers num]
```

**Parameters**:

- `--dir` or `-d`: Directory containing images.
- `--timezone` or `-t`: New timezone offset in HH:MM format.
- `--negative` or `-n`: Specify this flag for negative timezone offsets (e.g., UTC-06:00).
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

**Supported File Formats This script supports various image file formats, including:**

- JPEG (.jpg, .jpeg)
- TIFF (.tiff)
- HEIC (.heic)
- RAW formats (e.g., .raw, .cr2, .cr3, .nef, .arw, .raf, .rw2, .orf)

## Important Notes
- **Backup:** It is recommended to keep a backup of your images before running the script, especially if you are unsure about the changes.
- **EXIF Quality:** Modifying the EXIF data does not affect the image quality, as it only updates the metadata.