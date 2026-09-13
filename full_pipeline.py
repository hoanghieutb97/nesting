# coding: utf-8
"""
Nesting Pipeline: PNG -> Black Overlay -> BMP -> Potrace -> SVG -> API
"""

import os, json, subprocess, re, glob, time, shutil
from pathlib import Path
from PIL import Image
import numpy as np
import requests, xml.etree.ElementTree as ET
from msal import PublicClientApplication

DPI = 300
PIXELS_PER_CM = DPI / 2.54
MATERIAL_WIDTH, MATERIAL_HEIGHT = 900, 900  # mm (90 cm = 900 mm)
DISTANCE_PART_PART = 2       # mm
DISTANCE_PART_RAW_PLATE = 5  # mm
NESTING_TIMEOUT_MS = 60_000

CLIENT_ID = "14bc1c96-d677-4a08-8a07-68725b6bd732"
AUTHORITY = "https://starsoftonline.b2clogin.com/tfp/starsoftonline.onmicrosoft.com/B2C_1_Sign"
SCOPES = ["https://starsoftonline.onmicrosoft.com/81588f52-db64-40bd-8096-e75159abdd9a/NestingCenter"]
API_BASE_URL = "https://api-nesting.nestingcenter.com/nesting"

POTRACE_EXE = r"d:\nesting\potrace\potrace.exe"
BASE_DIR = "d:/nesting"

def create_black_overlay(png_path, output_bmp):
    """PNG -> Black silhouette (preserve DPI)"""
    print(f"  {os.path.basename(png_path)}")
    png = Image.open(png_path).convert('RGBA')
    dpi = png.info.get('dpi', (96, 96))  # Default to 96 if not set

    alpha = np.array(png.split()[3])
    bmp_array = np.where(alpha > 0, 0, 255).astype(np.uint8)
    bmp = Image.fromarray(bmp_array, 'L').convert('1')
    bmp.save(output_bmp, dpi=dpi)  # Preserve DPI in BMP
    print(f"    OK ({dpi[0]:.0f} DPI)")
    return output_bmp

def process_step1(batch_folder, output_dir):
    png_files = sorted(glob.glob(os.path.join(batch_folder, "*.png")))
    if not png_files:
        return None
    return [create_black_overlay(f, os.path.join(output_dir, f"{Path(f).stem}.bmp")) for f in png_files]

def trace_bmp_to_dxf(bmp_path, dxf_path):
    try:
        # Potrace: output DXF (pixel coordinates)
        subprocess.run([POTRACE_EXE, '-b', 'dxf', '-o', dxf_path, bmp_path],
                      capture_output=True, timeout=30, check=True)
        # Scale coordinates: pixel → mm (300 DPI: 0.0847 mm per pixel)
        scale_dxf_pixels_to_mm(dxf_path)
        return True
    except:
        return False

def scale_dxf_pixels_to_mm(dxf_path):
    """Scale all DXF coordinates from pixels to mm (0.0847 factor @ 300 DPI)"""
    MM_PER_PIXEL = 25.4 / 300  # 0.0847

    try:
        with open(dxf_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        output = []
        i = 0

        while i < len(lines):
            code = lines[i].strip()
            # 42 is a dimensionless POLYLINE bulge; scaling it changes arcs.
            if code in ['10', '20', '30']:
                output.append(lines[i])  # Append code
                if i + 1 < len(lines):
                    try:
                        value = float(lines[i + 1].strip())
                        scaled = value * MM_PER_PIXEL
                        output.append(f"{scaled:.6f}\n")
                        i += 2
                        continue
                    except:
                        pass

            output.append(lines[i])
            i += 1

        with open(dxf_path, 'w', encoding='utf-8') as f:
            f.writelines(output)
    except:
        pass

def process_step2(bmp_files, output_dir):
    print("\n[Step 2: Vectorize (DXF)]")
    dxf_files = []
    for bmp_file in bmp_files:
        dxf_output = os.path.join(output_dir, f"{Path(bmp_file).stem}.dxf")
        if trace_bmp_to_dxf(bmp_file, dxf_output):
            dxf_files.append((dxf_output, bmp_file))  # Store BMP path for fallback
            print(f"  {Path(bmp_file).stem}.dxf OK")
    return dxf_files

def extract_dxf_dimensions(dxf_path):
    """Extract dimensions from DXF EXTMAX (in mm after scaling)"""
    try:
        with open(dxf_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        # Find EXTMAX and read X, Y values (now in mm after scaling)
        for i, line in enumerate(lines):
            if '$EXTMAX' in line:
                extmax_x = None
                extmax_y = None
                for j in range(i+1, min(i+10, len(lines))):
                    code = lines[j].strip()
                    if code == '10' and j+1 < len(lines):
                        extmax_x = float(lines[j+1].strip())
                    elif code == '20' and j+1 < len(lines):
                        extmax_y = float(lines[j+1].strip())

                if extmax_x is not None and extmax_y is not None:
                    # DXF EXTMAX now contains mm (after scale_dxf_pixels_to_mm)
                    return (abs(extmax_x), abs(extmax_y))
                break
    except:
        pass

    return None  # Cannot extract, caller must handle


def _signed_area(vertices):
    """Signed polygon area; sufficient for determining contour direction."""
    return sum(
        vertices[i]['X'] * vertices[(i + 1) % len(vertices)]['Y']
        - vertices[(i + 1) % len(vertices)]['X'] * vertices[i]['Y']
        for i in range(len(vertices))
    ) / 2


def _reverse_vertices(vertices):
    """Reverse a bulge polyline without changing its geometry."""
    count = len(vertices)
    output = []
    for index in range(count - 1, -1, -1):
        vertex = {'X': vertices[index]['X'], 'Y': vertices[index]['Y']}
        previous_bulge = vertices[(index - 1) % count].get('B', 0)
        if previous_bulge:
            vertex['B'] = -previous_bulge
        output.append(vertex)
    return output


def extract_dxf_contours(dxf_path):
    """Read the closed legacy POLYLINE entities emitted by Potrace."""
    with open(dxf_path, 'r', encoding='utf-8', errors='ignore') as source:
        lines = [line.strip() for line in source]

    contours = []
    current = None
    vertex = None
    index = 0
    while index + 1 < len(lines):
        code, value = lines[index], lines[index + 1]
        index += 2
        if code == '0':
            if value == 'POLYLINE':
                current = []
                contours.append(current)
                vertex = None
            elif value == 'VERTEX' and current is not None:
                vertex = {}
                current.append(vertex)
            elif value == 'SEQEND':
                current = None
                vertex = None
        elif vertex is not None:
            if code == '10':
                vertex['X'] = round(float(value), 6)
            elif code == '20':
                vertex['Y'] = round(float(value), 6)
            elif code == '42':
                bulge = float(value)
                if abs(bulge) > 1e-12:
                    vertex['B'] = round(bulge, 9)

    contours = [
        contour for contour in contours
        if len(contour) >= 3 and all('X' in item and 'Y' in item for item in contour)
    ]
    if not contours:
        raise ValueError(f'No closed POLYLINE contours found in {dxf_path}')

    # Nesting Center: part outer contour CCW, inner contours CW.
    outer_index = max(range(len(contours)), key=lambda i: abs(_signed_area(contours[i])))
    normalized = []
    for i, vertices in enumerate(contours):
        wants_ccw = i == outer_index
        area = _signed_area(vertices)
        if (wants_ccw and area < 0) or (not wants_ccw and area > 0):
            vertices = _reverse_vertices(vertices)
        normalized.append({'Vertices': vertices})

    return [normalized[outer_index]] + [
        contour for i, contour in enumerate(normalized) if i != outer_index
    ]

class NestingAPI:
    def __init__(self):
        self.app = PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
        self.token = None

    def get_token(self):
        accounts = self.app.get_accounts()
        if accounts:
            result = self.app.acquire_token_silent(SCOPES, account=accounts[0])
            if result and "access_token" in result:
                self.token = result['access_token']
                return self.token
        result = self.app.acquire_token_interactive(SCOPES)
        if "access_token" in result:
            self.token = result['access_token']
            return self.token
        return None

    def submit_nesting_job(self, parts_data, batch_name):
        """Submit true part contours in millimetres."""
        print(f"\n[Step 3: Submit] {batch_name} ({len(parts_data)} parts)")

        parts = []
        for part_data in parts_data:
            filename = part_data['filename']
            part = {
                "Name": filename,
                "Quantity": 1,
                "RotationControl": "Free",
                "RefPt": {"X": 0, "Y": 0},
                "Contours": part_data['contours']
            }
            parts.append(part)
            print(f"  {filename}: {len(part_data['contours'])} contour(s)")

        payload = {
            "Context": {
                "Problem": {
                    "Parts": parts,
                    "RawPlates": [{"RectangularShape": {"Length": MATERIAL_WIDTH, "Width": MATERIAL_HEIGHT}, "Quantity": 1}]
                },
                "Settings": {
                    "DistancePartPart": DISTANCE_PART_PART,
                    "DistancePartRawPlate": DISTANCE_PART_RAW_PLATE,
                    "MirrorControl": "Never",
                    "NestingInHoles": True,
                    "RotationControl": "Free"
                }
            },
            "InputName": batch_name,
            "MachineNameClient": "python_pipeline",
            "StopJson": {
                "SmartStop": True,
                "Timeout": NESTING_TIMEOUT_MS
            }
        }

        try:
            response = requests.post(f"{API_BASE_URL}/start",
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                json=payload, timeout=30)
            if response.status_code in [200, 201]:
                return response.json().get('JobId')
            else:
                print(f"  API Error {response.status_code}: {response.text[:200]}")
        except Exception as e:
            print(f"  Error: {e}")
        return None

    def wait_for_job(self, job_id, timeout=300):
        print(f"  Waiting...")
        start = time.time()
        while time.time() - start < timeout:
            try:
                response = requests.get(f"{API_BASE_URL}/{job_id}",
                    headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
                if response.status_code == 200:
                    state = response.json().get('StateString')
                    if state == "Stopped":
                        return True
            except:
                pass
            time.sleep(2)
        return False

    def get_result(self, job_id):
        try:
            response = requests.get(f"{API_BASE_URL}/{job_id}/result",
                headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
            return response.json() if response.status_code == 200 else None
        except:
            return None

    def cleanup(self, job_id):
        try:
            requests.delete(f"{API_BASE_URL}/{job_id}", headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
        except:
            pass

def run_pipeline():
    print("="*70)
    print("NESTING PIPELINE")
    print("="*70)

    # Cleanup .work_batch_* directories
    for work_dir in Path(BASE_DIR).glob(".work_batch_*"):
        if work_dir.is_dir():
            shutil.rmtree(work_dir)
            print(f"  Cleaned {work_dir.name}")

    image_folders = sorted([f for f in Path(BASE_DIR).glob("image*/") if f.is_dir()])
    if not image_folders:
        print("No image folders")
        return

    api = NestingAPI()
    if not api.get_token():
        print("Auth failed")
        return

    all_results = []

    for batch_idx, folder in enumerate(image_folders, 1):
        print(f"\n[BATCH {batch_idx}] {folder.name}")

        work_dir = os.path.join(BASE_DIR, f".work_batch_{batch_idx}")
        os.makedirs(work_dir, exist_ok=True)

        print("\n[Step 1: Black Overlay]")
        bmp_files = process_step1(str(folder), work_dir)
        if not bmp_files:
            continue

        dxf_files = process_step2(bmp_files, work_dir)
        if not dxf_files:
            continue

        parts_data = []
        for dxf_path, bmp_path in dxf_files:
            contours = extract_dxf_contours(dxf_path)
            if contours:
                with Image.open(bmp_path) as img:
                    w_px, h_px = img.size
            parts_data.append({
                'filename': Path(dxf_path).stem,
                'contours': contours,
                'source_size_px': [w_px, h_px]
            })

        job_id = api.submit_nesting_job(parts_data, f"batch_{batch_idx}")
        if not job_id:
            continue

        if api.wait_for_job(job_id):
            result = api.get_result(job_id)
            if result and 'Result' in result:
                # Save parts order mapping (PartIndex → filename)
                parts_mapping = {str(i): part['filename'] for i, part in enumerate(parts_data)}
                parts_metadata = {
                    str(i): {
                        'filename': part['filename'],
                        'source_size_px': part['source_size_px'],
                        'ref_pt_mm': [0, 0]
                    }
                    for i, part in enumerate(parts_data)
                }
                result_data = result['Result']
                result_data['PartsMapping'] = parts_mapping
                result_data['PartsMetadata'] = parts_metadata
                all_results.append({'batch': batch_idx, 'folder': folder.name, 'result': result_data})
                scrap = result_data.get('Scrap', 0) * 100
                print(f"\n  Scrap: {scrap:.2f}%")
            api.cleanup(job_id)

    if all_results:
        print(f"\n{'='*70}")
        print("COMPLETE")
        print(f"{'='*70}")
        with open(os.path.join(BASE_DIR, "nesting_pipeline_results.json"), 'w') as f:
            json.dump(all_results, f, indent=2)
        print(f"Results: nesting_pipeline_results.json")

if __name__ == "__main__":
    run_pipeline()
