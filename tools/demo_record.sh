#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
#
# 錄影用的展示腳本：一次錄影剪出兩支 GIF。
#
# 與實驗腳本（m2_ra3.sh）的差別：
#   ① **不清場、不啟動場景**——場景必須已經跑起來，本腳本只負責「演」
#   ② 任務間隔縮短為 12 秒（實驗用 25 秒），讓 25 秒內就有完整的
#      「產生 → 指派 → 車動」，剪得進 GIF
#   ③ 目的地固定 coe / hardware_2——這兩處要開門且距離遠，畫面最有內容
#   ④ 開場先證明 22011 是我們的 bridge，不是 rmf_demos 的 fleet_manager
#   ⑤ 結束後標出哪一趟的 dist_penalty 最大（＝FIFO 最笨的那趟，最值得剪）
#
# 建議的四視窗排法（錄一次，事後裁兩次）：
#   ┌─────────────────┬──────────────────┐
#   │                 │ ① 本腳本（決策） │ ← GIF 1 裁這塊
#   │  Gazebo / RViz  ├──────────────────┤
#   │                 │ ② mosquitto_sub  │ ← GIF 2 裁這兩塊
#   │                 ├──────────────────┤
#   │                 │ ③ bridge log     │
#   └─────────────────┴──────────────────┘
#
#   視窗 ②：mosquitto_sub -h localhost -t 'vda5050/v3/#' -v
#   視窗 ③：tail -f /tmp/vda5050_bridge.jsonl
#
# 前置：先在另一個終端機啟動場景（約 30–40 秒）
#   source /opt/ros/humble/setup.bash && source ~/rmf_ws/install/setup.bash && \
#   ros2 launch fifo_dispatcher office_vda5050.launch.xml
#
# 用法：bash tools/demo_record.sh [policy]      預設 fifo
#
# 注意：不可加 set -u——ROS 的 setup.bash 會引用未定義變數而中斷

POLICY="${1:-fifo}"
LOG=/tmp/demo_record.jsonl
FM=http://127.0.0.1:22011/open-rmf/rmf_demos_fm

source /opt/ros/humble/setup.bash
source /root/rmf_ws/install/setup.bash

# ── 前置檢查：場景必須「已經在跑」（與實驗腳本相反）────────────
echo "════════ 前置檢查 ════════"
FAIL=0
ADP=$(pgrep -fc 'rmf_demos_fleet_adapter/fleet_adapter')
VEH=$(pgrep -fc 'lib/fifo_dispatcher/vda5050_vehicle')
GZ=$(pgrep -fc 'ign gazebo server')
[ "${ADP}" -ge 1 ] || { echo "✗ fleet_adapter 沒在跑"; FAIL=1; }
[ "${VEH}" -ge 2 ] || { echo "✗ vda5050_vehicle 只有 ${VEH} 個（需要 2）"; FAIL=1; }
[ "${GZ}"  -ge 1 ] || { echo "✗ Gazebo 沒在跑（錄影需要畫面）"; FAIL=1; }
if [ "${FAIL}" -ne 0 ]; then
  echo
  echo "請先在另一個終端機啟動場景："
  echo "  source /opt/ros/humble/setup.bash && source ~/rmf_ws/install/setup.bash && \\"
  echo "  ros2 launch fifo_dispatcher office_vda5050.launch.xml"
  exit 1
fi
echo "✓ 場景就緒（adapter ${ADP}｜vehicle ${VEH}｜Gazebo ${GZ}）"

# ── 開場：證明 22011 是我們的 bridge ──────────────────────────
echo
echo "════════ 這個 port 是誰的？ ════════"
echo "\$ ss -ltnp | grep 22011"
ss -ltnp 2>/dev/null | grep 22011
echo
echo "  ↑ 原本 rmf_demos 是由 fleet_manager 佔用 22011（模擬廠商私有 API）"
echo "    現在是我們的 vda5050_bridge —— 同一個 port，介面換成 VDA5050"
echo
echo "\$ ros2 node list | grep vda5050"
timeout 5 ros2 node list 2>/dev/null | grep vda5050 || echo "  （ROS graph 查詢逾時，屬本機已知現象，不影響運作）"
echo
echo "\$ curl .../status/ —— 車況由 VDA5050 的 state 訊息組出來"
curl -s --max-time 4 "${FM}/status/" | /usr/bin/python3 -m json.tool 2>/dev/null | head -14

# ── 倒數，讓錄影者按下錄製 ─────────────────────────────────
echo
echo "════════ 3 秒後開始派工，請開始錄影 ════════"
for i in 3 2 1; do printf '  %s...\n' "$i"; sleep 1; done
echo

# ── 主秀 ──────────────────────────────────────────────────
# ⚠️ 參數是為了「讓 FIFO 的笨看得見」而調的，與實驗用的參數不同。
# 這組參數是踩過兩次錯之後定下來的（2026/08/15）：
#
# 錯誤一：目的地用 coe / hardware_2 **交替**
#   兩台車剛好一左一右停著（R1 charger x=10.43、R2 charger x=20.42），
#   coe 在最左、hardware_2 在最右 → FIFO 每次都碰巧派到最近的那台，
#   dist_penalty 全是 0，完全看不出它笨。
#
# 錯誤二：改成**只送 coe**
#   第一台車跑完就停在 coe，第二台車又被派到同一個節點 →
#   兩台頂在一起（實測距離 1.5 m）。RMF 的交通機制有擋住、沒有真的相撞
#   （errors=[]、fieldViolation=false、無急停），但終點被佔用，畫面像卡死。
#
# 現在的做法：**目的地偏左，且三點互相隔開 5 公尺以上**
#   coe(5.35,-4.98) / patrol_B(7.99,-10.78) / patrol_D2(10.25,-3.09)
#   而 R2 的起點在 x=20.42（場地最右）。
#
#   實算 —— 若 FIFO 選到遠的 R2：
#     coe        R1=5.12m  R2=15.07m  → penalty  9.96 m
#     patrol_B   R1=5.74m  R2=13.58m  → penalty  7.84 m
#     patrol_D2  R1=2.51m  R2=10.41m  → penalty  7.90 m
#
#   三點互相距離：coe-patrol_B 6.37m、coe-patrol_D2 5.24m、patrol_B-patrol_D2 8.02m
#   （刻意不用 supplies——它離 coe 只有 1.72 m，正好是上次頂在一起的距離）
#
# 為什麼 interval 要 60 秒：
#   ① dist_penalty 是拿「當下可選的車」互比——只有一台閒著時恆為 0，
#      必須兩台都閒，比較才有意義
#   ② 實測周轉 35–55 秒，間隔 12 秒會塞車（第一次錄影就出現 timeout_not_started）
rm -f "$LOG"
timeout 300 ros2 run fifo_dispatcher dispatcher --ros-args \
  -p policy:="${POLICY}" \
  -p count:=3 \
  -p interval_sec:=60.0 \
  -p places:="coe,patrol_B,patrol_D2" \
  -p log_path:="$LOG"

# ── 收尾：標出最值得剪的片段 ───────────────────────────────
echo
echo "════════ 剪輯提示 ════════"
/usr/bin/python3 - "$LOG" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding='utf-8') if l.strip()]
tasks = [r for r in rows if r.get('event') != 'run_started' and r.get('status') == 'completed']
if not tasks:
    print('  沒有完成的任務——場景可能中途出事，建議重錄')
    sys.exit()
print(f"  {'序':<4}{'目的地':<12}{'派給':<14}{'理由':<28}{'多走(m)':>8}{'周轉(s)':>9}")
for t in sorted(tasks, key=lambda x: x.get('seq', 0)):
    pen = t.get('dist_penalty')
    print(f"  {t.get('seq',''):<4}{t.get('place',''):<12}{t.get('robot',''):<14}"
          f"{t.get('reason','')[:26]:<28}{(pen if pen is not None else 0):>8.1f}"
          f"{t.get('turnaround_sec',0):>9.1f}")
best = max(tasks, key=lambda x: x.get('dist_penalty') or 0)
pen = best.get('dist_penalty') or 0
print()
if pen >= 5:
    print(f"  ⭐ 剪第 {best.get('seq')} 趟：派給 {best.get('robot')} 去 {best.get('place')}，"
          f"比最近的車多走 {pen:.1f} 公尺")
    print(f"     這一趟看得出 FIFO 的笨——字幕可寫「近的車就在旁邊，但 FIFO 只看誰先排隊」")
else:
    print(f"  ⚠️ 這次最大的 dist_penalty 只有 {pen:.1f} m，FIFO 剛好沒選錯，畫面說服力不足")
    print("     可能原因：")
    print("       ① 每次派工時只有一台車閒著 → penalty 定義上恆為 0")
    print("          （它是拿「當下可選的車」互比，只有一台就沒得比）")
    print("       ② 兩台車的位置剛好讓 FIFO 選對了")
    print("     處置：直接再跑一次本腳本即可——車輛位置變了，結果就會不同")
timeouts = [t for t in tasks if t.get('status') == 'timeout_not_started']
if timeouts:
    print(f"\n  ⚠️ 有 {len(timeouts)} 個任務逾時未開始（seq={[t.get('seq') for t in timeouts]}）")
    print("     多半是任務間隔太短、前一個還沒跑完就又派下一個。本腳本已用 60 秒間隔避免。")
PY
echo
echo "  紀錄檔：$LOG"
echo "  ⚠️ 本腳本不清場——場景仍在跑，要停請回 launch 的終端機按一次 Ctrl+C 並等 10–20 秒"
