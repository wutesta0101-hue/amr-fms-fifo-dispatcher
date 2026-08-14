#!/usr/bin/env bash
# M2 步驟 5：**先跑 3 組**（rmf / fifo / nearest 各一輪），約 25 分鐘。
#
# 與 /tmp/m2_ra.sh（6 組、約 50 分鐘）的差別只有輪次：這裡固定 round=1。
# 為什麼先跑 3 組（見 notes/交接文件.md 九之一）：
#   M3b 基準的跨輪變異已知（rmf ±0.1、fifo ±0.4、nearest ±4.1），
#   3 組落在該範圍內，「未劣化」的結論即成立；失敗回饋也快一倍。
#   ⚠️ 但若 nearest 與 M3b 基準差距 > 10%，必須補跑第二輪
#      （nearest 本身跨輪變異就大：58.1 / 49.8，單輪分不出劣化與正常波動）。
#
# 用法（建議直接在 WSL 終端機執行，不要透過 PowerShell 包一層）：
#   bash /tmp/m2_ra3.sh 2>&1 | tee /tmp/m2_run3_$(date +%m%d_%H%M%S).log
#
# 注意：不可加 set -u——ROS 的 setup.bash 會引用未定義變數而中斷。

# ── 原則 0：開跑前先斷言環境是空的，不空就中止，不「順手清掉再跑」 ──────
PAT='ign gazebo|rmf_demos_fleet_adapter|rmf_fleet_adapter|lib/fifo_dispatcher'
LEFT=$(ps -eo cmd | grep -E "${PAT}" | grep -v grep | wc -l)
if [ "${LEFT}" -ne 0 ]; then
  echo "✗ 環境不乾淨（${LEFT} 個行程），中止。請先確認這些是不是你正在用的："
  ps -eo pid,etimes,cmd | grep -E "${PAT}" | grep -v grep | cut -c1-90
  exit 1
fi
if ss -ltn 2>/dev/null | grep -q ':22011'; then
  echo "✗ 22011 已被佔用（可能是上次殘留的 bridge 孤兒），中止"
  ss -ltnp | grep ':22011'
  exit 1
fi
echo "✓ 環境乾淨（無殘留行程、22011 未被佔用）"

# ── 三組實驗 ──────────────────────────────────────────────────────
for policy in rmf fifo nearest; do
  echo "###### round=1 policy=${policy} ######"
  bash /tmp/m2_rg.sh "${policy}" 1
done

# 最後一組跑完也要清場：否則場景會留在背景，下一件事（不論是誰做的）
# 都會撞上殘留的 Gazebo 與 bridge。2026/08/13 的教訓。
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

# ── 驗收條件 5：三個檔案的版本標記必須一致 ─────────────────────────
echo "###### 資料版本標記（驗收條件 5）######"
head -q -n 1 /tmp/m2exp_rmf_r1.jsonl /tmp/m2exp_fifo_r1.jsonl \
             /tmp/m2exp_nearest_r1.jsonl 2>/dev/null \
  | grep -o '"code_sha": "[a-f0-9]*"' | sort | uniq -c
echo "（上面應只有一行，且計數為 3）"

echo "###### 全部完成 ######"
ls -l /tmp/m2exp_*_r1.jsonl
echo
echo "接著跑分析：/usr/bin/python3 /tmp/m2_kpi.py"
