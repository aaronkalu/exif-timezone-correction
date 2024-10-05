import os
import subprocess
from datetime import datetime, timedelta
import argparse
import sys


def check_exiftool_installed():
    try:
        subprocess.run(["exiftool", "-ver"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        return False


def adjust_time(image_path, time_offset, timezone_offset):
    cmd = ["exiftool", "-DateTimeOriginal", "-SubSecTimeOriginal", "-OffsetTimeOriginal", "-T", image_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        print(f"Error reading EXIF data for {image_path}: {result.stderr}")
        return

    exif_data = result.stdout.strip().split()

    if len(exif_data) < 4:
        print(f"Unexpected EXIF data format for {image_path}: {result.stdout}")
        return

    date_original = exif_data[0]  # "YYYY:MM:DD"
    time_original = exif_data[1]  # "HH:MM:SS"
    subsec_time_original = exif_data[2]  # Subsecond part

    datetime_original_str = f"{date_original} {time_original}"
    dt_format = "%Y:%m:%d %H:%M:%S"
    dt_obj = datetime.strptime(datetime_original_str, dt_format)

    adjusted_time = dt_obj + timedelta(hours=time_offset)

    new_timezone = f"{timezone_offset:+03d}:00"

    new_datetime_str = adjusted_time.strftime(f"%Y:%m:%d %H:%M:%S")
    new_subsec_datetime_str = f"{new_datetime_str}.{subsec_time_original}{new_timezone}"

    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-SubSecDateTimeOriginal={new_subsec_datetime_str}",
        image_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        print(f"Error writing EXIF data for {image_path}: {result.stderr}")
    else:
        print(f"Successfully updated EXIF data for {image_path}")


if __name__ == "__main__":
    if not check_exiftool_installed():
        print("ExifTool is not installed. Please install it before running this script.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Adjust image EXIF timestamps.")
    parser.add_argument('path', help='Path to the folder containing images.')
    parser.add_argument('-tz', '--timezone', help='New Timezone in hours (e.g., -3 for UTC-3).', type=int, required=True)
    parser.add_argument('-t', '--time', help='Time offset in hours (e.g., 1 for +1 hour).', type=int, required=True)
    args = parser.parse_args()

    for filename in os.listdir(args.path):
        if filename.lower().endswith(('.jpg', '.jpeg', '.heic', '.raw', '.arw', '.raf', '.nef', '.orf', '.rw2', '.cr2', '.cr3')):
            file_path = os.path.join(args.path, filename)
            adjust_time(file_path, args.time, args.timezone)