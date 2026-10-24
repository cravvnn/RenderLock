# RenderLock artwork

Source artwork for the RenderLock identity, as delivered by the designer.

- `renderlockmain.png` — the main emblem. Used for the program icon: the window
  and taskbar icon in the app, and the icon embedded in the launcher
  executable. 1280 x 1280.
- `renderlock.png` — alternate composition.
- `renderlockclear.png` — alternate composition.
- `RenderLock.ico` — multi-size icon generated from `renderlockmain.png`
  (16-64 px BMP entries, 128/256 px PNG entries).
- `renderlock-intro.gif` — looping README title clip supplied by Cravvnn, resized
  to 600 x 600. It uses only the opening Blender/title shot, cut before the first
  switch to the app screen. The source MP4 is not included in the repository.

Note: the three source images carry WebP data exactly as delivered (browsers
and Windows Photos open them normally).

Regenerate the icon and re-embed it into a freshly built launcher:

```powershell
blender -b --factory-startup --python tools/make_app_icon.py
cargo build --release --manifest-path tools/launcher/Cargo.toml
python tools/launcher/set_icon.py
```
