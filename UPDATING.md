# Updating RenderLock

The Windows portable build includes an **Updates** page from version 0.3.0b4.
Users of an older build must download and extract this version once.

1. Open Updates to check the published release and read what's new.
2. Click **Download update**. The current app stays open while the ZIP is
   downloaded and verified. Checking at startup fetches metadata only; turn it
   off on this page if you prefer manual checks.
3. Finish any current job, then click **Install and restart**. RenderLock saves
   its settings, closes, installs the verified files and reopens.

Updates come only from `cravvnn/RenderLock` on GitHub. Beta installations receive
newer published beta and stable versions; stable installations receive stable
versions only. Draft releases are excluded. Notes are shown inside the app;
the full release can also be opened on GitHub. Checks require internet access
and can be deferred by GitHub's API limits or an incomplete release upload.
Source checkouts cannot install updates over development files.

Settings, the shared asset cache and exports are retained. Installation replaces
only manifest-owned application files and refuses collisions with unowned files.
Keep the extracted app in a writable folder. One portable session per app folder
is allowed, so another session cannot overlap an update.

The installer, recovery window and update errors use the same RenderLock theme,
logo and window icon as the editor. The standalone recovery runtime bundles these
brand assets so it can display them even if the installed app needs repair.
It also includes the private font loader and can reuse the verified display-font
cache prepared from the selected game installation. Font bytes are not bundled
with the application or updater; unavailable faces fall back to Segoe UI.

The updater verifies GitHub's ZIP SHA-256 digest and the complete package manifest,
backs up replaced files, and checks Python, the UI imports and Tk after install.
A failed install rolls back. An interrupted install is recovered on the next
launch using an independent copy of the Python runtime. Keep the app's
`.renderlock-update` folder if an update or recovery fails; it contains the backup
and journal. The previous update workspace is replaced when downloading a
different update. An update cannot repair an incompatible game/replay skeleton.

## Publishing a compatible update

Publish the normal Windows ZIP as a release asset, with this exact convention:

- Tag `v0.3.0b4` (or `0.3.0b4`), using the actual app version.
- Asset `RenderLock-0.3.0b4-Windows-x64.zip`.
- ZIP root `RenderLock-0.3.0b4-Windows-x64/`, including `release_manifest.json`.
- Paste the prepared release description into GitHub's release body; these are
  the notes users see inside RenderLock.

Use the project packager, which includes every updater component in the manifest.
Upload the complete ZIP and allow GitHub to compute its asset digest before users
check. No separate update server or checksum upload is required. The updater
examines up to the most recent 100 releases and compares version numbers rather
than trusting upload order. A missing ZIP or digest defers installation. GitHub's
TLS and release-asset digest establish the download source; packages are not
separately code-signed.
