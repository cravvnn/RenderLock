# DeadBlend artwork

Source artwork for the DeadBlend identity, as delivered by the designer.

- `deadblendmain.png` — the main emblem. Used for the program icon: the window
  and taskbar icon in the app, and the icon embedded in the launcher
  executable. 1280 x 1280.
- `deadblend.png` — alternate composition.
- `deadblendclear.png` — alternate composition.
- `DeadBlend.ico` — multi-size icon generated from `deadblendmain.png`
  (16-64 px BMP entries, 128/256 px PNG entries).

Note: the three source images carry WebP data exactly as delivered (browsers
and Windows Photos open them normally).

Regenerate the icon and re-embed it into a freshly built launcher:

```powershell
blender -b --factory-startup --python tools/make_app_icon.py
cargo build --release --manifest-path tools/launcher/Cargo.toml
python tools/launcher/set_icon.py
```
