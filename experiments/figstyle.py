#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
"""
學術圖表配色模組 —— 灰階承載結構，彩色只標示需要立即注意的訊號
與 Mermaid 淺色圖表共用前五色，可跨圖表類型與流程圖保持一致

正本：`流程圖跟統計圖參數格式/長條圖、熱力圖、折線圖配色與格式.md`（專案母資料夾）
本檔為該正本的可執行版本；配色若不一致，以正本為準。
"""
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

# ══ 共用調色盤 ═══════════════════════════════════════════════
INK = '#15191E'    # 文字、標題、最強調標記      （= Mermaid color）
MUT = '#78899B'    # 軸刻度、次要文字、平均線
EDGE = '#B4BAC1'   # 軸線、邊框                  （= Mermaid base stroke）
LT = '#DFE2E5'     # 參照帶、熱圖中段            （= Mermaid pivot fill）
PALE = '#F4F5F6'   # 最淺填色                    （= Mermaid soft fill）

ACC = '#1B8FA6'    # 訊號色 · 正常（青）
HOT = '#C77B14'    # 訊號色 · 超標（琥珀）
THR = '#C43B45'    # 門檻線（紅）—— 全圖僅此一用途

# 單色→琥珀漸層，供熱圖使用；vmin 應設為「無效應」的基準值
CMAP_HOT = LinearSegmentedColormap.from_list('mono_hot', ['#FFFFFF', LT, HOT])
# 需要雙向（低於/高於基準）時使用
CMAP_DIV = LinearSegmentedColormap.from_list('div', [ACC, '#FFFFFF', HOT])

# 圖上全是中文標籤，缺 CJK 字型會整片變成豆腐框。
# 依序試 Windows／WSL 常見的 CJK 字型，都沒有就退回預設（此時中文會是方框，
# 但軸與數值仍可讀，不會靜默產生看起來正常卻不可讀的圖）。
CJK_FONTS = ['Microsoft JhengHei', 'Microsoft YaHei', 'Noto Sans CJK TC',
             'Noto Sans CJK SC', 'Source Han Sans TW', 'PingFang TC', 'SimHei']


def cjk_font():
    from matplotlib import font_manager as fm
    have = {f.name for f in fm.fontManager.ttflist}
    return [n for n in CJK_FONTS if n in have]


def apply(base_size=8):
    plt.rcParams.update({
        'font.size': base_size,
        'font.sans-serif': cjk_font() + plt.rcParams['font.sans-serif'],
        'axes.unicode_minus': False,   # 用 CJK 字型時 U+2212 常缺字
        'axes.edgecolor': EDGE,
        'axes.linewidth': 0.8,
        'xtick.color': MUT,
        'ytick.color': MUT,
        'axes.labelcolor': INK,
        'text.color': INK,
        'axes.spines.top': False,
        'axes.spines.right': False,
        'figure.facecolor': 'white',
        'savefig.facecolor': 'white',
        'legend.frameon': False,
        'axes.titlecolor': INK,
    })


def title(ax, main, sub=None, size=9, pad=None):
    """主標題短而粗，副說明另起一行降階為 MUT

    `pad` 是本檔對正本的唯一擴充：正本寫死 10pt，那是照它自己的圖高算的；
    軸一高，1.015 這個相對位置就會把副說明頂到主標題上。字重疊時把 pad 加大。
    """
    if pad is None:
        pad = 10 if sub else 4
    ax.set_title(main, fontsize=size, weight='bold', loc='left', pad=pad)
    if sub:
        ax.text(0.0, 1.015, sub, transform=ax.transAxes,
                fontsize=size - 1.5, color=MUT, va='bottom')
