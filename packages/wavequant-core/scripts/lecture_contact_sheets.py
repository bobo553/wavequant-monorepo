"""Arrange existing slide renders for private inspection; no source edits."""
from pathlib import Path
from PIL import Image

root = Path('notes/n_lecture_20260908')
paths = sorted(root.glob('slide_*.png'))
for start in range(0, len(paths), 6):
    sheet = Image.new('RGB', (1440, 1620), 'white')
    for offset, path in enumerate(paths[start:start+6]):
        with Image.open(path) as slide:
            slide.thumbnail((720, 540))
            sheet.paste(slide, ((offset % 2)*720, (offset//2)*540))
    sheet.save(root/f'contact_{start+1:02d}_{min(start+6,len(paths)):02d}.png')
