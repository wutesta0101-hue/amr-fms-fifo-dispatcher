#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
# M2 步驟 5 的驗收分析：換掉廠商介面之後，KPI 有沒有劣化？
#
# 對照的是 M3b 的六組資料（notes/data/exp_*.jsonl，走 rmf_demos 的 fleet_manager）
# 與本次的六組（/tmp/m2exp_*.jsonl，走我們的 vda5050_bridge + vda5050_vehicle）。
#
# 判準（沿用 M3b 的方法論）：
#   差異要能被宣稱，必須大於**同一策略跨輪的變異**；否則落在雜訊內，不可下結論。
#
# 2026/08/13 起：紀錄檔第一行是版本標記（run_started），本腳本會抽掉它再統計，
# 並在表格與「資料可比性」一節顯示 code_sha——這是驗收條件 5。
# 原始版本在 /tmp（重開機會消失），本檔是收進 repo 的正本。
import json
import os
import statistics as st

POLICIES = ['rmf', 'fifo', 'nearest']
ROUNDS = [1, 2]
# 基準組（M3b，走原生 fleet_manager）的資料位置。
# 預設為 repo 的 notes/data——該目錄是個人工作紀錄，不進 git；
# 資料放在別處時用環境變數覆蓋：
#   M3B_DATA_DIR=/path/to/data /usr/bin/python3 tools/m2_kpi.py
OLD_DIR = os.environ.get(
    'M3B_DATA_DIR',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'notes', 'data'))
NEW_DIR = os.environ.get('M2_DATA_DIR', '/tmp')


def load(path):
    if not os.path.isfile(path):
        return None
    rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
    # 版本標記不是任務，先抽掉——留著的話 n 會多 1，「8/8 完成」會顯示成「8/9」，
    # 看起來像有一筆任務不見了（驗收條件 1 會被誤判為未通過）
    meta = next((r for r in rows if r.get('event') == 'run_started'), None)
    rows = [r for r in rows if r.get('event') != 'run_started']
    sha = meta.get('code_sha') if meta else None
    done = [r for r in rows if r.get('status') == 'completed']
    if not done:
        return {'n': len(rows), 'done': 0, 'sha': sha}
    turn = [r['turnaround_sec'] for r in done]
    wait = [r['wait_sec'] for r in done]
    bal = {}
    for r in done:
        bal[r['robot']] = bal.get(r['robot'], 0) + 1
    return {
        'n': len(rows), 'done': len(done), 'sha': sha,
        'turn_mean': st.mean(turn), 'turn_max': max(turn),
        'wait_mean': st.mean(wait), 'wait_max': max(wait),
        'wait_sd': st.pstdev(wait),
        'makespan': (max(r['t_done'] for r in done)
                     - min(r['t_created'] for r in done)),
        'bal': ':'.join(str(v) for v in sorted(bal.values(), reverse=True)),
    }


def table(title, data):
    print(f'\n=== {title} ===')
    print(f"{'策略':<9}{'輪':>3}{'完成':>7}{'平均周轉':>10}{'最大周轉':>10}"
          f"{'平均等待':>10}{'最大等待':>10}{'等待SD':>9}{'總時間':>9}  分配"
          f"      版本")
    print('-' * 110)
    for p in POLICIES:
        for r in ROUNDS:
            d = data.get((p, r))
            if d is None:
                print(f'{p:<9}{r:>3}   （缺資料）')
                continue
            if not d.get('done'):
                print(f"{p:<9}{r:>3}{d['done']:>4}/{d['n']:<3}   （無完成任務）")
                continue
            print(f"{p:<9}{r:>3}{d['done']:>4}/{d['n']:<3}"
                  f"{d['turn_mean']:>10.1f}{d['turn_max']:>10.1f}"
                  f"{d['wait_mean']:>10.1f}{d['wait_max']:>10.1f}"
                  f"{d['wait_sd']:>9.1f}{d['makespan']:>9.1f}  {d['bal']:<8}"
                  f"  {d.get('sha') or '—（無標記）'}")


def mean_of(data, policy, key):
    vals = [data[(policy, r)][key] for r in ROUNDS
            if data.get((policy, r)) and data[(policy, r)].get('done')]
    return st.mean(vals) if vals else None


def spread_of(data, policy, key):
    """同一策略跨輪的差距——這就是雜訊底線"""
    vals = [data[(policy, r)][key] for r in ROUNDS
            if data.get((policy, r)) and data[(policy, r)].get('done')]
    return (max(vals) - min(vals)) if len(vals) > 1 else None


# 驗收條件 5：本批資料是否來自同一版程式碼。
# 沒有這一關，上一輪「三個世代的資料混在一起比較」的事就會再發生一次。
def comparability(data):
    print('\n=== 資料可比性：版本標記（驗收條件 5）===')
    present = {(p, r): d.get('sha') for (p, r), d in data.items() if d}
    if not present:
        print('  無資料')
        return
    missing = sorted(f'{p}_r{r}' for (p, r), s in present.items() if not s)
    uniq = sorted({s for s in present.values() if s})
    for s in uniq:
        who = sorted(f'{p}_r{r}' for (p, r), v in present.items() if v == s)
        print(f'  {s}  ←  {", ".join(who)}')
    if missing:
        print(f'  ⚠️ 無版本標記（2026/08/13 之前的舊資料）：{", ".join(missing)}')
    if len(uniq) > 1:
        print('  ❌ 版本不一致——這批資料**不可合併比較**，請確認是否有元件跑的是舊 build')
    elif not missing:
        print('  ✅ 全部同一版，可合併比較')


# 只留「版本標記與多數相同」的資料進入比較。
# 光是印出警告不夠——上一輪就是明明知道資料有新有舊，仍然一起平均下去。
# 沒有標記的舊資料一律排除：不能證明可比，就不拿來下結論（原則 7）。
def comparable_only(data):
    shas = [d['sha'] for d in data.values() if d and d.get('sha')]
    if not shas:
        return data, None, []
    ref = max(set(shas), key=shas.count)
    dropped = sorted(f'{p}_r{r}' for (p, r), d in data.items()
                     if d and d.get('sha') != ref)
    kept = {k: (d if (d and d.get('sha') == ref) else None)
            for k, d in data.items()}
    return kept, ref, dropped


old = {(p, r): load(f'{OLD_DIR}/exp_{p}_r{r}.jsonl')
       for p in POLICIES for r in ROUNDS}
new = {(p, r): load(f'{NEW_DIR}/m2exp_{p}_r{r}.jsonl')
       for p in POLICIES for r in ROUNDS}

table('M3b 基準：rmf_demos 的 fleet_manager', old)
table('M2 重跑：我們的 vda5050_bridge + vda5050_vehicle', new)
comparability(new)

new_cmp, ref_sha, dropped = comparable_only(new)

print('\n=== 換掉介面之後，KPI 有沒有劣化？ ===')
print(f'只採用版本 {ref_sha} 的資料'
      + (f'；已排除 {", ".join(dropped)}（版本不符或無標記）' if dropped else ''))
print('判準：差距必須大於「兩批各自的跨輪變異之和」，才算真的有差別')
print(f"{'指標':<12}{'策略':<9}{'M3b':>9}{'M2':>9}{'差距':>9}"
      f"{'雜訊底線':>10}  判定")
print('-' * 70)
verdicts = []
for key, label in [('turn_mean', '平均周轉'), ('turn_max', '最大周轉'),
                   ('wait_mean', '平均等待'), ('wait_max', '最大等待')]:
    for p in POLICIES:
        o, n = mean_of(old, p, key), mean_of(new_cmp, p, key)
        if o is None or n is None:
            print(f'{label:<12}{p:<9}   （缺可比的資料，無法比較）')
            continue
        so = spread_of(old, p, key) or 0.0
        sn = spread_of(new_cmp, p, key)
        # M2 只有單輪時，雜訊底線只能取 M3b 的跨輪變異——
        # 這正是「先跑 3 組」的前提（M3b 變異已知），但結論要標明是單輪
        single = sn is None
        noise = so + (sn or 0.0)
        diff = n - o
        if abs(diff) <= noise:
            verdict = '雜訊內，未劣化'
        elif diff > 0:
            verdict = f'⚠️ 變差 {diff:.1f}s（超出雜訊）'
        else:
            verdict = f'變好 {-diff:.1f}s（超出雜訊）'
        verdicts.append((label, p, verdict))
        print(f'{label:<12}{p:<9}{o:>9.1f}{n:>9.1f}{diff:>+9.1f}'
              f'{noise:>10.1f}  {verdict}{"（單輪）" if single else ""}')

print('\n=== VDA5050 側的統計（本次特有）===')
for p in POLICIES:
    for r in ROUNDS:
        path = f'/tmp/m2bridge_{p}_r{r}.jsonl'
        if not os.path.isfile(path):
            continue
        rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
        sent = sum(1 for x in rows if x.get('event') == 'order_sent')
        done = sum(1 for x in rows if x.get('event') == 'cmd_completed')
        rej = sum(1 for x in rows if x.get('event') == 'order_rejected')
        print(f'  {p:<9}r{r}  order 發出 {sent:>4}｜完成 {done:>4}｜被拒 {rej:>3}')

bad = [v for v in verdicts if v[2].startswith('⚠️')]
print(f'\n總結：{len(bad)} 項超出雜訊變差' if bad else '\n總結：所有指標都在雜訊內，換掉介面未造成劣化')
for label, p, v in bad:
    print(f'  - {label} / {p}：{v}')
