#!/usr/bin/env python3
# 資料版本標記：回答「這批實驗資料是哪一版程式跑出來的」
#
# 每個會寫 JSON Lines 的元件在啟動時寫一行 run_started，帶上本模組算出的標記。
# M4 分析時先比對 code_sha，不同版就不可混用
# （驗收條件見 notes/交接文件.md 九之一第 5 項）。
#
# 為何用「內容雜湊」而不是交接文件建議的 mtime 或 git hash：
#   - git hash：本專案目前不是 git repo（2026/08/13 實測 `git rev-parse` 回 fatal），
#     且實驗常在未 commit 的狀態下跑，同一個 hash 會蓋住兩版不同的程式
#   - mtime：`colcon build` 會把檔案複製到 install/，複製後的時間戳不保證跟著來
#   - 內容雜湊：程式碼改一個字就變，複製、搬家、重 build 都不會誤判
#
# ⚠️ 這是代理指標，已知會誤判的情況（原則 7）：
#    code_sha 涵蓋整個套件，改動與該次實驗無關的節點（例如只跑 M3b 卻改了
#    vda5050_vehicle.py）也會讓它變動。要判斷「是否真的不可比」，看 files
#    裡的逐檔雜湊，不要只看 code_sha。

import hashlib
import os
import time

# 由各元件在啟動時呼叫一次，回傳寫進紀錄第一行的版本標記。
# 排除本檔自己：改這裡的註解不影響實驗行為，不該讓資料看起來不可比。
def code_version():
    here = os.path.dirname(os.path.abspath(__file__))
    me = os.path.basename(__file__).replace('.pyc', '.py')
    files, combined = {}, hashlib.sha256()
    for name in sorted(f for f in os.listdir(here)
                       if f.endswith('.py') and f != me):
        with open(os.path.join(here, name), 'rb') as f:
            data = f.read()
        files[name] = hashlib.sha256(data).hexdigest()[:8]
        combined.update(data)
    return {
        'code_sha': combined.hexdigest()[:12],
        'files': files,
        'code_dir': here,          # install/ 或 src/，用來確認跑的是哪一份
        'started_iso': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
