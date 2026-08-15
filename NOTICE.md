# NOTICE

**amr-fms-fifo-dispatcher**  
Copyright 2026 Testa Wu  

本專案以 Apache License 2.0 釋出，條款見 [`LICENSE`](LICENSE)。  

本檔案說明專案中來自第三方、或衍生自第三方的部分。

---

## 一、直接散布的第三方檔案

### `schemas/*.schema`

VDA5050 官方 JSON Schema，未經修改。

- 協定版本：VDA5050 3.0
- 來源：https://github.com/VDA5050/VDA5050 的 `json_schemas/`
- 取自：`main` @ commit `ea7c62a`（2026-06-17）
- 授權：MIT License
- Copyright 2024 Verband der Automobilindustrie

與 tag `3.0.0` 的差異見 [`schemas/NOTICE.md`](schemas/NOTICE.md)（含 MIT 授權全文，散布時須保留）。

---

## 二、衍生自 Open-RMF 的部分

以下檔案沒有複製上游程式碼，但沿用了上游定義的介面形狀（HTTP 端點路徑、查詢參數、回應欄位、launch 參數），以便與 `fleet_adapter` 相容。

**上游**

- 專案：Open-RMF `rmf_demos`，tag 2.0.4（commit `34d251e`，2023-08-10）
- 來源：https://github.com/open-rmf/rmf_demos
- 授權：Apache License 2.0
- Copyright 2021 Open Source Robotics Foundation, Inc.

**對照**

| 本專案檔案 | 沿用內容 | 上游來源 |
|---|---|---|
| `vda5050_bridge.py` | 五個 HTTP 端點的路徑、查詢參數與回應欄位（`status`／`navigate`／`stop_robot`／`start_task`／`toggle_action`） | `rmf_demos_fleet_adapter/RobotClientAPI.py` |
| 同上 | `/status` 回應欄位格式、剩餘時間估計 | `rmf_demos_fleet_adapter/fleet_manager.py` |
| `office_vda5050.launch.xml` | `fleet_adapter` 啟動參數 | `rmf_demos_fleet_adapter/launch/fleet_adapter.launch.xml` |
| 同上 | `common.launch.xml`、`simulation.launch.xml` 的 include 與參數 | `rmf_demos`、`rmf_demos_gz` |

**變更說明**（Apache-2.0 §4(b)）

1. 不啟動 `fleet_manager`：改由本專案的 `vda5050_bridge` 接手 HTTP `:22011`。
2. 南向改走 VDA5050：經 MQTT 的 `order`／`state`／`connection`，再由 `vda5050_vehicle` 落回 ROS。
3. 未修改、未複製 `rmf_demos` 原始碼；本專案程式碼均在獨立套件 `fifo_dispatcher` 內。

---

## 三、相依套件

執行時相依下列套件，原始碼不包含在本 repo。

**直接相依**（版本為 2026/08/14 實測，見 [`requirements.txt`](requirements.txt)）：

| 套件 | 版本 | 授權 | 查證 |
|---|---|---|---|
| `rclpy`、`builtin_interfaces` | Humble | Apache-2.0 | 本機 `package.xml` |
| `rmf_task_msgs`、`rmf_fleet_msgs` | Humble | Apache-2.0 | 同上 |
| FastAPI | 0.141.1 | MIT | pip-licenses / PyPI |
| pydantic | 2.13.4 | MIT | 上游 LICENSE |
| PyYAML | 6.0.3 | MIT | pip-licenses |
| jsonschema | 4.26.0 | MIT | pip-licenses / PyPI |
| uvicorn | 0.52.1 | BSD-3-Clause | pip-licenses / PyPI |
| paho-mqtt | 1.5.1 | EPL-1.0 / EDL-1.0 | pip-licenses |

`paho-mqtt` 1.5.1 為 EPL-1.0 / EDL-1.0；2.x 起改為 EPL-2.0 OR BSD-3-Clause，升級時需重新確認。EPL 僅在修改該元件本身時觸發，單純呼叫不受影響。

**遞移相依**（2026/08/14 以 `pip-licenses` 掃描，無 GPL／AGPL／未知授權）：

| 套件 | 授權 |
|---|---|
| starlette | BSD-3-Clause |
| anyio、h11、annotated-types、pydantic-core、attrs、pyrsistent | MIT |
| click、idna | BSD |
| typing-extensions | PSF-2.0 |

**外部服務／工具**（未散布，僅執行環境使用）：

| 元件 | 授權 |
|---|---|
| Eclipse Mosquitto | EPL-2.0 + EDL-1.0 |
| Ignition Gazebo Fortress | Apache-2.0 |

升級相依後建議重跑 `pip-licenses` 確認。

---

## 四、聲明

本專案為個人練習，與 Open Robotics／Open Source Robotics Foundation、
VDA（Verband der Automobilindustrie）、VDMA 均無隸屬關係，亦非任何組織的官方實作或認證產品。

「VDA5050」為其權利人所有的名稱，本專案僅作描述性使用，不主張任何認證或背書。

---