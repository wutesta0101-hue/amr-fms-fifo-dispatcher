# 第三方檔案聲明

本目錄下的 schema 檔案取自 VDA5050 官方規範，**未經修改**，不是本專案的著作。

| 檔案 | 內容 | 本專案是否實作 |
|---|---|---|
| `order.schema` | order 訊息 | ✅ |
| `state.schema` | state 訊息 | ✅ |
| `connection.schema` | connection 訊息 | ✅ |
| `instantActions.schema` | instantActions 訊息 | ❌ |
| `factsheet.schema` | factsheet 訊息 | ❌ |
| `visualization.schema` | visualization 訊息 | ❌ |
| `responses.schema` | responses 訊息 | ❌ |
| `zoneSet.schema` | zoneSet 訊息 | ❌ |

未實作的五份一併保留，方便對照官方欄位定義。

- 協定版本：VDA5050 3.0（`version` 欄位填 `3.0.0`）
- 來源：https://github.com/VDA5050/VDA5050 的 `json_schemas/`
- 取自：`main` @ commit `ea7c62a`（2026-06-17）
- Schema 宣告：`https://json-schema.org/draft/2020-12/schema`
- 授權：MIT License

選用 `main` 而非 tag `3.0.0`，是因為 tag 中的 `factsheet.schema` 與 `visualization.schema` 含有多餘逗號，無法被 `json.load()` 解析；`main` 為發布後的勘誤修訂。其餘差異多為描述文字或未使用欄位，不影響本專案已實作訊息的驗證結果。八份檔案皆已與上游 blob 雜湊比對確認一致。

保留本聲明是 MIT 授權的要求——散布時須附上原始著作權聲明與授權全文。

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

本專案與 VDA、VDMA 無隸屬關係。使用這些 schema 是為了讓訊息合規性可被實際驗證，驗證方式見根目錄 `README.md`。