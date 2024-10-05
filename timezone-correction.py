import os
import subprocess
from datetime import datetime, timedelta
import argparse
import sys
import re


def check_exiftool_installed():
    try:
        subprocess.run(["exiftool", "-ver"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        return False


def validate_timezone_format(timezone_str):
    # Ensure the timezone string follows the format "+/-HH:MM"
    return bool(re.match(r'^[+-](\d{2}):(\d{2})$', timezone_str))


def get_offset_in_hours_and_minutes(offset_str):
    # Parse the offset string, e.g., "+05:30" or "-03:45" and convert to hours and minutes
    match = re.match(r'([+-]?)(\d{2}):(\d{2})', offset_str)
    if match:
        sign = -1 if match.group(1) == '-' else 1
        hours = int(match.group(2))
        minutes = int(match.group(3))
        return sign * hours, sign * minutes
    return 0, 0


def adjust_time(image_path, new_timezone_offset_hours, new_timezone_offset_minutes):
    cmd = ["exiftool", "-DateTimeOriginal", "-SubSecTimeOriginal", "-OffsetTimeOriginal", "-T", image_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        print(f"Error reading EXIF data for {image_path}: {result.stderr}")
        return

    exif_data = result.stdout.strip().split()

    if len(exif_data) < 3:
        print(f"Unexpected EXIF data format for {image_path}: {result.stdout}")
        return

    date_original = exif_data[0]  # "YYYY:MM:DD"
    time_original = exif_data[1]  # "HH:MM:SS"
    subsec_time_original = exif_data[2]  # Subsecond part
    current_offset_str = exif_data[3] if len(exif_data) > 3 else None  # Current offset (e.g., "+10:00")

    datetime_original_str = f"{date_original} {time_original}"
    dt_format = "%Y:%m:%d %H:%M:%S"
    dt_obj = datetime.strptime(datetime_original_str, dt_format)

    current_offset_hours, current_offset_minutes = get_offset_in_hours_and_minutes(current_offset_str)

    if (current_offset_hours == new_timezone_offset_hours and
            current_offset_minutes == new_timezone_offset_minutes):
        print(f"Skipping {image_path}: Timezone is already set to UTC{new_timezone_offset_hours:+03d}:{new_timezone_offset_minutes:02d}")
        return

    time_difference_hours = new_timezone_offset_hours - current_offset_hours
    time_difference_minutes = new_timezone_offset_minutes - current_offset_minutes

    adjusted_time = dt_obj + timedelta(hours=time_difference_hours, minutes=time_difference_minutes)

    new_offset_str = f"{new_timezone_offset_hours:+03d}:{new_timezone_offset_minutes:02d}"

    new_datetime_str = adjusted_time.strftime(f"%Y:%m:%d %H:%M:%S")
    new_subsec_datetime_str = f"{new_datetime_str}.{subsec_time_original}{new_offset_str}"

    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-SubSecDateTimeOriginal={new_subsec_datetime_str}",
        f"-OffsetTimeOriginal={new_offset_str}",
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

    parser = argparse.ArgumentParser(description="Adjust image EXIF timestamps based on a new timezone.")
    parser.add_argument('path', help='Path to the folder containing images.')
    parser.add_argument('timezone', help='New timezone in format "HH:MM" (e.g., 02:00 for UTC+2).')
    parser.add_argument('--negative', action='store_true', help='Specify this flag if the new timezone is negative (default is positive).')
    args = parser.parse_args()

    if args.negative:
        timezone_offset_hours, timezone_offset_minutes = get_offset_in_hours_and_minutes(f"-{args.timezone}")
    else:
        timezone_offset_hours, timezone_offset_minutes = get_offset_in_hours_and_minutes(f"+{args.timezone}")

    if not validate_timezone_format(f"{'+' if not args.negative else '-'}{args.timezone}"):
        print(f"Invalid timezone format: {args.timezone}. Please provide a valid timezone in the format '+/-HH:MM'.")
        sys.exit(1)

    for filename in os.listdir(args.path):
        if filename.lower().endswith(('.jpg', '.jpeg', '.tiff', '.heic', '.raw', '.arw', '.raf', '.nef', '.orf', '.rw2', '.cr2', '.cr3')):
            file_path = os.path.join(args.path, filename)
            adjust_time(file_path, timezone_offset_hours, timezone_offset_minutes)

    print("Successfully adjusted EXIF timestamps for all images in the specified folder.")
