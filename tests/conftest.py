"""pytest 共享 fixture。"""
import sys
from pathlib import Path

# 项目根目录加入搜索路径
sys.path.insert(0, str(Path(__file__).parent.parent))
