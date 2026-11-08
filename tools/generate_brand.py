"""Render Arrify's original audio-bar A. Pillow is a development dependency."""
from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1]
bars=[(240,490,320,780),(356,340,436,780),(472,230,552,590),(588,340,668,780),(704,490,784,780)]
def mark(size):
 im=Image.new('RGB',(2048,2048),'#091512');d=ImageDraw.Draw(im)
 for box in bars:d.rounded_rectangle(tuple(x*2 for x in box),radius=64,fill='#4be89b')
 d.rounded_rectangle((560,1130,1488,1290),radius=72,fill='#4be89b')
 return im.resize((size,size),Image.Resampling.LANCZOS)
assets=ROOT/'web/assets'
for size,name in [(192,'icon-192.png'),(512,'icon-512.png'),(180,'apple-touch-icon.png')]:mark(size).save(assets/name)
mark(1024).save(ROOT/'ios/App/Assets.xcassets/AppIcon.appiconset/AppIcon.png')
for name in ['arrify-mark.jpg','arrify-logos.jpg','arrify-wordmark.jpg']:mark(1024).save(assets/name,quality=95)
svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><rect width="1024" height="1024" fill="#091512"/>'
for x,y,r,b in bars:svg+=f'<rect x="{x}" y="{y}" width="{r-x}" height="{b-y}" rx="32" fill="#4be89b"/>'
svg+='<rect x="280" y="565" width="464" height="80" rx="36" fill="#4be89b"/></svg>'
(assets/'arrify-mark.svg').write_text(svg+'\n')
