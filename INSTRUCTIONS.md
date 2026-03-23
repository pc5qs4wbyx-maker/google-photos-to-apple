# Google Photos to Apple Photos Transfer Guide

## What this does

This script takes your Google Takeout zip files, extracts them, fixes the metadata
(dates, locations, descriptions) that Google strips out, and produces a clean folder
you can drag straight into Apple Photos.

It also supports date filtering so you only transfer the photos you actually need,
avoiding duplicates with photos already in your Apple Photos library.

## Before you start

You need two things installed on your Mac:

### 1. Python 3 (almost certainly already installed)
Open Terminal and type:
```
python3 --version
```
If you see a version number, you're good.

### 2. exiftool (the metadata fixer)
Install via Homebrew. If you don't have Homebrew, install it first:
```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
Then install exiftool:
```
brew install exiftool
```

## Step-by-step

### Step 0: Find your cutoff date

Before downloading anything, figure out when you started using Apple Photos
alongside Google Photos. Open Apple Photos on your Mac, go to Library, and scroll
to the very top to see your earliest photo. Note the approximate month and year.

Everything before that date is "Google only" and needs transferring. Everything
after it is probably already in Apple Photos.

### Step 1: Prepare storage

You need enough disk space for the zip files plus the processed output. The script
processes one zip at a time, so you do NOT need space for all the extracted content
at once. A rough guide: if your zips total 350GB and your cutoff date filters out
half the photos, you need about 350GB (zips) + 175GB (output) = 525GB.

An external SSD works well. Create two folders:
```
mkdir -p /Volumes/YOUR_SSD_NAME/GoogleTakeout/zips
mkdir -p /Volumes/YOUR_SSD_NAME/GoogleTakeout/output
```

Replace YOUR_SSD_NAME with the actual name of your drive. You can find it by
running `ls /Volumes/` in Terminal.

Also copy the script there for convenience:
```
cp google_photos_to_apple.py /Volumes/YOUR_SSD_NAME/GoogleTakeout/
```

### Step 2: Request your Google Takeout

1. Go to https://takeout.google.com
2. Click "Deselect all" at the top
3. Scroll down and tick only "Google Photos"
4. Click "Next step"
5. Under "Delivery method", choose "Send download link via email"
6. Under "Frequency", choose "Export once"
7. Under "File type & size", choose ".zip" and "2 GB" (smaller chunks are more reliable)
8. Click "Create export"
9. Wait for Google to email you (can take hours to a couple of days for 350GB)
10. Download ALL the zip files into the zips folder on your SSD:
    /Volumes/YOUR_SSD_NAME/GoogleTakeout/zips/

### Step 3: Run the script

Open Terminal and run:
```
python3 /Volumes/YOUR_SSD_NAME/GoogleTakeout/google_photos_to_apple.py \
  /Volumes/YOUR_SSD_NAME/GoogleTakeout/zips \
  /Volumes/YOUR_SSD_NAME/GoogleTakeout/output \
  --before 2022-06
```

Replace `2022-06` with your actual cutoff date (the month you started using
Apple Photos). The format is YYYY-MM or YYYY-MM-DD.

You can also use --after if you want to skip very old photos:
```
  --after 2010-01 --before 2022-06
```

The script will:
- Process each zip file one at a time (extract, process, clean up, move on)
- Find every photo and video inside each zip
- Check each file's date against your cutoff
- Skip anything taken after your cutoff (already in Apple Photos)
- Fix metadata on the remaining files using exiftool
- Save them into the output folder
- Also pick up any loose video files too large for Google's zip format

### Step 4: Import into Apple Photos

1. Open the Photos app on your Mac
2. Go to File > Import...
3. Navigate to the output folder on your SSD
4. Click "Review for Import" or "Import All New Items"
5. Wait for the import to complete
6. iCloud Photos will then sync everything to your other Apple devices

### Step 5: Clean up

Once you're happy that everything imported correctly into Apple Photos:
- Delete the zips folder on your SSD
- Delete the output folder on your SSD

Also worth noting: since macOS Ventura, Apple Photos has a built-in "Duplicates"
album that automatically detects duplicate photos. After the import, check
Photos > Albums > Duplicates and merge any it finds.

## Tips

- Disk space: the script processes one zip at a time, so you only need space for
  the zips themselves plus the final output. With a date filter, the output will be
  smaller than the total zip size since filtered-out files are skipped, not copied.

- If a download from Google fails partway through, just re-download that specific
  zip file. You don't need to restart the whole export.

- If the script gets interrupted, you can re-run it. It will re-process everything
  into the output folder. Files already there will be overwritten (not duplicated).

- The --skip-extract flag is useful if you already unzipped manually:
  ```
  python3 google_photos_to_apple.py /path/to/zips /path/to/output --before 2022-06 --skip-extract
  ```

## What gets preserved

- Date and time the photo was taken
- GPS location (latitude, longitude, altitude)
- Descriptions you added in Google Photos
- Favourites (transferred as a 5-star rating)
- Original file quality

## What does NOT transfer

- Albums (Google Takeout exports album folders, but Apple Photos import puts
  everything into one library. You can recreate albums manually or use Smart Albums.)
- Comments from shared albums
- Google Photos edits (only the edited version is exported, not the edit history)
