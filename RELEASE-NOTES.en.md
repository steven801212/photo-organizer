# Photo Organizer v1.0.1

**English** | [繁體中文（台灣）](RELEASE-NOTES.md)

Application release: September 8, 2026. GitHub publication: September 16, 2026.

## Changes

- Photos, years, and dates are sorted newest first, with unknown dates last.
- Year folders are collapsed by default and expand on demand.
- The main content is top-aligned and uses the available window width. Gallery columns adapt automatically.
- Toolbars wrap on narrower windows; preview and folder scrolling adapt to window height.
- No photo relocation, database schema migration, or reindexing is required for these UI changes.

## Upgrade from v1.0.0 or Beta 7

1. Wait for active organizing jobs to finish and back up the index.
2. Stop the photo-organizer project in Container Manager.
3. Extract the v1.0.1 deployment ZIP into PhotoOrganizer/app, replacing application files. Preserve your existing .env and any custom volume mounts or ports.
4. Rebuild the image and recreate the container. The image tag is photo-organizer:1.0.1; restarting an old container is not sufficient.
5. Reload the browser (Ctrl+F5 if needed) and confirm v1.0.1 appears in the interface.
6. Before the next import, scan the Inbox again to refresh the plan.

Do not delete PhotoInbox, PhotoLibrary, PhotoOrganizer/data, backups, or rejected. Existing photos are not automatically moved during this update.

If using a terminal in the app directory:

```sh
docker compose build --pull photo-organizer
docker compose up -d --force-recreate photo-organizer
```

## Validation and scope

48 local regression tests passed. Browser checks cover 1280x800 through 3840x2160, date ordering, collapsed folders, selection, login/account switching, previews, and pagination.

No Docker daemon or direct NAS deployment was available during this validation. Check the deployed version and service status on your NAS. Use one Organizer Service per set of folders and keep an independent backup of your photos.

The app interface remains Traditional Chinese (Taiwan). English and Traditional Chinese documentation are available in the repository. The versioned deployment ZIP preserves the original application release and its documentation.
