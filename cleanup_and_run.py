# coding: utf-8
import os
import shutil
from pathlib import Path

# Xóa thư mục .work_batch_1 nếu tồn tại
work_dir = "d:/nesting/.work_batch_1"
if os.path.exists(work_dir):
    shutil.rmtree(work_dir)
    print(f"✓ Đã xóa {work_dir}")
else:
    print(f"Thư mục {work_dir} không tồn tại")

print("\nChạy pipeline...\n")

# Chạy full_pipeline.py
import full_pipeline
full_pipeline.run_pipeline()
