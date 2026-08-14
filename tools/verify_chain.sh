#!/usr/bin/env bash
# 驗證「無 Gazebo 全鏈」：curl → bridge → MQTT order → vehicle → PathRequest
#                        → 假 slotcar → /robot_state → vehicle → MQTT state → bridge
# 這條鏈是 console 的資料來源，也是演示的 Plan A。
# 不可加 set -u（ROS setup.bash 會引用未定義變數）
# 腳本自身的位置——不寫死絕對路徑，clone 到任何地方都能跑
HERE="$(cd "$(dirname "$0")" && pwd)"
CFG=/root/rmf_ws/install/rmf_demos/share/rmf_demos/config/office/tinyRobot_config.yaml
BIN=/root/rmf_ws/install/fifo_dispatcher/lib/fifo_dispatcher
FM=http://127.0.0.1:22011/open-rmf/rmf_demos_fm
LOGD=/tmp/chain
rm -rf "$LOGD"; mkdir -p "$LOGD"

# ── 原則 0：先斷言環境為空，不空即中止 ────────────────────────
PAT='ign gazebo|rmf_demos_fleet_adapter|lib/fifo_dispatcher|fake_slotcar'
LEFT=$(ps -eo cmd | grep -E "${PAT}" | grep -v grep | wc -l)
if [ "${LEFT}" -ne 0 ]; then
  echo "✗ 環境不乾淨（${LEFT} 個行程），中止"
  ps -eo pid,cmd | grep -E "${PAT}" | grep -v grep | cut -c1-90
  exit 1
fi
ss -ltn | grep -q ':22011' && { echo "✗ 22011 已被佔用，中止"; exit 1; }
echo "✓ 環境乾淨"

source /opt/ros/humble/setup.bash
source /root/rmf_ws/install/setup.bash

echo
echo "=== 1. 啟動五個行程（2 假車 + 2 VDA5050 車 + 1 bridge）==="
(setsid /usr/bin/python3 "$HERE/fake_slotcar.py" \
   --ros-args -p robot_name:=tinyRobot1 -p start_x:=10.43 -p start_y:=-5.58 > "$LOGD/slot1.log" 2>&1 &)
(setsid /usr/bin/python3 "$HERE/fake_slotcar.py" \
   --ros-args -p robot_name:=tinyRobot2 -p start_x:=6.90 -p start_y:=-2.03 > "$LOGD/slot2.log" 2>&1 &)
sleep 2
(setsid "$BIN/vda5050_vehicle" --ros-args -p serial_number:=tinyRobot1 \
   -p log_path:=$LOGD/veh1.jsonl > "$LOGD/veh1.log" 2>&1 &)
(setsid "$BIN/vda5050_vehicle" --ros-args -p serial_number:=tinyRobot2 \
   -p log_path:=$LOGD/veh2.jsonl > "$LOGD/veh2.log" 2>&1 &)
sleep 2
(setsid "$BIN/vda5050_bridge" -c "$CFG" --log_path "$LOGD/bridge.jsonl" > "$LOGD/bridge.log" 2>&1 &)

echo "=== 2. 等待就緒（curl 問 bridge）==="
READY=0
for i in $(seq 1 20); do
  body=$(curl -s --max-time 3 "${FM}/status/")
  ok=$(echo "$body" | grep -c '"success": *true')
  n=$(echo "$body" | grep -o '"robot_name"' | wc -l)
  if [ "$ok" -ge 1 ] && [ "$n" -ge 2 ]; then READY=1; echo "  就緒（${i}次輪詢，約 $((i))秒）"; break; fi
  sleep 1
done
if [ "$READY" != "1" ]; then
  echo "✗ 未就緒。bridge log："; tail -15 "$LOGD/bridge.log"
  echo "--- veh1 ---"; tail -10 "$LOGD/veh1.log"
else
  echo "  車況："; curl -s "${FM}/status/" | /usr/bin/python3 -m json.tool | head -25
fi

echo
echo "=== 3. 側錄 MQTT（背景 8 秒）==="
(timeout 8 mosquitto_sub -h localhost -t 'vda5050/v3/#' -v > "$LOGD/mqtt.txt" 2>&1 &)
sleep 1

echo "=== 4. 下一個 navigate 指令（cmd_id=1，往北 3 公尺）==="
curl -s -X POST "${FM}/navigate/?robot_name=tinyRobot1&cmd_id=1" \
  -H 'Content-Type: application/json' \
  -d '{"map_name":"L1","destination":{"x":10.43,"y":-2.58,"yaw":1.57}}' \
  | /usr/bin/python3 -m json.tool

echo
echo "=== 5. 追蹤抵達（每秒問一次 last_completed_request）==="
for i in $(seq 1 15); do
  s=$(curl -s --max-time 3 "${FM}/status/?robot_name=tinyRobot1")
  echo "$s" | /usr/bin/python3 -c "
import json,sys
d=json.load(sys.stdin).get('data',{})
p=d.get('position',{})
da=d.get('destination_arrival')
print(f\"  t+$i  x={p.get('x',0):.2f} y={p.get('y',0):.2f}  剩餘={da and round(da['duration'],1)}  已完成cmd={d.get('last_completed_request')}\")
" 2>/dev/null
  echo "$s" | grep -q '"last_completed_request": 1' && { echo "  ✅ cmd 1 完成"; break; }
  sleep 1
done

echo
echo "=== 6. 證據 ==="
echo "--- bridge JSONL ---"; cat "$LOGD/bridge.jsonl"
echo "--- vehicle1 JSONL（事件）---"; grep -oE '"event": "[a-z_]*"' "$LOGD/veh1.jsonl" | sort | uniq -c
echo "--- MQTT 側錄的 topic ---"; cut -d' ' -f1 "$LOGD/mqtt.txt" | sort | uniq -c
echo "--- 假 slotcar 有沒有抱怨時間基準 ---"; grep -cE "指令的時間基準錯了" "$LOGD/slot1.log"
grep -E "收到路徑|抵達" "$LOGD/slot1.log" | tail -3

echo
echo "=== 7. 清理（原則 15：SIGTERM 後 3 秒內要消失）==="
pkill -f 'lib/fifo_dispatcher/vda5050'
pkill -f 'fake_slotcar.py'
sleep 3
REST=$(ps -eo cmd | grep -E 'lib/fifo_dispatcher|fake_slotcar' | grep -v grep | wc -l)
echo "殘留行程：${REST}（應為 0）"
if ss -ltn | grep -q ':22011'; then echo "❌ 22011 仍被佔用"; else echo "✅ 22011 已釋放"; fi
