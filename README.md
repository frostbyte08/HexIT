# hexit

Desktop image-to-color palette generator for **Windows, macOS and Linux**. Fully local: no network, no uploads.

Drop an image, get a palette ranked by prominence. Click a swatch to copy its HEX, remove colors you don't want, and export to PNG, JSON or CSS.

## Features
- Drag-and-drop or file picker (PNG / JPG / JPEG / WEBP); EXIF orientation respected
- Palette size 3-10 (default 6), regenerate, per-swatch removal
- Click-to-copy HEX; RGB / HSL details on hover or via the Details toggle
- Copy all HEX (one per line), export PNG, JSON (HEX/RGB/HSL) and CSS custom properties

## Run
Requires Python 3.9+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m hexit              # or: python -m hexit path/to/image.png
```

Or install the `hexit` command: `pip install .`

## Build a standalone app (optional)
```bash
pip install pyinstaller
pyinstaller --windowed --name hexit hexit/__main__.py   # run from the repo root
```
The app appears in `dist/`. Build on each OS you want to target.

## How extraction works (`hexit/extract.py`)
1. The image is downscaled to at most 160 px for processing; the original is never modified.
2. Pixels with alpha < 125 are ignored; the rest are bucketed (5 bits/channel) to reduce noise.
3. Buckets are converted to CIELAB and clustered with weighted k-means++ (seeded; Regenerate changes the seed).
4. Clusters closer than ΔE 8 are merged so swatches stay distinct, then ranked by pixel share.

`extract.py` depends only on numpy, so it is easy to reuse or test on its own.

## License
MIT
