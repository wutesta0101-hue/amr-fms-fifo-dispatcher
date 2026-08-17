#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
# 三種派工策略的對照表與統計圖：自寫的 FIFO／nearest，跟 Open-RMF 自己投標選車比。
#
# 這支跟 `kpi_report.py` 問的是**不同的問題**，兩者常被混在一起：
#   kpi_report.py  ── 介面軸：同一個策略，換掉車端介面（原生 fleet_manager → VDA5050），
#                     Open-RMF 在兩邊都在，沒被換掉。問「會不會變慢」。
#   policy_report.py ── 策略軸：同一條鏈路，換派工策略。`rmf` 這一組就是
#                     讓 Open-RMF 自己投標選車，這裡才是「跟 Open-RMF 比」。
#
# 資料用 M3b 那六組（3 策略 × 2 輪，走原生 fleet_manager）——策略軸只有這批
# 每個策略都有兩輪，算得出雜訊底線。M2 那批 fifo／nearest 只有單輪，不夠。
#
# 執行：
#   PYTHONIOENCODING=utf-8 python experiments/policy_report.py
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

M3B_DIR = os.environ.get('M3B_DATA_DIR', os.path.join(ROOT, 'notes', 'data'))
os.environ.setdefault('M3B_DATA_DIR', M3B_DIR)

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
FIG_DIR = os.path.join(OUT_DIR, 'figs')
OUT_MD = os.path.join(OUT_DIR, 'M3b-策略對照.md')

import matplotlib                                            # noqa: E402
matplotlib.use('Agg')
import matplotlib.pyplot as plt                              # noqa: E402

import figstyle as fs                                        # noqa: E402
import m2_kpi                                                # noqa: E402
# 表格語法與 kpi_report 共用，避免兩份 md 產生器各自演化
from kpi_report import md_table                              # noqa: E402

POLICIES = ['rmf', 'fifo', 'nearest']
LABEL = {'rmf': 'rmf（Open-RMF 自己投標）',
         'fifo': 'fifo（自寫：閒置最久）',
         'nearest': 'nearest（自寫：距離最近）'}
# Open-RMF 那一組上色，自寫的兩組留灰——這張圖要問的是「自寫的跟它差多少」，
# 基準才是需要一眼認出來的東西（灰階承載結構，彩色只給訊號）
COLOR = {'rmf': fs.ACC, 'fifo': fs.INK, 'nearest': fs.MUT}
EDGES = {'rmf': 'none', 'fifo': 'none', 'nearest': 'none'}
PAIRS = [('fifo', 'rmf'), ('nearest', 'rmf'), ('nearest', 'fifo')]


# 同一策略跨輪的平均與變異。變異就是雜訊底線的來源。
def agg(data, policy, key):
    vals = [data[(policy, r)][key] for r in m2_kpi.ROUNDS
            if data.get((policy, r)) and data[(policy, r)].get('done')]
    return (st.mean(vals), (max(vals) - min(vals)) if len(vals) > 1 else None,
            len(vals))


# 兩個策略之間的差，判準與介面軸一致：差距要大於兩者跨輪變異之和才算數。
def compare_pairs(data):
    out = []
    for key, label in m2_kpi.METRICS:
        for a, b in PAIRS:
            ma, sa, na = agg(data, a, key)
            mb, sb, nb = agg(data, b, key)
            noise = (sa or 0.0) + (sb or 0.0)
            diff = ma - mb
            single = sa is None or sb is None
            if abs(diff) <= noise:
                verdict = '雜訊內，分不出'
            else:
                verdict = (f'{a} 較差 {diff:.1f}s' if diff > 0
                           else f'{a} 較好 {-diff:.1f}s')
            out.append({'key': key, 'label': label, 'a': a, 'b': b,
                        'ma': ma, 'mb': mb, 'diff': diff, 'noise': noise,
                        'single': single, 'verdict': verdict})
    return out


# 圖一：四項指標的絕對值，誤差線是跨輪的最小～最大（不是標準差——只有兩輪，
# 畫標準差會讓人以為樣本比實際多）
def fig_levels(data, path):
    fig, axes = plt.subplots(1, 4, figsize=(9.8, 3.2))
    for ax, (key, label) in zip(axes, m2_kpi.METRICS):
        for i, p in enumerate(POLICIES):
            vals = [data[(p, r)][key] for r in m2_kpi.ROUNDS
                    if data.get((p, r)) and data[(p, r)].get('done')]
            m = st.mean(vals)
            ax.bar(i, m, width=.62, color=COLOR[p], zorder=2, lw=.6,
                   edgecolor=EDGES[p])
            if len(vals) > 1:
                ax.errorbar(i, m, yerr=[[m - min(vals)], [max(vals) - m]],
                            fmt='none', ecolor=fs.INK, elinewidth=.9,
                            capsize=3, zorder=3)
            ax.text(i, max(vals), f'{m:.0f}', ha='center', va='bottom',
                    fontsize=7, color=fs.INK)
        ax.set_xticks(range(len(POLICIES)))
        ax.set_xticklabels(POLICIES, fontsize=7.5)
        ax.set_ylim(0, max(st.mean([data[(p, r)][key] for r in m2_kpi.ROUNDS])
                           for p in POLICIES) * 1.42)
        fs.title(ax, f'{label}（秒）', size=8.5)
    fig.tight_layout(rect=(0, 0.09, 1, 1))
    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=COLOR[p], lw=.6,
                             edgecolor=EDGES[p]) for p in POLICIES]
    fig.legend(handles, [LABEL[p] for p in POLICIES], fontsize=7,
               loc='lower center', ncol=3, bbox_to_anchor=(0.5, 0.008),
               alignment='left')
    fig.text(0.012, 0.012, '誤差線＝兩輪的最小～最大', fontsize=6.5,
             color=fs.MUT)
    fig.savefig(path, dpi=200)
    plt.close(fig)


# 圖二：每筆任務的分布。平均與最大值講不出「尾端換平均」——那是形狀的事。
# 同色編策略、線型編指標（周轉實線／等待虛線），兩個維度不搶。
def fig_dist(data, path):
    fig, (a, b) = plt.subplots(1, 2, figsize=(8.8, 3.3))
    for ax, key, name in ((a, 'turns', '周轉時間'), (b, 'waits', '等待時間')):
        for p in POLICIES:
            xs = sorted(v for r in m2_kpi.ROUNDS
                        if data.get((p, r)) and data[(p, r)].get('done')
                        for v in data[(p, r)][key])
            ys = [(i + 1) / len(xs) for i in range(len(xs))]
            ax.step(xs, ys, where='post', lw=1.7, color=COLOR[p],
                    label=f'{p}  n={len(xs)}  中位數 {st.median(xs):.0f}s')
            ax.plot(xs[-1], 1.0, 'o', ms=3.5, color=COLOR[p], zorder=3)
        ax.set_xlabel(f'{name}（秒）')
        ax.set_ylim(0, 1.06)
    a.legend(fontsize=6.8, loc='upper left')      # 左圖的曲線都壓在右下
    b.legend(fontsize=6.8, loc='lower right')     # 右圖的曲線都擠在左上
    a.set_ylabel('累積比例')
    fs.title(a, '每筆任務的周轉時間分布',
             '圓點＝該策略最慢的一筆；線越靠左上越好', pad=16)
    fs.title(b, '每筆任務的等待時間分布',
             '三者都有長尾，但 fifo 的中段整體右移——它讓多數任務都多等', pad=16)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


# 圖三：兩兩相減，跟介面軸那張圖同一套語彙——條子伸出灰帶才算真的分得出來。
def fig_pairs(rows, path):
    fig, ax = plt.subplots(figsize=(6.9, 4.8))
    y = list(range(len(rows)))[::-1]
    for yi, c in zip(y, rows):
        ax.barh(yi, 2 * c['noise'], left=-c['noise'], height=.78,
                color=fs.LT, zorder=0, lw=0)
        inside = abs(c['diff']) <= c['noise']
        # 差距為負＝前者比較快。灰＝分不出，青＝前者較好，琥珀＝前者較差
        color = fs.MUT if inside else (fs.HOT if c['diff'] > 0 else fs.ACC)
        ax.barh(yi, c['diff'], height=.5, color=color, zorder=2, lw=0)
        ax.text(c['diff'] + (1.4 if c['diff'] >= 0 else -1.4), yi,
                f"{c['diff']:+.1f}s", va='center',
                ha='left' if c['diff'] >= 0 else 'right',
                fontsize=7, color=fs.INK)
    ax.axvline(0, color=fs.EDGE, lw=.9, zorder=3)
    ax.set_yticks(y)
    ax.set_yticklabels([f"{c['label']}　{c['a']} 減 {c['b']}" for c in rows],
                       fontsize=7.5)
    # 左右各留白，否則最長那條的數值標籤會撞到 y 軸標籤
    lo = min(min(c['diff'], -c['noise']) for c in rows)
    hi = max(max(c['diff'], c['noise']) for c in rows)
    ax.set_xlim(lo - 14, hi + 12)
    ax.set_xlabel('兩策略的差（秒）；負值＝前者比較快')
    fs.title(ax, '三種策略兩兩相比，差多少',
             '灰帶＝雜訊底線（兩者跨輪變異之和）；落在帶內就是分不出高下', pad=16)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c)
               for c in (fs.MUT, fs.ACC, fs.HOT, fs.LT)]
    fig.tight_layout(rect=(0, 0.075, 1, 1))
    fig.legend(handles, ['雜訊內，分不出', '前者較好（超出雜訊）',
                         '前者較差（超出雜訊）', '灰帶｜雜訊底線 ±'],
               fontsize=6.8, loc='lower center', ncol=2,
               bbox_to_anchor=(0.5, 0.008), columnspacing=2.2,
               handlelength=1.8, alignment='left')
    fig.savefig(path, dpi=200)
    plt.close(fig)


def main():
    data, _ = m2_kpi.load_all()
    if not any(v for v in data.values()):
        sys.exit(f'找不到 M3b 資料：{M3B_DIR}\n用 M3B_DATA_DIR 指定 jsonl 位置。')
    rows = compare_pairs(data)

    fs.apply()
    os.makedirs(FIG_DIR, exist_ok=True)
    fig_levels(data, os.path.join(FIG_DIR, 'policy_levels.png'))
    fig_dist(data, os.path.join(FIG_DIR, 'policy_dist.png'))
    fig_pairs(rows, os.path.join(FIG_DIR, 'policy_pairs.png'))

    runs = [(p, r) for p in POLICIES for r in m2_kpi.ROUNDS
            if data.get((p, r)) and data[(p, r)].get('done')]
    n_tasks = sum(data[k]['done'] for k in runs)
    inside = sum(1 for c in rows if c['verdict'].startswith('雜訊內'))

    def cell(c):
        return c['verdict'] + ('（單輪）' if c['single'] else '')

    tail = {p: agg(data, p, 'turn_max')[0] for p in POLICIES}
    mean = {p: agg(data, p, 'turn_mean')[0] for p in POLICIES}
    wait = {p: agg(data, p, 'wait_mean')[0] for p in POLICIES}

    doc = f'''# 三種派工策略的對照：自寫的 FIFO／nearest vs Open-RMF 自己投標

> **本檔由 `experiments/policy_report.py` 從原始 JSON Lines 產生，請勿手動編輯。**
>
> 資料：M3b 六組（3 策略 × 2 輪，走 `rmf_demos` 原生 `fleet_manager`），
> 共 {n_tasks} 筆任務、每組 {n_tasks // len(runs)} 張。
> 這批**每個策略都有兩輪**，算得出雜訊底線；M2 那批 fifo／nearest 只有單輪，不適合做策略軸。

---

## 這張表回答的問題，跟〈M2 KPI 對照〉不一樣

| | 介面軸（[M2-KPI對照.md](M2-KPI對照.md)） | 策略軸（本檔） |
|---|---|---|
| 換掉什麼 | 車端介面：原生 `fleet_manager` → 自寫 VDA5050 橋接 | 派工策略：誰來決定「這張單給哪台車」 |
| Open-RMF | **兩邊都在，沒被換掉**（dispatcher、fleet_adapter、交通協商都是 RMF） | `rmf` 這一組就是**讓 RMF 自己投標選車** |
| 問題 | 換了會不會變慢 | 自寫的派工器跟 RMF 自己派，差在哪 |

三個策略在紀錄裡的決策理由（`reason` 欄）長這樣：

| 策略 | 誰決定 | `reason` 實例 |
|---|---|---|
| `rmf` | **Open-RMF 的投標機制** | `RMF 自行投標選車` |
| `fifo` | 自寫：挑閒置最久的 | `閒置最久（tinyRobot1=23.9s）` |
| `nearest` | 自寫：挑距離最近的 | `距離最近（tinyRobot1=1.44m）` |

---

## 一、四項指標

![三策略的四項指標](figs/policy_levels.png)

{md_table(['指標'] + [LABEL[p] for p in POLICIES],
          [[label] + [f'{agg(data, p, key)[0]:.1f}'
                      + (f'　±{agg(data, p, key)[1] / 2:.1f}'
                         if agg(data, p, key)[1] is not None else '')
                      for p in POLICIES]
           for key, label in m2_kpi.METRICS])}

（± 是兩輪最小～最大的一半，不是標準差——只有兩輪，寫標準差會讓人高估樣本量。）

---

## 二、兩兩相比：哪些差距真的存在

![三策略兩兩相比](figs/policy_pairs.png)

判準與介面軸一致：**差距要大於兩者跨輪變異之和才算數**。
{len(rows)} 項比較中 {inside} 項落在雜訊內，分不出高下。

{md_table(['指標', '比較', '前者', '後者', '差距', '雜訊底線', '判定'],
          [[c['label'], f"{c['a']} 減 {c['b']}", f"{c['ma']:.1f}",
            f"{c['mb']:.1f}", f"{c['diff']:+.1f}", f"{c['noise']:.1f}",
            cell(c)] for c in rows])}

---

## 三、分布：平均值講不出來的部分

![每筆任務的周轉與等待分布](figs/policy_dist.png)

---

## 四、看得出什麼

**1. `nearest` 全面不輸 `rmf`，尾端明顯較好。**
平均周轉 {mean['nearest']:.1f}s vs {mean['rmf']:.1f}s（落在雜訊內，分不出），
但最大周轉 {tail['nearest']:.1f}s vs {tail['rmf']:.1f}s、平均等待
{wait['nearest']:.1f}s vs {wait['rmf']:.1f}s，都是超出雜訊的差距。
**在這個場景、這個規模下，一個十幾行的「挑最近的」贏過 RMF 的投標機制。**

**2. `fifo` 慢，而且是設計上的慢。** 平均周轉 {mean['fifo']:.1f}s，
比另外兩者都高。它刻意不看距離，只看誰閒得久——換來的是公平性與
決策時間有界（不需要跑成本函式），代價就在這張表上。

**3. RMF 的取捨是「尾端換平均」。** 它的平均周轉跟 `nearest` 分不出高下，
但最大周轉 {tail['rmf']:.1f}s 是三者中最差。分布圖看得最清楚：
`rmf` 的曲線在中段很好，右端拖出一條長尾。

---

## 五、這批資料不能拿來宣稱的事

| # | 限制 | 影響 |
|---|---|---|
| 1 | 每策略只有兩輪、每輪 8 張任務、2 台車 | 雜訊底線是「兩輪的差」，不是統計意義上的信賴區間；樣本小 |
| 2 | 只有 office 一個場景 | 「nearest 贏」很可能是這個場域尺度（15.6×9.3m、兩台車）的結果，換到大場域、多車、有交通壅塞時 RMF 的協商價值才會顯現 |
| 3 | 任務型態單一（patrol，無取放、無充電） | RMF 的排程優勢（電量、充電站、多階段任務）完全沒被測到 |
| 4 | `rmf` 組的決策在 RMF 內部 | 我們只看得到結果，看不到它的成本函式怎麼算——不能解釋「為什麼」它選了那台 |

> 第 2、3 點特別重要：**這張表不是「自寫派工器優於 Open-RMF」的證據**，
> 只是「在這個小場景的巡邏任務上，簡單策略就夠用」。

---

## 六、重現

```bash
PYTHONIOENCODING=utf-8 python experiments/policy_report.py
```

資料在別處時：`M3B_DATA_DIR=<M3b jsonl 目錄> python experiments/policy_report.py`
'''
    with open(OUT_MD, 'w', encoding='utf-8', newline='\n') as f:
        f.write(doc)
    print(f'已產生 {OUT_MD}')
    for n in ('policy_levels.png', 'policy_dist.png', 'policy_pairs.png'):
        print(f'已產生 {os.path.join(FIG_DIR, n)}')
    print(f'摘要：{len(runs)} 組、{n_tasks} 筆任務；{len(rows)} 項兩兩比較 → '
          f'{inside} 項落在雜訊內')


if __name__ == '__main__':
    main()
