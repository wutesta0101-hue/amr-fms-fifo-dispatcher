#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
# 量測 VDA5050 鏈路自己引入了多少延遲。
# 資料來源是兩端各自的 JSON Lines（都帶牆鐘 ts，可直接相減）：
#   bridge : order_sent / cmd_completed
#   vehicle: order_received / node_reached
#
# ⚠️ 每組實驗的 cmd_id 都從 1 重新編號，因此不能只用 (車, cmd_id) 當鍵——
#    六組會互相撞號。改成「同車、同 orderId、且時間最接近」的就近配對，
#    並限制配對窗（120 秒）避免跨組誤配。
import glob
import json
import os
import statistics as st

WINDOW = 120.0     # 秒；超過就視為不同組的同號事件


def rows(pattern):
    out = []
    for path in glob.glob(pattern):
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def index(events, key_fn):
    idx = {}
    for e in events:
        idx.setdefault(key_fn(e), []).append(e['ts'])
    for v in idx.values():
        v.sort()
    return idx


def match(ts, candidates):
    """取時間上最接近、且不早於 ts−1 秒的那一筆"""
    best = None
    for c in candidates:
        if c < ts - 1.0 or c > ts + WINDOW:
            continue
        if best is None or c < best:
            best = c
    return best


BRIDGE_GLOB = os.environ.get('M2_BRIDGE_GLOB', '/tmp/m2bridge_*.jsonl')
VEHICLE_GLOB = os.environ.get('M2_VEHICLE_GLOB', '/tmp/vda5050_tinyRobot*.jsonl')
TASKS_PER_RUN = 8      # 每組實驗派 8 張任務（dispatcher 的 count 參數）


# 把兩端的紀錄配對成一段一段的延遲，回傳 (下行秒數清單, 上行秒數清單)。
# 抽成函式供 experiments/kpi_report.py 共用——配對規則只寫這一份（原則 16）。
def measure(bridge_glob=None, vehicle_glob=None):
    bridge = rows(bridge_glob or BRIDGE_GLOB)
    vehicle = rows(vehicle_glob or VEHICLE_GLOB)

    recv_idx = index([e for e in vehicle if e.get('event') == 'order_received'],
                     lambda e: (e['serial'], str(e['orderId'])))
    reach_idx = index([e for e in vehicle if e.get('event') == 'node_reached'],
                      lambda e: (e['serial'], str(e['orderId'])))
    comp_idx = index([e for e in bridge if e.get('event') == 'cmd_completed'],
                     lambda e: (e['robot'], str(e['cmd_id'])))

    down, up = [], []
    for e in bridge:
        if e.get('event') != 'order_sent':
            continue
        key = (e['robot'], str(e['cmd_id']))
        got = match(e['ts'], recv_idx.get(key, []))
        if got is not None:
            down.append(got - e['ts'])
            arrived = match(got, reach_idx.get(key, []))
            if arrived is not None:
                done = match(arrived, comp_idx.get(key, []))
                if done is not None:
                    up.append(done - arrived)
    return down, up


# 每組實驗發出幾張 order。除以 TASKS_PER_RUN 就是「一個任務幾段」——
# 這個數字原本是寫死的 10–13，現在改成從資料算，避免用推測填補（原則 7）。
def order_counts(bridge_glob=None):
    out = {}
    for path in sorted(glob.glob(bridge_glob or BRIDGE_GLOB)):
        out[os.path.basename(path).split('m2bridge_')[-1]] = sum(
            1 for l in open(path, encoding='utf-8')
            if l.strip() and json.loads(l).get('event') == 'order_sent')
    return out


def show(name, xs):
    if not xs:
        print(f'{name}: 無資料')
        return
    xs = sorted(xs)
    p90 = xs[int(len(xs) * 0.9)]
    print(f'{name}\n    n={len(xs)}  平均 {st.mean(xs):.3f}s  中位數 {st.median(xs):.3f}s'
          f'  P90 {p90:.3f}s  最大 {xs[-1]:.3f}s')


def main():
    down, up = measure()
    counts = order_counts()

    print('=== VDA5050 鏈路的內建延遲 ===')
    show('下行 bridge→vehicle（order 發出 → 車輛處理）', down)
    show('上行 vehicle→bridge（車輛判定抵達 → bridge 認定完成）', up)
    if down and up and counts:
        per_leg = st.mean(down) + st.mean(up)
        segs = sorted(n / TASKS_PER_RUN for n in counts.values())
        print(f'\n每一段路徑合計拖慢約 {per_leg:.2f}s')
        print(f'（每個任務實測 {segs[0]:.1f}–{segs[-1]:.1f} 段 → 單筆任務多出 '
              f'{per_leg * segs[0]:.1f}–{per_leg * segs[-1]:.1f}s）')

    print('\n=== 每組的 order 段數 ===')
    for name, n in counts.items():
        print(f'  {name:<20} {n} 張 order'
              f'（{n / TASKS_PER_RUN:.1f} 段/任務）')


if __name__ == '__main__':
    main()
