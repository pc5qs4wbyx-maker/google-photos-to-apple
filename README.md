# Google Photos to Apple Photos

Transfer your Google Photos library to Apple Photos with full metadata (dates, GPS locations, descriptions, favourites).

Google Takeout exports your photos with the metadata stripped out and dumped into separate JSON sidecar files. This tool reads those JSON files, writes the metadata back into each photo and video using exiftool, and produces a clean folder ready for import into Apple Photos.

## Why this exists

If you use Apple's iCloud Advanced Data Protection (ADP), the official Apple transfer service from Google will not work. Apple requires ADP to be disabled, and for users in the UK this is a permanent change since Apple removed the ability to re-enable ADP in February 2025. This tool provides an alternative route that keeps ADP intact.

It is also useful for anyone who has tried Google Takeout and ended up with thousands of photos showing the wrong date, or no location data, because Google does not embed metadata into the exported files.

## What it does

- Extracts Google Takeout zip files one at a time (no disk space explosion)
- Matches each photo/video to its companion JSON metadata file
- Handles all of Google's naming conventions (old format, supplemental-metadata format, truncated filenames, duplicate numbering, edited files)
- Writes date taken, GPS coordinates, descriptions, and favourites into EXIF data
- Supports date filtering so you only transfer photos before (or after) a cutoff date
- Picks up oversized videos that Google exports as standalone files outside the zips
- Produces a single folder you can drag into Apple Photos via File > Import

## Requirements

- macOS (or Linux/Windows with exiftool installed)
- Python 3.8+
- [exiftool](https://exiftool.org/) (install on Mac with `brew install exiftool`)

## Quick start

1. Request your data from [Google Takeout](https://takeout.google.com) (select only Google Photos, export as .zip, 2GB chunks)
2. Download all the zip files into a folder (e.g. `/Volumes/MySSD/zips/`)
3. Run the script:

```bash
python3 google_photos_to_apple.py /Volumes/MySSD/zips /Volumes/MySSD/output
```

With a date filter (only transfer photos taken before June 2022):

```bash
python3 google_photos_to_apple.py /Volumes/MySSD/zips /Volumes/MySSD/output --before 2022-06
```

4. Open Apple Photos, go to File > Import, select the output folder
5. After import, check the Duplicates album in Photos to merge any overlaps

## Options

| Flag | Description | Example |
|------|-------------|---------|
| `--before` | Only include media taken before this date | `--before 2022-06` |
| `--after` | Only include media taken after this date | `--after 2015-01` |
| `--skip-extract` | Skip zip extraction (scan source folder directly for media) | `--skip-extract` |

Date format is `YYYY-MM` or `YYYY-MM-DD`.

## How it handles disk space

The script processes one zip at a time: extract, find media, apply metadata, copy to output, delete extracted files, move to the next zip. This means you only need space for the zips plus the output folder, not for all the extracted content at once.

For a 350GB library with a date filter that keeps roughly half the photos, you need about 525GB total (350GB zips + 175GB output).

## What gets preserved

- Date and time taken
- GPS location (latitude, longitude, altitude)
- Descriptions added in Google Photos
- Favourites (transferred as a 5-star rating)
- Original file quality

## What does not transfer

- Albums (Google exports album folders, but Apple Photos import puts everything into one library -- you can recreate albums manually or use Smart Albums)
- Comments from shared albums
- Google Photos edit history (only the final edited version is exported)

## Detailed guide

See [INSTRUCTIONS.md](INSTRUCTIONS.md) for a full step-by-step walkthrough including external SSD setup, Google Takeout download tips, and troubleshooting.

## License

MIT
