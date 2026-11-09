"""Export the owner-supplied Arrify icon at the app and web asset sizes."""
from pathlib import Path
from PIL import Image
ROOT = Path(__file__).resolve().parents[1]
assets = ROOT / 'web/assets'
with Image.open(assets / 'arrify-icon-source.jpg') as source:
    source = source.convert('RGB')
    for size, name in [(192, 'icon-192.png'), (512, 'icon-512.png'), (180, 'apple-touch-icon.png')]:
        source.resize((size, size), Image.Resampling.LANCZOS).save(assets / name)
    source.resize((1024, 1024), Image.Resampling.LANCZOS).save(ROOT / 'ios/App/Assets.xcassets/AppIcon.appiconset/AppIcon.png')
