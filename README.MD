# EXIF Timestamp Adjuster

This Python script allows you to adjust the EXIF timestamps of image files, including JPEGs and RAW formats. You can modify the timestamps based on a specified time offset and timezone adjustment.

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
python timezone-correction.py /path/to/images/ HH:MM [--negative]
```

**Parameters**:

- path: The path to the directory containing the images you want to adjust.
- timezone: New timezone in the format HH:MM (e.g., 02:00 for UTC+02:00).
- --negative: Specify this flag if the new timezone is negative (default is positive).

Example:
To adjust the timestamps of images in the directory /path/to/images/ to a new timezone offset of -06:00, you would run:

```bash
python timezone-correction.py /path/to/images/ 06:00 --negative
```
To specify a positive timezone offset of +02:30, you would run:
    
```bash
python timezone-correction.py /path/to/images/ 02:30
```

**Supported File Formats This script supports various image file formats, including:**

- JPEG (.jpg, .jpeg)
- TIFF (.tiff)
- HEIC (.heic)
- RAW formats (e.g., .cr2, .cr3, .nef, .arw, .raf, .rw2, .orf)

**Important Notes**
- Backup: It is recommended to keep a backup of your images before running the script, especially if you are unsure about the changes.
- Quality: Modifying the EXIF data does not affect the image quality, as it only updates the metadata.