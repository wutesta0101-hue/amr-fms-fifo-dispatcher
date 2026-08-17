#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
# M2 步驟 5：單組對照實驗（VDA5050 版）
#
# 與 M3b 的 /tmp/rg.sh 只有一個差異：**啟動 office_vda5050.launch.xml**
#   （fleet_manager → vda5050_bridge + vda5050_vehicle × 2）
# 控制變因、任務序列、間隔、量測方式全部照抄，這樣兩批資料才可比。
#
# 用法：bash /tmp/m2_rg.sh <policy> <round>      policy = rmf | fifo | nearest
# 注意：不可加 set -u——ROS 的 setup.bash 會引用未定義變數而中斷
POLICY="$1"
ROUND="${2:-1}"

PLACES="patrol_A1,hardware_2,supplies,lounge,coe,patrol_B,pantry,patrol_D2"
COUNT=8
INTERVAL=25.0
LOG="/tmp/m2exp_${POLICY}_r${ROUND}.jsonl"
BRIDGE_LOG="/tmp/m2bridge_${POLICY}_r${ROUND}.jsonl"
FM=http://127.0.0.1:22011/open-rmf/rmf_demos_fm

# ── D8 修正（2026/08/16）：外層重跑不再覆蓋前一次的故障現場 ────────────
# m2_rerun.sh 在 completed < 8 時會用**同樣的 policy/round** 再呼叫本腳本，
# 此時內層的 attempt 歸 1，於是：
#   ① launch log 被 `>` 覆蓋
#   ② 結果檔被 `rm -f` 刪掉
#   ③ bridge 紀錄被 `cp` 蓋掉
# 三份證據同時消失。2026/08/13 就這樣弄丟了一次 Read timed out 風暴的完整 log，
# 該故障至今成因未明（手冊故障 F）。
#
# 修法：每次執行有自己的 RUN_ID；舊檔在被覆蓋前先搬進 attic。
#
# ⚠️ attic 刻意放在**子目錄**：m2_rerun.sh 與 m2_ra3.sh 都用
#    `/tmp/m2exp_*_r*.jsonl` 這個 glob 列出結果，舊檔若留在 /tmp 同一層，
#    會被當成正式結果一併列出，反而製造新的誤判。
RUN_ID="$(date +%m%d_%H%M%S)"
ATTIC="/tmp/m2_attic"
mkdir -p "${ATTIC}"

# 把即將被覆蓋的舊檔搬走。名字帶的是「搬走當下」的 RUN_ID，
# 因此同一組重跑多次也不會互相蓋掉。檔案不存在就什麼都不做。
stash() {
  [ -f "$1" ] || return 0
  mv "$1" "${ATTIC}/$(basename "${1%.jsonl}")_${RUN_ID}.jsonl"
  echo "[$(date +%H:%M:%S)] D8：舊檔已保留 → ${ATTIC}/$(basename "${1%.jsonl}")_${RUN_ID}.jsonl"
}

source /opt/ros/humble/setup.bash
source /root/rmf_ws/install/setup.bash

echo "[$(date +%H:%M:%S)] === 組別 ${POLICY} 輪次 ${ROUND} 開始（VDA5050 介面）==="

# 1) 徹底清場（clean.sh 會殺 lib/fifo_dispatcher/，也就順帶收掉舊的 bridge 與 vehicle）
bash /tmp/clean.sh > /dev/null 2>&1
PAT_OURS='lib/fifo_dispatcher/vda5050'
pkill -f "${PAT_OURS}" > /dev/null 2>&1
sleep 3
# SIGTERM 殺不掉就補一刀：uvicorn 若卡在優雅關閉（等 adapter 的 keep-alive
# 連線關閉），SIGTERM 是無效的。2026/08/13 就是這樣留下一個佔住 22011 的
# 孤兒 bridge，害後面兩組實驗直接中止。
pkill -9 -f "${PAT_OURS}" > /dev/null 2>&1
sleep 17      # DDS 探索狀態要時間散掉，太快啟動會讓 adapter 找不到 traffic_schedule

# 2) 斷言清乾淨（寧可不跑，也不要產生無效資料）
LEFT=$(ps -eo cmd | grep -E 'rmf_demos_fleet_adapter|ign gazebo|rmf_fleet_adapter|vda5050' | grep -v grep | wc -l)
if [ "${LEFT}" -ne 0 ]; then
  echo "[$(date +%H:%M:%S)] ✗ 清場失敗，仍有 ${LEFT} 個殘留行程，中止本組"
  ps -eo pid,cmd | grep -E 'rmf|ign gazebo|vda5050' | grep -v grep | cut -c1-70
  exit 1
fi
echo "[$(date +%H:%M:%S)] 清場完成，無殘留"

# 3) 啟動我們的 launch 並等待就緒；最多兩次
READY=0
for attempt in 1 2; do
  echo "[$(date +%H:%M:%S)] 啟動 VDA5050 場景（第 ${attempt} 次嘗試）…"
  (setsid ros2 launch fifo_dispatcher office_vda5050.launch.xml \
      > "/tmp/m2launch_${POLICY}_r${ROUND}_${RUN_ID}_a${attempt}.log" 2>&1 &)

  # 就緒判準：兩台車都回報位置且電量 100%（證明是全新場景）。
  # ⚠️ 這裡**不用** `ros2 topic echo /fleet_states --once`。
  #    2026/08/13 實測：這台機器的 ROS graph 查詢會失靈
  #    （`ros2 node list` 回 0 行、`ros2 topic info` 空白、ros2 daemon 不在），
  #    場景明明正常，就緒偵測卻永遠等不到訊號，六組會全部空轉失敗。
  #    改用 curl 問我們自己的 bridge：資料同源（車輛回報的 state），
  #    但完全不經過 ROS CLI，也順便驗證了受測介面本身活著。
  #    ⚠️ 但 curl 只證明 bridge 與 vehicle 活著，**證明不了 fleet_adapter 活著**
  #    （舊的 /fleet_states 偵測隱含涵蓋了這點，因為那個 topic 由 adapter 發布）。
  #    2026/08/13 實測：nearest r1 就是 adapter 死了而偵測沒抓到，白跑一組。
  #    故障 E：adapter 找不到 rmf_traffic_schedule → Adapter.make() 回 None
  #            → AttributeError: 'NoneType' object has no attribute 'node'
  #    所以這裡多一道行程檢查，死了就重試（重試會清場再等，正是手冊的處置）。
  for i in $(seq 1 25); do
    body=$(curl -s --max-time 4 "${FM}/status/")
    ok=$(echo "${body}" | grep -c '"success": *true')
    full=$(echo "${body}" | grep -oE '"battery": *100(\.0)?' | wc -l)
    adapter=$(pgrep -fc 'rmf_demos_fleet_adapter/fleet_adapter')
    if [ "${ok}" -ge 1 ] && [ "${full}" -ge 2 ] && [ "${adapter}" -ge 1 ]; then
      READY=1
      echo "[$(date +%H:%M:%S)] 車隊就緒（bridge 回報 ${full} 台滿電，adapter 活著）"
      break
    fi
    sleep 4
  done

  if [ "${READY}" = "1" ] && pgrep -f "ign gazebo server" > /dev/null; then
    break
  fi
  READY=0
  echo "[$(date +%H:%M:%S)] ✗ 場景未就緒或 Gazebo 已崩潰，清場後重試"
  bash /tmp/clean.sh > /dev/null 2>&1
  pkill -f 'lib/fifo_dispatcher/vda5050' > /dev/null 2>&1
  sleep 5
done

if [ "${READY}" != "1" ]; then
  echo "[$(date +%H:%M:%S)] ✗✗ 兩次嘗試皆失敗，中止本組"
  exit 1
fi

# 3b) VDA5050 鏈路健康斷言（M2 新增：這是本次實驗的受測介面，壞了就沒有意義）
VEH=$(pgrep -fc 'lib/fifo_dispatcher/vda5050_vehicle')
ADP=$(pgrep -fc 'rmf_demos_fleet_adapter/fleet_adapter')
ORD=$(timeout 6 mosquitto_sub -h localhost -t 'vda5050/v3/#' -C 2 2>/dev/null | wc -l)
if [ "${VEH}" -lt 2 ] || [ "${ORD}" -lt 2 ] || [ "${ADP}" -lt 1 ]; then
  echo "[$(date +%H:%M:%S)] ✗ 鏈路不健康（vehicle=${VEH}, adapter=${ADP}, 6 秒內 MQTT 訊息=${ORD}），中止本組"
  exit 1
fi
echo "[$(date +%H:%M:%S)] 鏈路正常（vehicle ${VEH} 個、adapter 1 個、MQTT 有訊息流）"

sleep 15   # 讓 dispatcher 先觀察一段時間，idle_since 才有意義

# 4) 跑派工器（參數與 M3b 完全相同）
# D8：前一次（失敗）的結果檔不直接刪，先搬進 attic 留作故障現場
stash "${LOG}"
stash "${BRIDGE_LOG}"
: > /tmp/vda5050_bridge.jsonl      # 清空，讓本組的 order 紀錄獨立可數
DURATION=$(python3 -c "print(int(${COUNT} * ${INTERVAL} + 150))")
timeout "${DURATION}" ros2 run fifo_dispatcher dispatcher --ros-args \
  -p policy:="${POLICY}" \
  -p count:=${COUNT} \
  -p interval_sec:=${INTERVAL} \
  -p places:="${PLACES}" \
  -p log_path:="${LOG}"

cp /tmp/vda5050_bridge.jsonl "${BRIDGE_LOG}" 2>/dev/null

echo "[$(date +%H:%M:%S)] === 組別 ${POLICY} 輪次 ${ROUND} 結束 ==="
echo "  派工紀錄 ${LOG}：$(wc -l < "${LOG}") 筆"
echo "  VDA5050 order：$(grep -c order_sent "${BRIDGE_LOG}" 2>/dev/null) 張"
echo "  車輛回報的錯誤：$(grep -c order_rejected "${BRIDGE_LOG}" 2>/dev/null) 次"
echo "  本次 launch log：/tmp/m2launch_${POLICY}_r${ROUND}_${RUN_ID}_a*.log"
