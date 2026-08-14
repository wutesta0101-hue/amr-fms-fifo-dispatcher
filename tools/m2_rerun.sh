#!/usr/bin/env bash
# 補跑指定的幾組實驗（用於某組因故障 E 等原因作廢時）。
#
# 用法：bash /tmp/m2_rerun.sh fifo:1 rmf:2
#       參數是「策略:輪次」，可給任意組數。
#
# 與 m2_ra3.sh 的差別：
#   ① 只跑指定的組
#   ② **失敗自動重試一次**——2026/08/13 實測，fleet_adapter 會在就緒檢查通過之後
#      才死掉（故障 E），m2_rg.sh 的重試只涵蓋啟動階段，跑到一半死掉不會重跑。
#      判準用結果檔的 completed 筆數，不用行程狀態：資料才是我們要的東西。
#
# 注意：不可加 set -u——ROS 的 setup.bash 會引用未定義變數而中斷。

# ── 原則 0：開跑前斷言環境是空的 ──────────────────────────────────
PAT='ign gazebo|rmf_demos_fleet_adapter|rmf_fleet_adapter|lib/fifo_dispatcher'
LEFT=$(ps -eo cmd | grep -E "${PAT}" | grep -v grep | wc -l)
if [ "${LEFT}" -ne 0 ]; then
  echo "✗ 環境不乾淨（${LEFT} 個行程），中止。請先確認這些是不是你正在用的："
  ps -eo pid,etimes,cmd | grep -E "${PAT}" | grep -v grep | cut -c1-90
  exit 1
fi
if ss -ltn 2>/dev/null | grep -q ':22011'; then
  echo "✗ 22011 已被佔用（可能是上次殘留的 bridge 孤兒），中止"
  exit 1
fi
echo "✓ 環境乾淨（無殘留行程、22011 未被佔用）"

# 這一組的結果檔有幾筆 completed（版本標記那行沒有 status，不會被算進去）
count_done() {
  grep -c '"status": "completed"' "/tmp/m2exp_${1}_r${2}.jsonl" 2>/dev/null || echo 0
}

for spec in "$@"; do
  policy="${spec%%:*}"
  round="${spec##*:}"
  for attempt in 1 2; do
    echo "###### policy=${policy} round=${round}（第 ${attempt} 次）######"
    bash /tmp/m2_rg.sh "${policy}" "${round}"
    DONE=$(count_done "${policy}" "${round}")
    if [ "${DONE}" -ge 8 ]; then
      echo "✓ ${policy} r${round}：${DONE}/8 完成"
      break
    fi
    echo "✗ ${policy} r${round}：只有 ${DONE}/8 完成（多半是 fleet_adapter 中途死亡，故障 E）"
    if [ "${attempt}" = "2" ]; then
      echo "✗✗ 重試後仍失敗，放棄本組；請查 /tmp/m2launch_${policy}_r${round}_a*.log"
    else
      echo "→ 重試一次"
    fi
  done
done

# 收尾清場（最後一組跑完也要清，否則場景留在背景）
echo "###### 收尾清場 ######"
bash /tmp/clean.sh > /dev/null 2>&1
PAT_OURS='lib/fifo_dispatcher/vda5050'
pkill -f "${PAT_OURS}" > /dev/null 2>&1
sleep 3
pkill -9 -f "${PAT_OURS}" > /dev/null 2>&1
sleep 2
LEFT=$(ps -eo cmd | grep -E 'ign gazebo|rmf_demos_fleet_adapter|vda5050' | grep -v grep | wc -l)
echo "殘留行程：${LEFT}（應為 0）"
ss -ltn 2>/dev/null | grep -q ':22011' && echo "⚠️ 22011 仍被佔用" || echo "22011 已釋放"

echo "###### 各組結果與版本標記 ######"
for f in /tmp/m2exp_*_r*.jsonl; do
  printf '%-32s completed=%-3s %s\n' "$(basename "$f")" \
    "$(grep -c '"status": "completed"' "$f")" \
    "$(head -1 "$f" | grep -o '"code_sha": "[a-f0-9]*"')"
done

echo
echo "接著跑分析：/usr/bin/python3 /tmp/m2_kpi.py"
