#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
# 從原始 JSON Lines 產生 M2 的 KPI 對照表與統計圖。
#
# 為什麼要有這支腳本：README〈結果與 KPI〉原本只有一句「KPI 未見明顯劣化」，
# 而所有數字都在 `notes/`（被 .gitignore 排除）。讀者拿不到任何一個數字。
# 這支腳本把數字從原始資料重新算出來，寫成進得了 repo 的 md 與 png。
#
# 判準與配對規則都不在這裡重寫一遍——直接 import `tools/m2_kpi.py`、
# `tools/m2_latency.py`，避免同一個事實出現第二份會各自演化的來源（原則 16）。
#
# 執行（Windows PowerShell 或 WSL bash 皆可，路徑用相對於本 repo 根目錄）：
#   PYTHONIOENCODING=utf-8 python experiments/kpi_report.py
# 資料不在預設位置時用環境變數覆蓋，見下方 M3B_DIR / M2_DIR / VEHICLE_GLOB。
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 資料位置。預設指向 `notes/`——那是個人工作紀錄，不進 git，
# 所以在別台機器上跑要自己給路徑（沒有資料時本腳本會明確報錯，不會產生空圖）。
M3B_DIR = os.environ.get('M3B_DATA_DIR', os.path.join(ROOT, 'notes', 'data'))
M2_DIR = os.environ.get(
    'M2_DATA_DIR', os.path.join(ROOT, 'notes', 'data', 'm2', 'run_20260813_2033'))
# 車端紀錄只有跨世代的那一份（見下方「延遲資料的限制」）
VEHICLE_GLOB = os.environ.get(
    'M2_VEHICLE_GLOB',
    os.path.join(ROOT, 'notes', 'data', 'm2', 'vda5050_tinyRobot*.jsonl'))
BRIDGE_GLOB_ALL = os.environ.get(
    'M2_BRIDGE_GLOB',
    os.path.join(ROOT, 'notes', 'data', 'm2', 'm2bridge_*.jsonl'))

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
FIG_DIR = os.path.join(OUT_DIR, 'figs')
OUT_MD = os.path.join(OUT_DIR, 'M2-KPI對照.md')

os.environ.setdefault('M3B_DATA_DIR', M3B_DIR)
os.environ['M2_DATA_DIR'] = M2_DIR

import matplotlib                                            # noqa: E402
matplotlib.use('Agg')
import matplotlib.pyplot as plt                              # noqa: E402
from matplotlib.legend_handler import HandlerTuple           # noqa: E402

import figstyle as fs                                        # noqa: E402
import m2_kpi                                                # noqa: E402
import m2_latency                                            # noqa: E402

POLICY_ORDER = ['rmf', 'fifo', 'nearest']
TASKS_PER_RUN = m2_latency.TASKS_PER_RUN


# ── 產圖 ────────────────────────────────────────────────────────────────
# 圖一：12 項比較。橫條是「M2 − M3b」的差距，灰帶是同一列的雜訊底線。
# 條子落在灰帶裡＝不能宣稱有差別；伸出灰帶才算真的變好或變差。
def fig_diff(rows, path):
    rows = [c for c in rows if c['old'] is not None]
    fig, ax = plt.subplots(figsize=(6.9, 4.8))
    y = list(range(len(rows)))[::-1]
    for yi, c in zip(y, rows):
        # 參照帶：±雜訊底線（zorder 0）
        ax.barh(yi, 2 * c['noise'], left=-c['noise'], height=.78,
                color=fs.LT, zorder=0, lw=0)
        inside = abs(c['diff']) <= c['noise']
        # 資料自己決定顏色：雜訊內＝灰，超出且變好＝青，超出且變差＝琥珀
        color = fs.MUT if inside else (fs.HOT if c['diff'] > 0 else fs.ACC)
        ax.barh(yi, c['diff'], height=.5, color=color, zorder=2, lw=0,
                hatch='///' if c['single'] else None,
                edgecolor='white' if c['single'] else None)
        ax.text(c['diff'] + (0.6 if c['diff'] >= 0 else -0.6), yi,
                f"{c['diff']:+.1f}s", va='center',
                ha='left' if c['diff'] >= 0 else 'right',
                fontsize=7, color=fs.INK)
    ax.axvline(0, color=fs.EDGE, lw=.9, zorder=3)
    ax.set_yticks(y)
    ax.set_yticklabels([f"{c['label']} · {c['policy']}"
                        + ('  †' if c['single'] else '') for c in rows],
                       fontsize=7.5)
    ax.set_xlabel('M2（VDA5050）減 M3b（原生 fleet_manager），單位秒；負值＝變快')
    ax.set_xlim(-46, 24)
    ax.set_ylim(-0.7, len(rows) - 0.3)
    fs.title(ax, '換成 VDA5050 之後，12 項 KPI 各差多少',
             '灰帶＝該項的雜訊底線（兩批跨輪變異之和）；沒有任何一項往變差的方向伸出灰帶',
             pad=16)
    # 圖例手工做。顏色與斜線是兩個獨立的維度，可以任意組合
    # （灰＋斜線＝雜訊內且單輪），所以斜線那兩格兩種顏色各給一個樣本，
    # 不然讀者會以為斜線專屬於青色。琥珀在這批資料裡不會出現，仍要列出來——
    # 讀者才知道「沒有琥珀」是結論，不是巧合。
    def swatch(color, hatch=None):
        return plt.Rectangle((0, 0), 1, 1, facecolor=color, hatch=hatch,
                             edgecolor='white' if hatch else 'none')

    # 斜線那一列用一個標籤配兩個色塊（HandlerTuple），一眼看出它不分顏色；
    # 拆成兩列會被迫寫「同上，但…」，而且長標籤換行會把右欄的對齊撐開
    entries = [(swatch(fs.MUT), '顏色｜雜訊內，不可宣稱'),
               (swatch(fs.ACC), '顏色｜變好（超出雜訊）'),
               (swatch(fs.HOT), '顏色｜變差（超出雜訊）— 本批 0 項'),
               (swatch(fs.LT), '灰帶｜雜訊底線 ±'),
               ((swatch(fs.MUT, '///'), swatch(fs.ACC, '///')),
                '斜線｜M2 側只有單輪，判定較弱')]
    # 圖例放在圖外的下緣：放圖內一定會壓到條子，而 12 列橫條沒有留白可用
    fig.tight_layout(rect=(0, 0.11, 1, 1))
    fig.legend([h for h, _ in entries], [t for _, t in entries],
               fontsize=6.8, loc='lower center', ncol=2,
               bbox_to_anchor=(0.5, 0.008), columnspacing=2.2,
               handlelength=1.8, handleheight=1.0, alignment='left',
               handler_map={tuple: HandlerTuple(ndivide=None, pad=0.35)})
    fig.savefig(path, dpi=200)
    plt.close(fig)


# 圖二：同一指標下兩批的絕對值並排。圖一看得到差距，看不到基準有多大，
# 所以 −38s 到底是「大幅改善」還是「本來就這麼吵」要靠這張圖判斷。
def fig_compare(old, new, path):
    fig, axes = plt.subplots(1, 4, figsize=(9.6, 3.0))
    for ax, (key, label) in zip(axes, m2_kpi.METRICS):
        xs = range(len(POLICY_ORDER))
        o = [m2_kpi.mean_of(old, p, key) for p in POLICY_ORDER]
        n = [m2_kpi.mean_of(new, p, key) for p in POLICY_ORDER]
        single = [m2_kpi.spread_of(new, p, key) is None for p in POLICY_ORDER]
        ax.bar([x - .2 for x in xs], o, width=.38, color=fs.LT,
               edgecolor=fs.EDGE, lw=.6, label='M3b 原生', zorder=2)
        ax.bar([x + .2 for x in xs], n, width=.38, color=fs.ACC, lw=0,
               label='M2 VDA5050', zorder=2,
               hatch=['///' if s else '' for s in single], edgecolor='white')
        for x, v in zip(xs, o):
            ax.text(x - .2, v, f'{v:.0f}', ha='center', va='bottom',
                    fontsize=6.5, color=fs.MUT)
        for x, v, s in zip(xs, n, single):
            ax.text(x + .2, v, f'{v:.0f}' + ('†' if s else ''), ha='center',
                    va='bottom', fontsize=6.5, color=fs.INK)
        ax.set_xticks(list(xs))
        ax.set_xticklabels(POLICY_ORDER, fontsize=7.5)
        ax.set_ylim(0, max(o + n) * 1.22)
        fs.title(ax, f'{label}（秒）', size=8.5)
    axes[0].legend(fontsize=6.8, loc='upper left')
    axes[-1].text(1.0, -0.16, '†＝單輪', transform=axes[-1].transAxes,
                  ha='right', fontsize=6.5, color=fs.MUT)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


# 圖三：鏈路延遲。左邊是分布（ECDF），右邊回答唯一重要的問題——
# 這條鏈路帶來的開銷，跟我們想比較的策略差異相比是不是小到可以忽略。
def fig_latency(down, up, segs, ctx, path):
    fig, (a, b) = plt.subplots(1, 2, figsize=(8.4, 3.2),
                               gridspec_kw={'width_ratios': [1.25, 1]})

    for xs, c, name in ((down, fs.ACC, '下行 bridge→vehicle'),
                        (up, fs.INK, '上行 vehicle→bridge')):
        xs = sorted(xs)
        ys = [(i + 1) / len(xs) for i in range(len(xs))]
        a.step(xs, ys, where='post', color=c, lw=1.6,
               label=f'{name}  n={len(xs)}  中位數 {st.median(xs):.3f}s')
    a.set_xscale('log')
    # 預設的科學記號刻度會用 U+2212，CJK 字型缺這個字，會印成豆腐框
    a.xaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda v, _: f'{v:g}'))
    a.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    a.set_xlabel('單段延遲（秒，對數軸）')
    a.set_ylabel('累積比例')
    a.set_ylim(0, 1.02)
    a.legend(fontsize=6.8, loc='lower right')
    fs.title(a, '鏈路單段延遲分布',
             '上行幾乎即時；下行的長尾來自 bridge 端的輪詢間隔', pad=16)

    labels = [f'鏈路開銷／單筆任務\n({segs[0]:.1f}–{segs[-1]:.1f} 段)'] + \
             [k for k, _ in ctx]
    per_leg = st.mean(down) + st.mean(up)
    vals = [per_leg * segs[-1]] + [v for _, v in ctx]
    thr = vals[0]
    b.bar(range(len(vals)), vals, width=.62, zorder=2,
          color=[fs.ACC] + [fs.MUT] * len(ctx), lw=0)
    for i, v in enumerate(vals):
        b.text(i, v, f'{v:.1f}s', ha='center', va='bottom', fontsize=7,
               color=fs.INK)
    b.axhline(thr, color=fs.THR, ls='--', lw=1.1, zorder=3)
    b.set_xticks(range(len(vals)))
    b.set_xticklabels(labels, fontsize=6.8)
    b.set_ylim(0, max(vals) * 1.25)
    b.set_ylabel('秒')
    fs.title(b, '這個開銷跟什麼比',
             '紅線＝鏈路開銷上界；它低於我們想比較的每一個量', pad=16)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


# ── 產表 ────────────────────────────────────────────────────────────────
def md_table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |',
           '|' + '|'.join(['---'] * len(header)) + '|']
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def main():
    old, new = m2_kpi.load_all()
    if not any(v for v in new.values()):
        sys.exit(f'找不到 M2 資料：{M2_DIR}\n'
                 '用 M2_DATA_DIR / M3B_DATA_DIR 指定原始 jsonl 的位置。')
    new_cmp, ref_sha, dropped = m2_kpi.comparable_only(new)
    rows = m2_kpi.compare(old, new_cmp)
    bridge = m2_kpi.bridge_stats(M2_DIR)
    # 這批（有版本標記的四組）沒有留下車端紀錄，所以拒絕次數只能從
    # 跨世代那批車端 log 看——不是同一批，不能當這四組的驗收結果
    rej_run = m2_kpi.reject_stats(os.path.join(M2_DIR, 'vda5050_tinyRobot*.jsonl'))
    rej_mixed = m2_kpi.reject_stats(VEHICLE_GLOB)
    down, up = m2_latency.measure(BRIDGE_GLOB_ALL, VEHICLE_GLOB)
    # 段/任務只採用「有版本標記」那一批，避免被舊的半截檔（36 張）拉低下界
    segs = sorted(b['sent'] / TASKS_PER_RUN for b in bridge)
    per_leg = st.mean(down) + st.mean(up)

    # 右圖的對照量：策略之間的差、以及同一策略自己的跨輪變異。
    # 兩者都從資料算，不寫死——資料換了，結論的強弱要跟著換。
    turn = {p: m2_kpi.mean_of(old, p, 'turn_mean') for p in POLICY_ORDER}
    ctx = [('策略間差異\n(fifo 減 nearest\n平均周轉)',
            turn['fifo'] - turn['nearest']),
           ('同策略跨輪變異\n(rmf 平均周轉)',
            m2_kpi.spread_of(new_cmp, 'rmf', 'turn_mean'))]

    fs.apply()
    os.makedirs(FIG_DIR, exist_ok=True)
    fig_diff(rows, os.path.join(FIG_DIR, 'kpi_diff.png'))
    fig_compare(old, new_cmp, os.path.join(FIG_DIR, 'kpi_compare.png'))
    fig_latency(down, up, segs, ctx, os.path.join(FIG_DIR, 'link_latency.png'))

    n_inside = sum(1 for c in rows
                   if c['old'] is not None and c['verdict'].startswith('雜訊內'))
    n_better = sum(1 for c in rows
                   if c['old'] is not None and c['verdict'].startswith('變好'))
    n_worse = sum(1 for c in rows
                  if c['old'] is not None and c['verdict'].startswith('⚠️'))
    n_single = sum(1 for c in rows if c['old'] is not None and c['single'])
    # 單輪 × 判定「變好」＝這次最不能對外講的那幾列
    n_single_better = sum(1 for c in rows if c['old'] is not None
                          and c['single'] and c['verdict'].startswith('變好'))
    total_sent = sum(b['sent'] for b in bridge)
    used = [f"{b['policy']}_r{b['round']}" for b in bridge]
    rej_line = ('**無法判定**（這批未留下車端紀錄）' if rej_run is None
                else f"**{rej_run['rejected']} 次**")

    doc = f'''# M2：換成 VDA5050 介面之後，端到端 KPI 有沒有劣化

> **本檔由 `experiments/kpi_report.py` 從原始 JSON Lines 產生，請勿手動編輯。**
> 數字的正本是原始資料本身；文字結論的正本是
> `notes/M2-步驟5-對照實驗結果.md`（個人工作紀錄，不進 repo）。
>
> 程式版本 `code_sha = {ref_sha}`　·　實驗日期 2026/08/13　·
> 採用組別 {', '.join(used)}（共 {len(bridge)} 組，每組 {TASKS_PER_RUN} 張任務）

---

## 一、結論

**{n_worse} 項超出雜訊變差。** {len(rows)} 項比較（3 策略 × 4 指標）中，
{n_inside} 項落在雜訊底線內、{n_better} 項優於基準；VDA5050 側共發出
**{total_sent} 張 order**，被拒次數{rej_line}——詳見〈VDA5050 側〉。

⚠️ 其中 **{n_single} 項的 M2 側只有單輪**（fifo、nearest 各只跑了 r1），
雜訊底線只含 M3b 自己的跨輪變異，涵蓋不到 M2 的波動。
**這些列標「變好」者不可對外宣稱**——同樣的情形在 rmf 身上發生過：
只看 r1 時判定是「超出雜訊變差 +11.3s」，補上 r2 之後就落回雜訊內。

---

## 二、12 項比較

![12 項 KPI 差距與雜訊底線](figs/kpi_diff.png)

判準沿用 M3b：**差距要能被宣稱，必須大於雜訊底線**
（＝兩批各自「同一策略跨輪的最大差」之和）。落在底線內的一律不下結論。

{md_table(['指標', '策略', 'M3b（原生）', 'M2（VDA5050）', '差距', '雜訊底線', '判定'],
          [[c['label'], c['policy'], f"{c['old']:.1f}", f"{c['new']:.1f}",
            f"{c['diff']:+.1f}", f"{c['noise']:.1f}",
            c['verdict'] + ('（單輪）' if c['single'] else '')]
           for c in rows if c['old'] is not None])}

![四項指標的絕對值對照](figs/kpi_compare.png)

---

## 三、VDA5050 側：order 發出與被拒

{md_table(['組別', 'order 發出', 'cmd 完成', '段/任務'],
          [[f"{b['policy']} r{b['round']}", b['sent'], b['done'],
            f"{b['sent'] / TASKS_PER_RUN:.1f}"]
           for b in bridge] +
          [['**合計**', f'**{total_sent}**',
            f"**{sum(b['done'] for b in bridge)}**", '—']])}

> 「cmd 完成」少於「order 發出」是正常的：`vda5050_bridge.py` 的 `_send_order`
> 直接覆寫該車的待辦指令，被後續指令取代的那一張不會寫出 `cmd_completed`。

### ⚠️ 「零拒絕」這件事，這批資料證明不了

`order_rejected` **只由 `vda5050_vehicle.py` 寫進車端紀錄**，`vda5050_bridge.py`
從不寫這個事件。因此在 bridge 紀錄裡數 `order_rejected` **永遠是 0**——
那不是量測結果，是量錯了檔案（原則 7 的代理指標）。

而這四組**沒有保存車端紀錄**（`{os.path.basename(M2_DIR)}/` 下沒有
`vda5050_tinyRobot*.jsonl`），所以**這 {total_sent} 張 order 有沒有被拒，
無法從留存資料回答**。

現有的車端紀錄只有跨世代、無版本標記的那一份，統計如下——它**不是**上表這四組：

{md_table(['車端紀錄（跨世代，無版本標記）', '數量'],
          [] if rej_mixed is None else
          [['檔案數', rej_mixed['files']],
           ['`order_received`', rej_mixed['received']],
           ['`order_rejected`', f"**{rej_mixed['rejected']}**"]])}

也就是說：**目前能講的是「另一批 {0 if rej_mixed is None else rej_mixed['received']} 筆
order_received 中 0 筆被拒」**，不是「這四組 {total_sent} 張 order 零拒絕」。
要補上這個驗收條件，實驗腳本必須把車端 log 一起收進 run 目錄。

---

## 四、鏈路延遲

![鏈路延遲分布與量級對照](figs/link_latency.png)

```
下行 bridge → vehicle   n={len(down)}  平均 {st.mean(down):.3f}s  中位數 {st.median(down):.3f}s
上行 vehicle → bridge   n={len(up)}  平均 {st.mean(up):.3f}s  中位數 {st.median(up):.3f}s
每段合計約 {per_leg:.2f}s  ×  每任務 {segs[0]:.1f}–{segs[-1]:.1f} 段
  → 單筆任務多出約 {per_leg * segs[0]:.1f}–{per_leg * segs[-1]:.1f}s
```

這個量級小於策略之間的差異（fifo 與 nearest 的平均周轉差
{turn['fifo'] - turn['nearest']:.1f}s），也小於同一策略的跨輪變異
（rmf {m2_kpi.spread_of(new_cmp, 'rmf', 'turn_mean'):.1f}s），因此不影響策略比較的結論。

⚠️ **「段/任務」與舊文件不一致。** 舊版 `m2_latency.py` 把段數**寫死**為 10–13
（因而得到 4–5s/任務），這個數字進了 README 與 `notes/` 的多份文件，
但**從留存資料重算不出來**：這四組實測是每任務 {segs[0]:.1f}–{segs[-1]:.1f} 張 order
（若改數「相異目標點」則是 5.6–8.8）。本檔一律採「order 張數」——
每張 order 就是一次下行＋一次上行，那才是延遲要乘的次數。

⚠️ **延遲資料的限制**：車端紀錄（`vda5050_tinyRobot*.jsonl`）**沒有版本標記、
且混了三個世代**，與〈VDA5050 側〉那 {len(bridge)} 組不是同一批。此節僅供**量級參考**，
不作為驗收依據。段/任務則取自有版本標記的 {len(bridge)} 組。

---

## 五、這批資料不能拿來宣稱的事

| # | 限制 | 影響 |
|---|---|---|
| 1 | fifo、nearest 只有單輪（{n_single} 列） | 其中判定「變好」的 {n_single_better} 列**不可對外宣稱**，只能說「未變差」 |
| 2 | 車端延遲資料跨世代、無版本標記 | 〈鏈路延遲〉只能講量級，不能講精確值 |
| 3 | 這批未保存車端紀錄 | **「order 零拒絕」無法驗證**（見〈VDA5050 側〉）；下一輪實驗要把車端 log 收進 run 目錄 |
| 4 | rmf r2 曾出現 `Read timed out` 風暴，成因未知 | 重跑後 0 次逾時、8/8 完成，但**間歇性缺陷仍在**；當時的 log 被同名覆蓋而遺失 |
| 5 | 每組僅 {TASKS_PER_RUN} 張任務、2 台車 | 樣本小；結論限於 office 場景這個規模 |

---

## 六、重現

原始資料在 `notes/`（個人工作紀錄，不進 repo）。有資料時：

```bash
PYTHONIOENCODING=utf-8 python experiments/kpi_report.py
```

資料放在別處時用環境變數指定：

```bash
M3B_DATA_DIR=<M3b jsonl 目錄> M2_DATA_DIR=<M2 jsonl 目錄> \\
M2_VEHICLE_GLOB='<車端 jsonl glob>' python experiments/kpi_report.py
```

只要表格數字（不產圖）：`python tools/m2_kpi.py`；只要延遲：`python tools/m2_latency.py`。

> 這裡比的是**介面**，Open-RMF 在兩邊都在、沒被換掉。
> 要看「自寫派工器 vs RMF 自己投標」，見 [M3b-策略對照.md](M3b-策略對照.md)。
'''
    if dropped:
        doc += f'\n> 已排除版本不符或無標記的組別：{", ".join(dropped)}。\n'

    with open(OUT_MD, 'w', encoding='utf-8', newline='\n') as f:
        f.write(doc)
    print(f'已產生 {OUT_MD}')
    for n in ('kpi_diff.png', 'kpi_compare.png', 'link_latency.png'):
        print(f'已產生 {os.path.join(FIG_DIR, n)}')
    print(f'摘要：{len(rows)} 項比較 → 雜訊內 {n_inside}／變好 {n_better}／'
          f'變差 {n_worse}（其中單輪 {n_single} 項）；order {total_sent} 張，'
          + ('被拒無法判定（缺車端紀錄）' if rej_run is None
             else f"被拒 {rej_run['rejected']} 次"))


if __name__ == '__main__':
    main()
