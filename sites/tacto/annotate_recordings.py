#!/usr/bin/env python3
"""Bake explanatory pointer overlays into existing recordings; keep sources intact.

Development-only dependencies: ffmpeg and rsvg-convert. No live UI control.
Annotation coordinates/timings are in assets/demos/pointer-guide.json.
"""
import argparse
import html
import json
import math
from pathlib import Path
import subprocess
import tempfile


def svg_frame(spec, time, poster=False):
    markers = spec['markers']
    index = max(i for i, row in enumerate(markers) if row['time'] <= time)
    current = spec['poster_marker'] if poster else markers[index]
    previous = markers[max(0, index - 1)]
    elapsed = max(0, time - current['time'])
    # Ease between targets. The arrow is an annotation, never a measured mouse.
    progress = min(1, elapsed / .20)
    progress = progress * progress * (3 - 2 * progress)
    x = current['x'] if poster else previous['x'] + (current['x'] - previous['x']) * progress
    y = current['y'] if poster else previous['y'] + (current['y'] - previous['y']) * progress
    width, height = spec['width'], spec['height']
    scale = 1 if width < 600 else 1.55
    stroke = 2.5 * scale
    size = 17 * scale
    label = html.escape(current['label'])
    label_width = min(width-16, max(125, len(current['label']) * size * .61 + 24))
    lx = max(8, min(width-label_width-8, x + 22*scale))
    ly = max(58, min(height-85*scale, y - 47*scale))
    highlight = ''
    if current.get('box') and current['kind'] != 'idle':
        bx, by, bw, bh = current['box']
        highlight = f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="8" fill="#2b7de9" fill-opacity=".07" stroke="#2b7de9" stroke-width="{stroke}"/>'
    pulse = ''
    if current['kind'] == 'click' and progress >= .8:
        phase = (elapsed % .65) / .65
        radius = (15 + phase*18)*scale
        pulse = f'<circle cx="{x}" cy="{y}" r="{radius}" fill="#2b7de9" fill-opacity=".12" stroke="#2b7de9" stroke-opacity="{1-phase*.7}" stroke-width="{stroke}"/>'
    tag_width = 141*scale
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<g font-family="Arial,sans-serif">
<rect x="{width-tag_width-8}" y="8" width="{tag_width}" height="{27*scale}" rx="{6*scale}" fill="#153864"/>
<text x="{width-tag_width+2}" y="{8+18*scale}" font-size="{12*scale}" font-weight="700" fill="white">GUIDE POINTER</text>
{highlight}{pulse}
<rect x="{lx}" y="{ly}" width="{label_width}" height="{33*scale}" rx="{6*scale}" fill="#153864"/>
<text x="{lx+12}" y="{ly+23*scale}" font-size="{size}" font-weight="700" fill="white">{label}</text>
<g transform="translate({x:.2f} {y:.2f}) scale({scale})"><path d="M1 1V31L9 23L15 37L22 34L16 21H29Z" fill="white" stroke="#142133" stroke-width="2.4" stroke-linejoin="round"/></g>
</g></svg>'''


def render_svg(svg, output):
    subprocess.run(['rsvg-convert', '-o', str(output)], input=svg.encode(), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, default=Path(__file__).parent/'dist/assets/demos')
    args = parser.parse_args()
    spec = json.loads((args.assets/'pointer-guide.json').read_text())
    for name, row in spec['scenarios'].items():
        with tempfile.TemporaryDirectory(prefix='tacto-pointer-') as temporary:
            work = Path(temporary)
            for n in range(math.ceil(row['seconds']*24)):
                render_svg(svg_frame(row, n/24), work/f'frame-{n:04d}.png')
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
                '-i',str(args.assets/row['source']),'-framerate','24','-i',str(work/'frame-%04d.png'),
                '-filter_complex','[0:v][1:v]overlay=0:0:shortest=1:format=auto',
                '-c:v','libx264','-preset','slow','-crf','21','-pix_fmt','yuv420p',
                '-movflags','+faststart','-an','-t',str(row['seconds']),str(args.assets/row['output'])],check=True)
            render_svg(svg_frame(row, row['seconds']-.01, poster=True), work/'poster-overlay.png')
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
                '-i',str(args.assets/row['poster_source']),'-i',str(work/'poster-overlay.png'),
                '-filter_complex','[0:v][1:v]overlay=0:0:format=auto',
                '-frames:v','1',str(args.assets/row['poster_output'])],check=True)
        print(name, 'annotated; original recording and screenshot preserved', flush=True)


if __name__ == '__main__':
    main()
