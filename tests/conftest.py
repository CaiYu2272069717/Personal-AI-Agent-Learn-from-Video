"""Pytest 全局 fixtures"""

import sys
from pathlib import Path

# 确保 src 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
