# coding: utf-8
"""Render Nesting Center transformations onto a transparent 90 x 90 cm PNG."""

import json
import math
from pathlib import Path

from PIL import Image

DPI = 300
PX_PER_MM = DPI / 25.4
MATERIAL_W_MM = 900
MATERIAL_H_MM = 900
BASE_DIR = Path("d:/nesting")


def _forward_matrix(source_size, transformation):
    """Build source-PIL-pixels -> destination-PIL-pixels affine matrix."""
    _, source_h = source_size
    angle = transformation['Rotation']
    cosine, sine = math.cos(angle), math.sin(angle)
    mirror_x = -1 if transformation.get('Mirror', False) else 1
    insertion = transformation['InsertionPt']

    # API: local +Y up, RefPt=(0,0) at image lower-left, then mirror,
    # CCW rotation and translation. PIL canvas coordinates use +Y down.
    return (
        mirror_x * cosine,
        sine,
        PX_PER_MM * insertion['X'] - source_h * sine,
        -mirror_x * sine,
        cosine,
        MATERIAL_H_MM * PX_PER_MM - source_h * cosine - PX_PER_MM * insertion['Y'],
    )


def _transform_part(source, transformation, canvas_size):
    """Affine-transform one part into a tightly cropped destination image."""
    a, b, c, d, e, f = _forward_matrix(source.size, transformation)
    corners = [(0, 0), (source.width, 0), (0, source.height), source.size]
    points = [(a * x + b * y + c, d * x + e * y + f) for x, y in corners]
    left = math.floor(min(x for x, _ in points))
    top = math.floor(min(y for _, y in points))
    right = math.ceil(max(x for x, _ in points))
    bottom = math.ceil(max(y for _, y in points))

    clip_left, clip_top = max(0, left), max(0, top)
    clip_right, clip_bottom = min(canvas_size[0], right), min(canvas_size[1], bottom)
    if clip_left >= clip_right or clip_top >= clip_bottom:
        raise ValueError('Part transformation lies outside the material')

    determinant = a * e - b * d
    inverse = (
        e / determinant,
        -b / determinant,
        (e * (clip_left - c) - b * (clip_top - f)) / determinant,
        -d / determinant,
        a / determinant,
        (-d * (clip_left - c) + a * (clip_top - f)) / determinant,
    )
    rendered = source.transform(
        (clip_right - clip_left, clip_bottom - clip_top),
        Image.Transform.AFFINE,
        inverse,
        resample=Image.Resampling.BICUBIC,
    )
    return rendered, (clip_left, clip_top), (left, top, right, bottom)


def create_nesting_visualization():
    with (BASE_DIR / 'nesting_pipeline_results.json').open(encoding='utf-8') as source:
        batches = json.load(source)
    if not batches:
        raise ValueError('No nesting results found')

    canvas_size = (
        round(MATERIAL_W_MM * PX_PER_MM),
        round(MATERIAL_H_MM * PX_PER_MM),
    )
    canvas = Image.new('RGBA', canvas_size, (0, 0, 0, 0))

    for batch in batches:
        result = batch['result'].get('Result', batch['result'])
        image_dir = BASE_DIR / batch['folder']
        mapping = result.get('PartsMapping', {})
        metadata = result.get('PartsMetadata', {})
        for plate in result.get('RawPlatesNested', []):
            for nested_part in plate.get('PartsNested', []):
                part_key = str(nested_part['PartIndex'])
                filename = metadata.get(part_key, {}).get('filename') or mapping.get(part_key)
                if not filename:
                    raise KeyError(f'No filename mapping for PartIndex {part_key}')
                with Image.open(image_dir / f'{filename}.png') as image:
                    part = image.convert('RGBA')
                rendered, position, bounds = _transform_part(
                    part, nested_part['Transformation'], canvas.size
                )
                canvas.alpha_composite(rendered, position)
                rotation = math.degrees(nested_part['Transformation']['Rotation']) % 360
                print(
                    f"{filename}: position={nested_part['Transformation']['InsertionPt']}, "
                    f"rotation={rotation:.2f}deg, "
                    f"mirror={nested_part['Transformation'].get('Mirror', False)}, bounds={bounds}"
                )

    output_path = BASE_DIR / 'nesting_final_90x90cm_300dpi.png'
    canvas.save(output_path, dpi=(DPI, DPI))
    print(f'Saved {output_path} ({canvas.width}x{canvas.height}px @ {DPI} DPI)')
    return output_path


if __name__ == '__main__':
    create_nesting_visualization()
