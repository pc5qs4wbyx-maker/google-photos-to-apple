#!/usr/bin/env python3
"""
Google Photos Takeout to Apple Photos Transfer Tool
====================================================
Extracts Google Takeout zip files, merges JSON metadata (dates, GPS, descriptions)
back into photo/video EXIF data using exiftool, and produces a clean folder ready
for import into Apple Photos.

Processes one zip at a time to avoid disk space exhaustion -- only one zip is ever
extracted at once, and its extracted contents are deleted before moving to the next.

Requirements:
    - Python 3.8+
    - exiftool (install via: brew install exiftool)

Usage:
    python3 google_photos_to_apple.py /path/to/takeout/zips /path/to/output
    python3 google_photos_to_apple.py /path/to/zips /path/to/output --before 2022-06

Author: pc5qs4wbyx-maker
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif",
    ".tiff", ".tif", ".bmp", ".webp", ".raw", ".cr2",
    ".nef", ".arw", ".dng", ".orf", ".rw2",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv",
}

MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS

# Files to skip entirely
SKIP_FILES = {"metadata.json", "print-subscriptions.json", "shared_album_comments.json", "user-generated-memory-titles.json"}

# Tunable constants
TRUNCATED_FILENAME_PREFIX_LEN = 40
PROGRESS_LOG_INTERVAL = 500
EXIFTOOL_TIMEOUT_SECONDS = 30
EXIFTOOL_ERROR_LOG_LIMIT = 20

# Regex for extracting dates from common photo/video filenames
_FILENAME_DATE_RE = re.compile(
    r"(?:IMG|PXL|VID|MVIMG|Screenshot|PANO|BURST)?[_-]?"
    r"(\d{4})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])"
    r"[_-]?(\d{2})(\d{2})(\d{2})"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


def _safe_rmtree(path: Path) -> None:
    """Remove a directory tree, logging a warning on failure instead of silently ignoring."""
    try:
        shutil.rmtree(path)
    except OSError as e:
        log(f"Warning: could not fully clean up {path}: {e}", "WARN")
        log("You may need to manually delete this directory to free disk space.", "WARN")


def extract_date_from_filename(filename: str) -> Optional[datetime]:
    """
    Try to extract a date/time from common photo/video filename patterns.
    Returns a timezone-aware (UTC) datetime or None.
    """
    m = _FILENAME_DATE_RE.search(filename)
    if m:
        try:
            year, month, day, hour, minute, second = (int(g) for g in m.groups())
            return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def find_json_for_media(media_path: Path) -> Optional[Path]:
    """
    Find the companion JSON metadata file for a given media file.
    Google uses several naming conventions:
      1. photo.jpg.json                              (old format)
      2. photo.jpg.supplemental-metadata.json         (new format, late 2024+)
      3. Truncated variants of the above               (46-char limit)
      4. photo.json                                    (extension stripped)
      5. photo(1).jpg.json                             (duplicate numbering)
    """
    parent = media_path.parent
    name = media_path.name

    # Strategy 1: Direct match -- name.json
    candidates: List[Path] = [
        parent / f"{name}.json",
        parent / f"{name}.supplemental-metadata.json",
    ]

    # Strategy 2: Extension stripped -- stem.json
    candidates.append(parent / f"{media_path.stem}.json")

    # Strategy 3: Handle Google's duplicate numbering.
    # Google sometimes names files like: photo(1).jpg with JSON as photo.jpg(1).json
    # or photo(1).jpg.json
    dup_match = re.match(r"^(.+?)(\(\d+\))(\.\w+)$", name)
    if dup_match:
        base, num, ext = dup_match.groups()
        candidates.append(parent / f"{base}{ext}{num}.json")
        candidates.append(parent / f"{base}{ext}{num}.supplemental-metadata.json")
        candidates.append(parent / f"{base}{num}{ext}.json")

    # Strategy 4: Handle edited files (Google appends -edited)
    if "-edited" in media_path.stem:
        original_stem = media_path.stem.replace("-edited", "")
        original_name = original_stem + media_path.suffix
        candidates.append(parent / f"{original_name}.json")
        candidates.append(parent / f"{original_name}.supplemental-metadata.json")
        candidates.append(parent / f"{original_stem}.json")

    # Strategy 5: Fuzzy match for truncated filenames.
    # If the media filename is long, Google may have truncated the JSON name.
    # Look for JSON files that start with a substantial prefix of our filename.
    if len(name) > TRUNCATED_FILENAME_PREFIX_LEN:
        prefix = name[:TRUNCATED_FILENAME_PREFIX_LEN]
        try:
            for f in parent.iterdir():
                if f.suffix == ".json" and f.name.startswith(prefix) and f.name not in SKIP_FILES:
                    candidates.append(f)
        except FileNotFoundError:
            pass
        except OSError as e:
            log(f"Warning: could not scan directory for truncated JSON matches: {e}", "WARN")

    # Strategy 6: Scan for any JSON whose "title" field matches our filename
    # (expensive, so only used as last resort below)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # Last resort: scan JSONs in the same directory for a title match
    try:
        for f in parent.iterdir():
            if f.suffix == ".json" and f.name not in SKIP_FILES:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if data.get("title") == name:
                        return f
                except (json.JSONDecodeError, UnicodeDecodeError, FileNotFoundError):
                    continue
    except FileNotFoundError:
        pass
    except OSError as e:
        log(f"Warning: could not scan directory for JSON title matches: {e}", "WARN")

    return None


def parse_json_metadata(json_path: Path) -> Dict[str, Any]:
    """
    Extract useful metadata from a Google Takeout JSON sidecar file.
    Returns a dict with keys ready for exiftool arguments.
    """
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}

    metadata: Dict[str, Any] = {}

    # Date/time taken
    photo_taken = data.get("photoTakenTime", {})
    timestamp = photo_taken.get("timestamp")
    if timestamp:
        try:
            ts = int(timestamp)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            metadata["date_taken"] = dt.strftime("%Y:%m:%d %H:%M:%S")
            metadata["date_taken_utc"] = dt
        except (ValueError, OSError):
            pass

    # GPS coordinates -- use None-check (not == 0) to detect missing data,
    # since 0.0 is valid for equator/prime meridian coordinates.
    geo = data.get("geoData", {})
    lat = geo.get("latitude")
    lon = geo.get("longitude")
    # Fall back to geoDataExif only if geoData has no coordinates at all
    if lat is None and lon is None:
        geo = data.get("geoDataExif", {})
        lat = geo.get("latitude")
        lon = geo.get("longitude")

    # Reject Null Island (0, 0) -- Google uses this as a placeholder for "no GPS"
    if lat is not None and lon is not None and not (lat == 0.0 and lon == 0.0):
        metadata["latitude"] = lat
        metadata["longitude"] = lon
        alt = geo.get("altitude", 0)
        if alt and alt != 0:
            metadata["altitude"] = alt

    # Description
    desc = data.get("description", "")
    if desc and desc.strip():
        metadata["description"] = desc.strip()

    # Favorited
    if data.get("favorited"):
        metadata["favorited"] = True

    return metadata


def build_exiftool_args(metadata: Dict[str, Any], media_path: Path) -> List[str]:
    """
    Build exiftool command-line arguments to write metadata into a media file.
    Uses different tags for photos vs videos.
    """
    args = ["exiftool", "-overwrite_original", "-ignoreMinorErrors"]

    is_video = media_path.suffix.lower() in VIDEO_EXTENSIONS

    if "date_taken" in metadata:
        dt = metadata["date_taken"]
        if is_video:
            # QuickTime CreateDate/ModifyDate are UTC by spec (no offset needed).
            # Keys:CreationDate supports timezone, so append +00:00.
            args.extend([
                f"-QuickTime:CreateDate={dt}",
                f"-QuickTime:ModifyDate={dt}",
                f"-Keys:CreationDate={dt}+00:00",
            ])
        else:
            args.extend([
                f"-DateTimeOriginal={dt}",
                f"-CreateDate={dt}",
                f"-ModifyDate={dt}",
                "-OffsetTimeOriginal=+00:00",
                "-OffsetTime=+00:00",
            ])
        # Also set file modification date
        args.append(f"-FileModifyDate={dt}")

    if "latitude" in metadata and "longitude" in metadata:
        lat = metadata["latitude"]
        lon = metadata["longitude"]
        lat_ref = "N" if lat >= 0 else "S"
        lon_ref = "E" if lon >= 0 else "W"
        args.extend([
            f"-GPSLatitude={abs(lat)}",
            f"-GPSLatitudeRef={lat_ref}",
            f"-GPSLongitude={abs(lon)}",
            f"-GPSLongitudeRef={lon_ref}",
        ])
        if "altitude" in metadata:
            alt = metadata["altitude"]
            alt_ref = "Below Sea Level" if alt < 0 else "Above Sea Level"
            args.extend([
                f"-GPSAltitude={abs(alt)}",
                f"-GPSAltitudeRef={alt_ref}",
            ])

    if "description" in metadata:
        desc = metadata["description"]
        if is_video:
            args.append(f"-Description={desc}")
        else:
            args.extend([
                f"-ImageDescription={desc}",
                f"-XPComment={desc}",
            ])

    if "favorited" in metadata:
        args.append("-Rating=5")

    args.append(str(media_path))
    return args


def find_all_media(search_dir: Path) -> List[Path]:
    """Walk a directory tree and find all media files."""
    media_files: List[Path] = []
    for root, dirs, files in os.walk(search_dir):
        for f in files:
            fp = Path(root) / f
            if fp.suffix.lower() in MEDIA_EXTENSIONS and fp.name not in SKIP_FILES:
                media_files.append(fp)
    return media_files


def check_exiftool() -> bool:
    """Verify exiftool is installed."""
    try:
        result = subprocess.run(
            ["exiftool", "-ver"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            log(f"exiftool version {result.stdout.strip()} found.")
            return True
    except FileNotFoundError:
        pass

    log("exiftool is not installed.", "ERROR")
    log("Install it with: brew install exiftool", "ERROR")
    return False


def process_media_files(
    media_files: List[Path],
    output_dir: Path,
    before_date: Optional[datetime],
    after_date: Optional[datetime],
    stats: Dict[str, int],
    used_names: Set[str],
    failed_metadata_files: List[str],
) -> None:
    """
    Process a batch of media files:
      1. Find companion JSON for each
      2. Parse metadata
      3. Apply date filters
      4. Copy to output dir
      5. Write metadata with exiftool

    Mutates stats, used_names, and failed_metadata_files in place.
    """
    for i, media_path in enumerate(media_files, 1):
        file_num = stats["scanned"] + 1
        stats["scanned"] += 1

        if file_num % PROGRESS_LOG_INTERVAL == 0 or file_num == 1:
            log(f"Scanning file {file_num} (total processed so far: {stats['processed']}, skipped by date: {stats.get('skipped_by_date', 0)})...")

        # Find and parse JSON metadata
        json_path = find_json_for_media(media_path)
        metadata: Dict[str, Any] = {}
        if json_path:
            metadata = parse_json_metadata(json_path)

        # Filename-based date fallback when JSON has no date
        if "date_taken" not in metadata:
            fallback_dt = extract_date_from_filename(media_path.name)
            if fallback_dt:
                metadata["date_taken"] = fallback_dt.strftime("%Y:%m:%d %H:%M:%S")
                metadata["date_taken_utc"] = fallback_dt
                metadata["date_from_filename"] = True
                stats["date_from_filename"] += 1

        # Apply date filters
        if before_date or after_date:
            photo_date = metadata.get("date_taken_utc")
            if photo_date:
                if before_date and photo_date >= before_date:
                    stats["skipped_by_date"] += 1
                    continue
                if after_date and photo_date < after_date:
                    stats["skipped_by_date"] += 1
                    continue
            else:
                # No date in metadata -- include it anyway (better safe than sorry)
                stats["no_date_included_anyway"] += 1

        # Determine a unique output filename
        out_name = media_path.name
        if out_name.lower() in used_names:
            stem = media_path.stem
            ext = media_path.suffix
            counter = 1
            while f"{stem}_{counter}{ext}".lower() in used_names:
                counter += 1
            out_name = f"{stem}_{counter}{ext}"
        used_names.add(out_name.lower())

        out_path = output_dir / out_name

        # Copy the media file to the output directory
        try:
            shutil.copy2(media_path, out_path)
        except Exception as e:
            log(f"  Could not copy {media_path.name}: {e}", "WARN")
            stats["copy_errors"] += 1
            continue

        # Write metadata with exiftool
        if json_path and metadata:
            args = build_exiftool_args(metadata, out_path)
            if len(args) > 3:  # More than just exiftool + flags + filepath
                try:
                    result = subprocess.run(
                        args,
                        capture_output=True, text=True, timeout=EXIFTOOL_TIMEOUT_SECONDS
                    )
                    if result.returncode == 0:
                        stats["metadata_written"] += 1
                    else:
                        stats["exiftool_errors"] += 1
                        failed_metadata_files.append(str(out_path))
                        if stats["exiftool_errors"] <= EXIFTOOL_ERROR_LOG_LIMIT:
                            log(f"  exiftool warning for {out_name}: {result.stderr.strip()}", "WARN")
                        elif stats["exiftool_errors"] == EXIFTOOL_ERROR_LOG_LIMIT + 1:
                            log("  (further exiftool warnings suppressed; count will appear in summary)", "WARN")
                except subprocess.TimeoutExpired:
                    stats["exiftool_errors"] += 1
                    failed_metadata_files.append(str(out_path))
            else:
                stats["no_useful_metadata"] += 1
        elif json_path and not metadata:
            stats["json_parse_failed"] += 1
        else:
            stats["no_json_found"] += 1
            stats["kept_original_exif"] += 1

        stats["processed"] += 1


def print_summary(stats: Dict[str, int], output_dir: Path) -> None:
    """Print a summary of what was processed."""
    print("\n" + "=" * 60)
    print("  TRANSFER COMPLETE")
    print("=" * 60)
    print(f"  Total media files scanned:  {stats['scanned']}")
    if stats.get('skipped_by_date', 0) > 0:
        print(f"  Skipped (outside date range):{stats['skipped_by_date']}")
    if stats.get('no_date_included_anyway', 0) > 0:
        print(f"  No date in metadata (kept):  {stats['no_date_included_anyway']}")
    if stats.get('date_from_filename', 0) > 0:
        print(f"  Date recovered from filename:{stats['date_from_filename']}")
    print(f"  Successfully processed:     {stats['processed']}")
    print(f"  Metadata restored:          {stats['metadata_written']}")
    print(f"  No JSON found (kept EXIF):  {stats.get('no_json_found', 0)}")
    print(f"  JSON parse issues:          {stats.get('json_parse_failed', 0)}")
    print(f"  Exiftool warnings:          {stats.get('exiftool_errors', 0)}")
    print(f"  Copy errors:                {stats.get('copy_errors', 0)}")
    print("=" * 60)
    print(f"\n  Your photos are ready in:\n  {output_dir}\n")
    print("  NEXT STEP:")
    print("  1. Open Apple Photos on your Mac")
    print("  2. Go to File > Import...")
    print(f"  3. Select the folder: {output_dir}")
    print("  4. Click 'Import All New Items'")
    print("  5. Wait for iCloud to sync across your devices")
    print()


def _check_directory_overlap(source_dir: Path, output_dir: Path) -> None:
    """Exit with an error if source and output directories overlap."""
    if source_dir == output_dir:
        log("Output directory must not be the same as source directory.", "ERROR")
        sys.exit(1)
    try:
        output_dir.relative_to(source_dir)
        log("Output directory must not be inside the source directory.", "ERROR")
        sys.exit(1)
    except ValueError:
        pass
    try:
        source_dir.relative_to(output_dir)
        log("Source directory must not be inside the output directory.", "ERROR")
        sys.exit(1)
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transfer Google Photos Takeout to Apple Photos with full metadata.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 google_photos_to_apple.py ~/Downloads/takeout-zips ~/Pictures/GooglePhotosImport
  python3 google_photos_to_apple.py /Volumes/MySSD/takeout /Volumes/MySSD/output --before 2022-06
  python3 google_photos_to_apple.py ./zips ./output --after 2015-01 --before 2022-06
  python3 google_photos_to_apple.py ./zips ./output --skip-extract
        """
    )
    parser.add_argument(
        "source",
        help="Folder containing your Google Takeout .zip files"
    )
    parser.add_argument(
        "output",
        help="Folder where processed photos will be saved (ready for Apple Photos import)"
    )
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Skip zip extraction (use if you already extracted them manually). "
             "When set, the source directory itself is scanned for media files."
    )
    parser.add_argument(
        "--before",
        help="Only include photos taken BEFORE this date (format: YYYY-MM or YYYY-MM-DD). "
             "Example: --before 2022-06 to only get photos from before June 2022.",
        default=None
    )
    parser.add_argument(
        "--after",
        help="Only include photos taken AFTER this date (format: YYYY-MM or YYYY-MM-DD). "
             "Example: --after 2018-01 to skip anything before January 2018.",
        default=None
    )

    args = parser.parse_args()

    source_dir = Path(args.source).resolve()
    output_dir = Path(args.output).resolve()

    if not source_dir.exists():
        log(f"Source directory does not exist: {source_dir}", "ERROR")
        sys.exit(1)

    _check_directory_overlap(source_dir, output_dir)

    # Parse date filters
    def parse_date_arg(date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        for fmt in ("%Y-%m-%d", "%Y-%m"):
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        log(f"Could not parse date '{date_str}'. Use YYYY-MM or YYYY-MM-DD format.", "ERROR")
        sys.exit(1)

    before_date = parse_date_arg(args.before)
    after_date = parse_date_arg(args.after)

    # Check exiftool is available
    if not check_exiftool():
        sys.exit(1)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Shared state across all zip batches
    stats: Dict[str, int] = defaultdict(int)
    used_names: Set[str] = set()
    failed_metadata_files: List[str] = []

    # Seed used_names from existing files in output directory to avoid
    # overwriting results from a previous partial run
    for existing in output_dir.iterdir():
        if existing.is_file():
            used_names.add(existing.name.lower())
    if used_names:
        log(f"Output directory already contains {len(used_names)} file(s); new files will avoid name collisions.")

    if before_date:
        log(f"Date filter active: only media taken BEFORE {before_date.strftime('%B %Y')}")
    if after_date:
        log(f"Date filter active: only media taken AFTER {after_date.strftime('%B %Y')}")

    if args.skip_extract:
        # --skip-extract mode: scan source directory directly for media
        log("Skipping extraction (--skip-extract flag). Scanning source directory for media...")
        media_files = find_all_media(source_dir)
        log(f"Found {len(media_files)} media files.")
        if not media_files:
            log("No media files found.", "ERROR")
            sys.exit(1)
        log("Processing media and restoring metadata...")
        process_media_files(media_files, output_dir, before_date, after_date, stats, used_names, failed_metadata_files)
    else:
        # Incremental mode: process one zip at a time
        zip_files = sorted(source_dir.glob("*.zip"))
        if not zip_files:
            log("No zip files found in source directory.", "ERROR")
            sys.exit(1)

        log(f"Found {len(zip_files)} zip file(s). Processing one at a time to save disk space.")

        temp_extract_dir = source_dir / "_extracting"

        for zip_idx, zf in enumerate(zip_files, 1):
            log(f"")
            log(f"--- ZIP {zip_idx}/{len(zip_files)}: {zf.name} ---")

            # Clean up any leftover temp directory from a previous interrupted run
            if temp_extract_dir.exists():
                log(f"  Cleaning up leftover temp directory...")
                _safe_rmtree(temp_extract_dir)

            # Extract this single zip
            temp_extract_dir.mkdir(parents=True, exist_ok=True)
            log(f"  Extracting...")
            try:
                with zipfile.ZipFile(zf, 'r') as z:
                    z.extractall(temp_extract_dir)
            except (zipfile.BadZipFile, Exception) as e:
                log(f"  Warning: Could not extract {zf.name}: {e}", "WARN")
                _safe_rmtree(temp_extract_dir)
                continue

            # Find media files in the extracted content
            media_files = find_all_media(temp_extract_dir)
            log(f"  Found {len(media_files)} media files in this zip.")

            if media_files:
                # Process them (copy to output + fix metadata)
                process_media_files(media_files, output_dir, before_date, after_date, stats, used_names, failed_metadata_files)

            # Delete the extracted content to free disk space
            log(f"  Cleaning up extracted files...")
            _safe_rmtree(temp_extract_dir)

            log(f"  Done with {zf.name}. Running totals: {stats['processed']} processed, {stats.get('skipped_by_date', 0)} skipped by date.")

        # Handle loose media files in the source directory
        # (Google exports oversized videos as standalone files, not in zips)
        loose_media = []
        for f in source_dir.iterdir():
            if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS and f.name not in SKIP_FILES:
                loose_media.append(f)

        if loose_media:
            log(f"")
            log(f"Found {len(loose_media)} loose media file(s) in source directory (oversized videos).")
            process_media_files(loose_media, output_dir, before_date, after_date, stats, used_names, failed_metadata_files)

    # Write metadata failure log if any
    if failed_metadata_files:
        failures_path = output_dir / "_metadata_failures.txt"
        failures_path.write_text("\n".join(failed_metadata_files) + "\n", encoding="utf-8")
        log(f"Wrote {len(failed_metadata_files)} metadata failure path(s) to {failures_path}")

    # Final summary
    print_summary(stats, output_dir)


if __name__ == "__main__":
    main()
