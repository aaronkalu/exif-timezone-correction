import os
import subprocess
from datetime import datetime, timedelta
import argparse
import sys
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import tqdm

def check_exiftool_installed():
    try:
        subprocess.run(["exiftool", "-ver"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def validate_timezone_format(timezone_str):
    return bool(re.match(r'^[+-](\d{2}):(\d{2})$', timezone_str))


def get_offset_in_hours_and_minutes(offset_str):
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
        return f"Error reading EXIF data for {image_path}: {result.stderr}"

    exif_data = result.stdout.strip().split()
    if len(exif_data) < 3:
        return f"Unexpected EXIF data format for {image_path}: {result.stdout}"

    date_original, time_original, subsec_time_original = exif_data[:3]
    current_offset_str = exif_data[3] if len(exif_data) > 3 else None

    datetime_original_str = f"{date_original} {time_original}"
    dt_obj = datetime.strptime(datetime_original_str, "%Y:%m:%d %H:%M:%S")
    current_offset_hours, current_offset_minutes = get_offset_in_hours_and_minutes(current_offset_str)

    if (current_offset_hours == new_timezone_offset_hours and
            current_offset_minutes == new_timezone_offset_minutes):
        return f"Skipping {image_path}: Timezone already set."

    time_difference_hours = new_timezone_offset_hours - current_offset_hours
    time_difference_minutes = new_timezone_offset_minutes - current_offset_minutes
    adjusted_time = dt_obj + timedelta(hours=time_difference_hours, minutes=time_difference_minutes)

    new_offset_str = f"{new_timezone_offset_hours:+03d}:{new_timezone_offset_minutes:02d}"
    new_datetime_str = adjusted_time.strftime("%Y:%m:%d %H:%M:%S")
    new_subsec_datetime_str = f"{new_datetime_str}.{subsec_time_original}{new_offset_str}"

    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-SubSecDateTimeOriginal={new_subsec_datetime_str}",
        f"-OffsetTimeOriginal={new_offset_str}",
        image_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    return f"Updated {image_path}"


def process_images(image_paths, timezone_offset_hours, timezone_offset_minutes, max_workers=1):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(adjust_time, img, timezone_offset_hours, timezone_offset_minutes): img for img in
                   image_paths}

        with tqdm.tqdm(total=len(image_paths), desc="Processing Images", unit="img") as pbar:
            for future in as_completed(futures):
                result = future.result()
                print(result)
                pbar.update(1)


if __name__ == "__main__":
    if not check_exiftool_installed():
        print("ExifTool is not installed. Please install it before running this script.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Adjust image EXIF timestamps based on a new timezone.")
    parser.add_argument('path', help='Path to the folder containing images.')
    parser.add_argument('timezone', help='New timezone in format "HH:MM" (e.g., 02:00 for UTC+2).')
    parser.add_argument('--negative', action='store_true',
                        help='Specify if the new timezone is negative (default is positive).')
    parser.add_argument('--recursive', action='store_true', help='Process images in subdirectories recursively.')
    parser.add_argument('--workers', type=int, default=1, help='Number of threads to run in parallel.')

    args = parser.parse_args()

    timezone_offset_hours, timezone_offset_minutes = get_offset_in_hours_and_minutes(
        f"{'-' if args.negative else '+'}{args.timezone}")

    if not validate_timezone_format(f"{'+' if not args.negative else '-'}{args.timezone}"):
        print(f"Invalid timezone format: {args.timezone}. Please use '+/-HH:MM'.")
        sys.exit(1)

    image_extensions = (
    '.jpg', '.jpeg', '.tiff', '.heic', '.raw', '.arw', '.raf', '.nef', '.orf', '.rw2', '.cr2', '.cr3')

    image_paths = []
    for root, _, files in os.walk(args.path) if args.recursive else [(args.path, [], os.listdir(args.path))]:
        for filename in files:
            if filename.lower().endswith(image_extensions):
                image_paths.append(os.path.join(root, filename))

    if image_paths:
        process_images(image_paths, timezone_offset_hours, timezone_offset_minutes, args.workers)
        print(f"Successfully adjusted EXIF timestamps for {len(image_paths)} images.")
    else:
        print("No images found.")
