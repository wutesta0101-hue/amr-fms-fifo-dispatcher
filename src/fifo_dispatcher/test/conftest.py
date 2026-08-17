# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
#
# 讓 pytest 從 test/ 往上一層找得到 fifo_dispatcher 套件，
# 這樣不必先 colcon build 也能測——測的是 src 的當下版本，不是 install/ 的舊複本。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
