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

VDA5050 官方 JSON Schema，**未經修改**——2026/08/14 以 sha256 逐位元比對上游確認相同。

- 協定版本：VDA5050 **3.0**
- 檔案來源：<https://github.com/VDA5050/VDA5050> 的 `json_schemas/`，
  取自 `main` 分支 @ commit **`ea7c62a`**（2026-06-17，即 3.0.0 發布後的勘誤修訂）
- 授權：**MIT License**
- Copyright 2024 Verband der Automobilindustrie

**與 tag `3.0.0` 的差異、以及為何不影響本專案的結果**，見
[`schemas/NOTICE.md`](schemas/NOTICE.md)（該檔同時包含 MIT 授權全文，散布時必須保留）。

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

**直接相依**（版本為 2026/08/14 實測，見 [`requirements.txt`](requirements.txt)）：

| 套件 | 實測版本 | 授權 | 查證來源 |
|---|---|---|---|
| `rclpy`、`builtin_interfaces` | Humble | Apache-2.0 | 本機 `/opt/ros/humble/share/<pkg>/package.xml` 的 `<license>` |
| `rmf_task_msgs`、`rmf_fleet_msgs` | Humble | Apache-2.0 | 同上 |
| FastAPI | 0.141.1 | MIT | `pip-licenses` + PyPI |
| pydantic | 2.13.4 | MIT | 上游 `LICENSE`（Copyright 2017-present Pydantic Services Inc.） |
| PyYAML | 6.0.3 | MIT | `pip-licenses` |
| jsonschema | 4.26.0 | MIT | `pip-licenses` + PyPI |
| uvicorn | 0.52.1 | BSD-3-Clause | `pip-licenses` + PyPI |
| **paho-mqtt** | **1.5.1** | **EPL-1.0 / EDL-1.0**（雙授權） | `pip-licenses` |

> ⚠️ **`paho-mqtt` 的授權隨版本而異**：本專案使用的 **1.5.1 是 EPL-1.0 / EDL-1.0**；
> 該套件自 **2.x 起改為 EPL-2.0 OR BSD-3-Clause**。升級版本時要重新確認。
> EPL 屬**弱 copyleft**——僅在修改該元件本身時觸發，單純呼叫不受影響。

**遞移相依**（2026/08/14 以 `pip-licenses` 掃描，全部為寬鬆授權，**無 GPL／AGPL／未知**）：

| 套件 | 授權 |
|---|---|
| starlette | BSD-3-Clause |
| anyio、h11、annotated-types、pydantic-core、attrs、pyrsistent | MIT |
| click、idna | BSD |
| typing-extensions | PSF-2.0 |

**外部服務／工具**（非相依套件，未散布，僅在執行環境中使用）：

| 元件 | 授權 |
|---|---|
| Eclipse Mosquitto（MQTT broker） | EPL-2.0 + EDL-1.0（雙授權） |
| Ignition Gazebo Fortress | Apache-2.0 |

> 上表所有項目皆已直接查證，**含遞移相依**（2026/08/14 以 `pip-licenses` 掃描）。
> 升級任何相依之後應重跑一次——`paho-mqtt` 從 1.x 到 2.x 就換過授權。

---

## 四、聲明

本專案為**個人練習專案**，與 Open Robotics／Open Source Robotics Foundation、
VDA（Verband der Automobilindustrie）、VDMA 均**無隸屬關係**，
亦非任何組織的官方實作或認證產品。

「VDA5050」為其權利人所有的名稱，本專案僅作**描述性使用**
（說明所實作的協定版本），不主張任何認證或背書。
