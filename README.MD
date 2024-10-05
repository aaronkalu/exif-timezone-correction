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

To use the script, navigate to the directory containing `time_adjuster.py` and run the following command in your terminal:
  ```bash
  python time_adjuster.py /path/to/images/ -t <time_offset> -tz <timezone_offset>
  ```

**Parameters**:
  path: The path to the directory containing the images you want to adjust.
  -t or --time: Time offset in hours (e.g., 1 for +1 hour).
  -tz or --timezone: New Timezone in hours (e.g., -3 for UTC-3).

Example:
To adjust the timestamps of images in the directory /path/to/images/, with a time adjustment of 2 hours and a new timezone offset of -7, you would run:
  ```bash
    python main.py /path/to/images/ -t 2 -tz -7
    ```

**Supported File Formats**
This script supports various image file formats, including:

- JPEG (.jpg, .jpeg)
- TIFF (.tiff)
- HEIC (.heic)
- RAW formats (e.g., .cr2, .cr3, .nef, .arw, .raf, .rw2, .orf)

**Important Notes**
- Backup: It is recommended to keep a backup of your images before running the script, especially if you are unsure about the changes.
- Quality: Modifying the EXIF data does not affect the image quality, as it only updates the metadata.
