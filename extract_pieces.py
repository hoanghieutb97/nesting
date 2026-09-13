# coding: utf-8
"""
Tách mỗi mảnh từ tach.png thành các file PNG riêng biệt
Lưu vào thư mục image1/
"""

import os
from pathlib import Path
from PIL import Image
import numpy as np
import cv2

def extract_pieces(input_png, output_dir):
    """Tách các mảnh riêng biệt từ ảnh và lưu vào thư mục"""

    print(f"Đang xử lý: {input_png}")

    # Tạo thư mục output nếu chưa có
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Mở ảnh
    img = Image.open(input_png).convert('RGBA')
    img_array = np.array(img)

    # Tạo binary mask từ alpha channel (non-transparent pixels)
    alpha = img_array[:, :, 3]
    binary_mask = (alpha > 0).astype(np.uint8) * 255

    print(f"  Kích thước ảnh: {img.size}")

    # Tìm contours (tách từng mảnh - hiệu quả với mảnh sát nhau)
    contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print(f"  Tìm thấy {len(contours)} mảnh")

    if len(contours) == 0:
        print("  ⚠ Không tìm thấy mảnh nào!")
        return

    # Tách từng mảnh theo contour
    for piece_idx, contour in enumerate(contours, 1):
        # Lấy bounding box của contour
        x, y, w, h = cv2.boundingRect(contour)

        # Tăng bounding box thêm padding
        padding = 10
        x_min = max(0, x - padding)
        y_min = max(0, y - padding)
        x_max = min(img_array.shape[1], x + w + padding)
        y_max = min(img_array.shape[0], y + h + padding)

        # Cắt ảnh theo bbox
        cropped = img_array[y_min:y_max, x_min:x_max]

        # Tạo mask cho contour này (chỉ lấy pixels thuộc contour)
        mask = np.zeros((y_max - y_min, x_max - x_min), dtype=np.uint8)
        # Dịch contour về tọa độ cục bộ của crop
        contour_offset = contour.copy().astype(np.int32)
        contour_offset[:, :, 0] -= x_min
        contour_offset[:, :, 1] -= y_min
        cv2.drawContours(mask, [contour_offset], 0, 255, -1)

        # Apply mask: chỉ giữ pixels trong contour
        cropped_masked = cropped.copy()
        cropped_masked[mask == 0, 3] = 0  # Set alpha=0 cho pixels ngoài contour

        # Tạo ảnh RGBA từ masked crop
        piece_img = Image.fromarray(cropped_masked, 'RGBA')

        # Lưu file
        output_path = os.path.join(output_dir, f"piece_{piece_idx:03d}.png")
        piece_img.save(output_path, dpi=(300, 300))
        print(f"  ✓ {Path(output_path).name} ({piece_img.size[0]}x{piece_img.size[1]})")

    print(f"\n✓ Đã lưu {len(contours)} mảnh vào {output_dir}")

if __name__ == "__main__":
    BASE_DIR = "d:/nesting"
    input_file = os.path.join(BASE_DIR, "tach.png")
    output_folder = os.path.join(BASE_DIR, "image1")

    if not os.path.exists(input_file):
        print(f"❌ Không tìm thấy {input_file}")
        exit(1)

    extract_pieces(input_file, output_folder)
