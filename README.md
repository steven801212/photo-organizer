# Photo Organizer

**English** | [繁體中文（台灣）](README.zh-TW.md)

A private photo-organizing service for Synology NAS. Drop files into your Inbox, preview the proposed destinations in your browser, and organize them into a dated library. Each user has a separate library, index, thumbnail cache, backups, and non-media quarantine.

**Current version: v1.0.1** · [Download](https://github.com/steven801212/photo-organizer/releases/latest) · [Upgrade guide](RELEASE-NOTES.en.md)

> Designed for a personal or household LAN, not direct exposure to the public Internet. Run one Organizer Service per set of folders. Keep an independent backup of important photos.

## What's new in v1.0.1?

- Photos, years, and dates are ordered newest first; unknown dates appear last.
- Year folders start collapsed and expand when needed.
- The interface uses the available window width, with responsive photo columns and less unused space.
- These changes do not move existing photos or require reindexing.

## Features

### Date-based organization

- Reads capture metadata with ExifTool rather than relying solely on filenames.
- Organizes media into **type → year → date** folders.
- Supports JPEG, PNG, TIFF, HEIC/HEIF, common camera RAW formats, MOV, and MP4.
- Sends files without a usable capture date to `_Unsorted`.
- Shows proposed destinations before you start an import.

Example library:

```text
PhotoLibrary/
└─ alice/
   ├─ JPG/
   │  └─ 2023/
   │     └─ 2023-02-04/
   ├─ RAW/
   │  └─ 2023/
   │     └─ 2023-02-04/
   ├─ VIDEO/
   ├─ LIVE/
   ├─ _Duplicates/
   ├─ _Unsorted/
   └─ _Trash/
```

### Live Photos, RAW sidecars, and HEIC

- Pairs Live Photo still images (`JPG/HEIC`) with `MOV` clips. Apple pairing identifiers take precedence; same-folder, same-stem matching is a fallback when identifiers do not conflict.
- Stores paired files together under `LIVE/year/date` and shows one gallery item.
- Lets you play the paired MOV from the enlarged preview, when the browser supports its codec.
- Keeps Live Photo pairs together for downloads, trash, and restore operations.
- Can reindex an existing library, preview legacy Live Photo matches, and organize them.
- Keeps matching `.xmp` sidecars with their RAW files.
- Includes HEIC/HEIF decoding in the Docker image.
- Produces browser-friendly sRGB JPEG thumbnails and 1600px previews, applying orientation and ICC color-profile handling.

### Duplicate protection and non-media files

- Uses full SHA-256 content checks for final duplicate decisions, with a size/sample fingerprint to narrow candidates.
- Preserves duplicates in `_Duplicates` instead of overwriting originals.
- Generates safe destination names for different files with the same name.
- Detects duplicates within the same import as well as against the existing index.
- Moves unrelated files such as PDF, EXE, ZIP, and documents into:

```text
PhotoOrganizer/rejected/<username>/<import-batch>/
```

Original subfolder structure is retained. Program files are never executed. Identical non-media duplicates are retained in the user's duplicate area.

After successful processing, empty Inbox subfolders are removed from the deepest level upward. The user's Inbox root is retained. Incomplete uploads, changed files, and failed items remain in the Inbox.

### Browser gallery and map

- Browse a collapsible year/date hierarchy, newest first.
- View thumbnails and load additional photos as needed.
- Search filenames, dates, camera make/model, lens, ISO, and focal length.
- Filter by JPG/HEIC, RAW, video, or Live Photo.
- Mark favorites and create private albums.
- Select photos and download a ZIP, subject to the 200-file request limit.
- Browse GPS photos on an interactive OpenStreetMap map.
- See thumbnail markers and grouped counts for photos at the same location.
- Open a marker's photos in the existing full preview and EXIF panel.
- Automatically fit the map to shooting locations, with a control to restore the overview.

Leaflet is bundled locally. Your browser retrieves map tiles from OpenStreetMap; original photos are not uploaded to the map provider.

### Enlarged previews and EXIF

- Use the left/right arrow keys for previous/next photos.
- Zoom with the mouse wheel; double-click to reset.
- Read EXIF beside the image without covering it.
- Inspect available capture time, camera, lens, focal length, aperture, shutter speed, ISO, exposure compensation, flash, dimensions, GPS, and altitude.

### Progress, automation, and notifications

- Shows the current stage, processed count, total count, percentage, and current filename.
- Import previews have **500 items per page**. This is a display limit, not an import limit: importing processes the complete scan plan.
- Supports stop requests at safe file-processing boundaries.
- Reloading the browser reconnects to the current server-side job status.
- Stores job status and the scan plan in the user's index directory; a restarted service reports an interrupted job rather than pretending it completed.
- Supports scheduled Inbox scans and optional automatic imports.
- Automatic importing is disabled until the user enables it.
- Supports a webhook URL for completion/failure notifications.

### Separate users, trash, and index backups

- Administrators can create, enable, disable, and reset passwords for users.
- Passwords must be nonempty; choose a strong password even on a private LAN.
- Each user has independent folders, a SQLite index, thumbnails, and backups.
- There is no shared family library.
- Removing a gallery item first moves it to `_Trash`.
- Items can be restored, and a retention period can be configured.
- Manual permanent deletion requires confirmation. Automatic permanent deletion is a separate option and is **off by default**.
- Indexes are backed up before and after imports; manual backups are also supported.
- Restoring an index first creates a safety backup and checks SQLite integrity.
- Reindexing scans an existing library and repairs indexed paths without reorganizing the photos.
- Health checks identify missing files and unindexed library files.

**Index backups are not photo backups.** Back up the actual library separately.

## Conservative file handling

1. Scanning and previewing do not move source files.
2. Existing destination files are not directly overwritten.
3. On the same filesystem, a non-overwriting hard link is preferred; the Inbox source is removed only after the index commit.
4. Across filesystems, files are copied, verified with SHA-256, and only then removed from the source.
5. Failed database writes trigger best-effort recovery while retaining source data.
6. The service favors an extra copy over deleting the only copy.
7. Imports record a manifest and operation history for recovery.
8. Synology/system metadata such as `@eaDir`, `@tmp`, `.DS_Store`, `Thumbs.db`, and incomplete temporary files is ignored.

These safeguards do not replace independent backups. RAID is not a backup.

## Quick start on Synology

### Requirements

- Synology DSM 7.2 with Container Manager and a Docker-capable NAS.
- The primary deployment target is x86-64, such as the DS1821+. Compatibility has not been verified on every NAS model.
- Internet access during image building to obtain dependencies.

The app's current interface is **Traditional Chinese (Taiwan)**. This repository provides English and Traditional Chinese documentation; an English app interface is not included in this release.

### 1. Create folders

The example configuration uses the shared folder `Organized Photo`:

```text
/volume1/Organized Photo/PhotoInbox
/volume1/Organized Photo/PhotoLibrary
/volume1/Organized Photo/PhotoOrganizer/app
/volume1/Organized Photo/PhotoOrganizer/data
/volume1/Organized Photo/PhotoOrganizer/backups
/volume1/Organized Photo/PhotoOrganizer/rejected
```

If your shared folder or volume differs, adjust the host side of the mount in `docker-compose.yml`.

### 2. Download and extract

Download **photo-organizer-v1.0.1-synology.zip** from [Releases](https://github.com/steven801212/photo-organizer/releases/latest) and extract its contents into `PhotoOrganizer/app`.

This is a source deployment bundle for Container Manager, not a Windows installer or a prebuilt Docker image. The deployment bundle retains the original release documentation; this repository provides the current bilingual guides.

### 3. Configure credentials and permissions

Copy `.env.example` to `.env` and set a nonempty administrator password:

```dotenv
PHOTO_ADMIN_PASSWORD=replace-with-your-own-strong-password
```

For an existing installation, the account database is retained; changing this variable does not reset an existing user's password.

Optionally set both `PUID` and `PGID` to the numeric IDs of your DSM account:

```dotenv
PUID=1026
PGID=100
```

These numbers are examples. Use `id <DSM-username>` to find yours, and grant that account read/write access to the shared folder. If omitted, the service retains its legacy root execution mode.

Never publish your real `.env`, passwords, indexes, or backups.

### 4. Build and start

In Container Manager, create a project using the supplied `docker-compose.yml`, then build and start it. The first build installs ExifTool and Python dependencies.

Open:

```text
http://NAS-IP:8080
```

Sign in as `admin` with the password you configured. Keep access on your LAN or VPN, and use HTTPS through a properly configured reverse proxy when appropriate. Do not expose port 8080 directly to the Internet.

## Daily use and data locations

1. Copy photos or an old folder tree into `PhotoInbox/<username>`.
2. Wait for the upload to finish.
3. Sign in and click **掃描 Inbox** (Scan Inbox).
4. Review new photos, duplicates, unknown dates, non-media files, and destinations.
5. Click **開始整理** (Start organizing).
6. Browse **照片圖庫** (Photo library), or check the folders in File Station.

Start with a small copied sample before importing a large legacy collection.

```text
PhotoInbox/alice/                         Incoming files
PhotoLibrary/alice/                       Organized media
PhotoOrganizer/data/users/alice/          Index and thumbnail cache
PhotoOrganizer/backups/alice/             Index backups
PhotoOrganizer/rejected/alice/            Non-media quarantine
PhotoOrganizer/data/system/accounts.db    Account database
```

Multiple PCs can upload to the same user's Inbox. The NAS service performs the actual scanning and organization. Run only one service against the same folders.

## Upgrading

See the [English upgrade guide](RELEASE-NOTES.en.md) or [繁體中文升級說明](RELEASE-NOTES.md).

Wait for active jobs to finish, back up the index, stop the project, replace application files, and rebuild the image/container. Preserve your existing `.env`, mount/port customizations, photos, indexes, backups, and quarantine.

A restart alone does not rebuild an image. Confirm the web interface displays `v1.0.1`. This UI update does not require reindexing.

## Using the library with Immich

Immich should read your photo folders as an external library; it does not need this application's SQLite database.

Example path:

```text
/volume1/Organized Photo/PhotoLibrary/<username>
```

Exclude `_Trash`, `_Duplicates`, and `_Unsorted` if you do not want them indexed. Keep `LIVE` included because paired still images and MOV files are stored together. Recognition of older Live Photos depends on the Immich version and the pairing metadata in those files.

## Supported formats

| Type | Extensions |
| --- | --- |
| Standard images | JPG, JPEG, PNG, TIFF, TIF, HEIC, HEIF |
| Camera RAW | ARW, RW2, CR2, CR3, NEF, NRW, ORF, RAF, DNG |
| Video | MOV, MP4 |
| RAW sidecar | XMP, paired with a matching RAW filename |

## Limitations and validation

- Desktop browser interface; no dedicated mobile interface or PWA.
- No shared family library.
- HEVC MOV playback depends on browser/OS codec support; original files remain intact.
- Initial scans of large Inboxes can create significant CPU and disk load.
- Final duplicate checks and cross-volume verification still use full SHA-256.
- Use a single service instance and keep an independent photo backup.
- Not designed for direct public-Internet exposure.

v1.0.1 passed **48 local regression tests** and real-browser checks at 1280×800, 1920×1080, 2560×1440, and 3840×2160. Tests cover account isolation, imports, duplicates, Live Photos, XMP, trash, index backup/restore, non-media quarantine, empty-folder cleanup, HEIC, and service APIs. This does not imply testing on every Synology model, a Docker deployment in this development environment, or all power-loss scenarios.

## Technology and development

- Python 3.11+ and a standard-library HTTP service
- SQLite
- ExifTool
- Pillow and pillow-heif
- Docker / Docker Compose
- Locally bundled Leaflet with OpenStreetMap tiles

From a source checkout, use a virtual environment:

```sh
python -m venv .venv
# Activate .venv for your operating system, then:
python -m pip install -e .
python -m unittest discover -s tests -q
```

ExifTool should also be installed and available on PATH for metadata operations. The Dockerfile installs it automatically. The optional browser smoke test requires Playwright and a supported browser.

Additional documents: [Traditional Chinese NAS guide](SYNOLOGY.md), [Changelog](CHANGELOG.md), [Architecture](ARCHITECTURE.md), and [validation record](artifacts/release-validation.txt).

## License

The source is publicly available, but no open-source license has been selected for the project. It is not currently declared MIT-licensed or licensed under another open-source license. Bundled third-party components retain their own notices, including the [Leaflet license](src/photo_organizer/web/vendor/leaflet/LICENSE).
