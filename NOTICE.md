# NOTICE

**amr-fms-fifo-dispatcher**
Copyright 2026 Testa Wu

本專案以 **Apache License 2.0** 釋出，條款見 [`LICENSE`](LICENSE)。

本檔案說明專案中**來自第三方、或衍生自第三方**的部分。
列出這些不是形式：介面實作本來就必須沿用對方定義的形狀，把來源講清楚才分得出
「哪些是我設計的、哪些是我整合的」。

---

## 一、直接散布的第三方檔案

### `schemas/order.schema`、`schemas/state.schema`、`schemas/connection.schema`

VDA5050 官方規範 **3.0.0** 的 JSON Schema，**未經修改**。

- 來源：<https://github.com/VDA5050/VDA5050>
- 授權：**MIT License**
- Copyright 2024 Verband der Automobilindustrie

完整授權文字見 [`schemas/NOTICE.md`](schemas/NOTICE.md)（MIT 要求散布時保留）。

---

## 二、衍生自 Open-RMF 的部分

以下檔案**沒有複製上游的程式碼**，但**沿用了上游定義的介面形狀**
（HTTP 端點路徑、查詢參數、回應欄位、launch 參數）。
介面相容是這個專案的目的——`vda5050_bridge` 要能被 `fleet_adapter` 直接呼叫，
少一個欄位對方就取不到值，因此這些形狀是必要條件而非選擇。

**上游**

- 專案：Open-RMF `rmf_demos`，tag **2.0.4**（commit `34d251e`, 2023-08-10）
- 來源：<https://github.com/open-rmf/rmf_demos>
- 授權：**Apache License 2.0**
- **Copyright 2021 Open Source Robotics Foundation, Inc.**

**對照表**

| 本專案的檔案 | 沿用了什麼 | 上游來源 |
|---|---|---|
| `src/fifo_dispatcher/fifo_dispatcher/vda5050_bridge.py` | 五個 HTTP 端點的路徑、查詢參數與回應欄位（`status`／`navigate`／`stop_robot`／`start_task`／`toggle_action`） | `rmf_demos_fleet_adapter/RobotClientAPI.py` 的呼叫方式 |
| 同上 | `/status` 回應的七個欄位格式、剩餘時間的估計算法 | `rmf_demos_fleet_adapter/fleet_manager.py` 的 `get_robot_state` |
| `src/fifo_dispatcher/launch/office_vda5050.launch.xml` | `fleet_adapter` 節點的啟動參數 | `rmf_demos_fleet_adapter/launch/fleet_adapter.launch.xml` |
| 同上 | `common.launch.xml`、`simulation.launch.xml` 的 include 與參數 | `rmf_demos/common.launch.xml`、`rmf_demos_gz/simulation.launch.xml` |

**做了什麼變更**（Apache-2.0 §4(b) 的變更說明）

1. **不啟動 `fleet_manager`**：`office_vda5050.launch.xml` 刻意不 include
   `fleet_adapter.launch.xml`（該檔會連帶啟動 `fleet_manager`），改為自行以 `<node>`
   啟動 `fleet_adapter`，並由本專案的 `vda5050_bridge` 接手 HTTP `:22011`。
2. **南向改走 VDA5050**：上游的 `fleet_manager` 直接對 ROS topic
   （`robot_path_requests` / `robot_state`）收發；本專案改為經 MQTT 的
   VDA5050 `order` / `state` / `connection`，由 `vda5050_vehicle` 再落回 ROS。
3. **`rmf_demos` 的原始碼未被修改**，也未被複製進本 repo。
   本專案的所有程式碼都在獨立的 ROS 2 套件 `fifo_dispatcher` 內。

---

## 三、相依套件

本專案在執行時相依下列套件，**它們的原始碼不包含在本 repo 內**：

| 套件 | 授權 |
|---|---|
| ROS 2 Humble、Open-RMF | Apache-2.0 |
| FastAPI、PyYAML、jsonschema | MIT |
| uvicorn | BSD-3-Clause |
| paho-mqtt | EPL-2.0 / EDL-1.0 |

> 上表中經直接查證的是 VDA5050（MIT）與 `rmf_demos`（Apache-2.0，見上游 `LICENSE` 與檔案標頭）；
> Python 套件的授權為一般認知，商業用途前請以 `pip-licenses` 實際確認完整相依樹。

---

## 四、聲明

本專案為**個人練習專案**，與 Open Robotics／Open Source Robotics Foundation、
VDA（Verband der Automobilindustrie）、VDMA 均**無隸屬關係**，
亦非任何組織的官方實作或認證產品。

「VDA5050」為其權利人所有的名稱，本專案僅作**描述性使用**
（說明所實作的協定版本），不主張任何認證或背書。
