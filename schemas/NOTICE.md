# 第三方檔案聲明

本目錄下的三個 schema 檔案**不是本專案的著作**，取自 VDA5050 官方規範，**未經任何修改**：

| 檔案 | 內容 |
|---|---|
| `order.schema` | VDA5050 `order` 訊息的 JSON Schema |
| `state.schema` | VDA5050 `state` 訊息的 JSON Schema |
| `connection.schema` | VDA5050 `connection` 訊息的 JSON Schema |

- **協定版本**：VDA5050 **3.0**（訊息的 `version` 欄位填 `3.0.0`）
- **檔案來源**：<https://github.com/VDA5050/VDA5050> 的 `json_schemas/`
- **取自分支**：`main` @ commit **`ea7c62a`**（2026-06-17）
- **Schema 版本宣告**：`https://json-schema.org/draft/2020-12/schema`
- **授權**：MIT License

保留這份聲明是 MIT 授權的要求——散布時必須附上原始的著作權聲明與授權全文。

### ⚠️ 為什麼是 `main` 而不是 tag `3.0.0`

**這三個檔案與 tag `3.0.0` 並非位元相同**（2026/08/14 逐位元比對確認）。
`main` 是 3.0.0 發布（2026-03-18）之後的**勘誤修訂**，差異如下：

| 檔案 | `main` 相對於 tag `3.0.0` 的差異 | 對本專案的影響 |
|---|---|---|
| `order.schema` | `nodePosition.theta` 的 `unit` 由 `"m"` **更正為 `"rad"`**（3.0.0 把角度單位誤寫成公尺） | 無——`unit` 是註解性關鍵字，不參與驗證 |
| `order.schema` | `orientationType` 新增 `enum: [GLOBAL, TANGENTIAL]` 限制 | 無——本專案不送 edge 的 orientationType |
| `state.schema` | 描述文字的空白修正；移除 controlPoint `weight` 的 `exclusiveMinimum` | 無——本專案不送 trajectory |
| `connection.schema` | 描述文字由 `CONNECTIONBROKEN`／`DISCONNECTED` 更正為 `CONNECTION_BROKEN`／`OFFLINE`，與其 enum 一致 | 無——**兩版的 enum 本身完全相同** |

**結論：改用 tag `3.0.0` 的檔案重驗，本專案的訊息會得到相同結果**
（差異全部落在註解性關鍵字，或落在本專案未使用的欄位上）。
選用 `main` 是因為它修正了 3.0.0 的已知錯誤。

> 上游 repo 自身的免責聲明（VDA5050 README）：
> *"Use of this GitHub repository and all content, information, and support services
> offered therein is at your own risk."*
> 該 repo 未對名稱使用、符合性宣稱或 schema 再散布設定 MIT 以外的額外條件（2026/08/14 查證）。

---

## MIT License

Copyright 2024 Verband der Automobilindustrie

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

> 本專案與 VDA（Verband der Automobilindustrie）、VDMA 均無隸屬關係。
> 使用這些 schema 的目的，是讓「我們發出的訊息符合規範」這件事**可被執行驗證**，
> 而不是口頭宣稱——驗證方式見 repo 根目錄 `README.md` 第十節。
