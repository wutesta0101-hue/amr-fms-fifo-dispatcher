# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
#
# vda5050_vehicle 的單元測試：只測純邏輯，不啟動 ROS、不連 MQTT、不寫檔。
#
# 為什麼繞過 __init__：VDA5050Vehicle 繼承 rclpy Node，建構時會註冊 ROS 參數、
# 連 MQTT broker、開紀錄檔。這些都是整合層面的東西，已由 tools/verify_chain.sh
# 涵蓋。單元測試要問的是另一件事——「收到這樣的輸入，判斷對不對」——
# 所以用 __new__ 建出空殼，只填被測方法真正會碰到的屬性。
#
# 對應的失敗會長什麼樣：改壞 _handle_order 的冪等判斷、把 state 的必要欄位漏掉、
# 把 errorType 打錯字，這裡都會紅。

import json
import math
import re
from pathlib import Path

import pytest

from rmf_fleet_msgs.msg import Location, RobotMode, RobotState

from fifo_dispatcher.vda5050_vehicle import (
    ORDER_REQUIRED, REPLAN_ERRORS, VDA5050Vehicle, iso_now, wrap_pi,
)


# ── 測試替身 ────────────────────────────────────────────────────────────────

# 取代 node.get_logger()：把訊息吞掉，測試不該被 log 洗版
class _NullLogger:
    def info(self, *a, **k):
        pass

    warning = error = debug = info


# 建一台「只有純邏輯」的車：不連 MQTT、不寫檔、不註冊 ROS 參數
def make_vehicle(**overrides):
    v = VDA5050Vehicle.__new__(VDA5050Vehicle)
    v.manufacturer = 'rmfdemos'
    v.serial = 'tinyRobot1'
    v.fleet = 'tinyRobot'
    v.header = {'state': 0, 'connection': 0}
    v.robot = None
    v.order = None
    v.reached = False
    v.last_node_id = ''
    v.last_node_seq = 0
    v.action_states = []
    v.order_error = None
    v.tolerance = 0.5
    v.max_eta = 120.0
    v.adopt_timeout = 3.0
    v.v_lin = 0.5
    v.v_ang = 0.6
    v.__dict__.update(overrides)
    v.get_logger = _NullLogger
    v._write = lambda record: None       # 不寫 JSON Lines
    return v


# 一張最小可用的 order：9 個必要欄位齊全，一個 released 且帶位置的節點
def make_order(**overrides):
    order = {
        'headerId': 1,
        'timestamp': iso_now(),
        'version': '3.0.0',
        'manufacturer': 'rmfdemos',
        'serialNumber': 'tinyRobot1',
        'orderId': '7',
        'orderUpdateId': 0,
        'nodes': [node('cmd_7', 0, 3.0, 4.0)],
        'edges': [],
    }
    order.update(overrides)
    return order


def node(node_id, seq, x, y, released=True, with_position=True, actions=None):
    n = {'nodeId': node_id, 'sequenceId': seq, 'released': released}
    if with_position:
        n['nodePosition'] = {'x': x, 'y': y, 'mapId': 'L1'}
    if actions:
        n['actions'] = actions
    return n


# 造一則 slotcar 車況；預設是「停在目標點、任務編號相符」
def robot_state(name='tinyRobot1', x=3.0, y=4.0, yaw=0.0, task_id='7',
                mode=RobotMode.MODE_IDLE, battery=87.5, path_len=0, t_sec=100):
    r = RobotState()
    r.name = name
    r.task_id = task_id
    r.battery_percent = battery
    r.mode = RobotMode(mode=mode)
    loc = Location()
    loc.x = x
    loc.y = y
    loc.yaw = yaw
    loc.level_name = 'L1'
    loc.t.sec = t_sec
    r.location = loc
    r.path = [Location() for _ in range(path_len)]
    return r


# 從測試檔往上找 repo 的 schemas/；WSL 的 ROS 套件底下沒有這個目錄，找不到就跳過
def find_schema(name):
    for parent in Path(__file__).resolve().parents:
        candidate = parent / 'schemas' / name
        if candidate.exists():
            return candidate
    return None


# ── 1. 純函式 ───────────────────────────────────────────────────────────────

# theta 送進 state 之前必須落在 [-pi, pi]，否則不符 mobileRobotPosition 的 schema
@pytest.mark.parametrize('raw', [0.0, math.pi, -math.pi, 3 * math.pi,
                                 -3 * math.pi, 100.0, -0.5])
def test_wrap_pi_folds_into_range(raw):
    out = wrap_pi(raw)
    assert -math.pi - 1e-9 <= out <= math.pi + 1e-9
    # 折疊後必須和原角度等價（差整數倍 2pi）
    assert abs(math.sin(out) - math.sin(raw)) < 1e-9
    assert abs(math.cos(out) - math.cos(raw)) < 1e-9


# timestamp 的格式是 schema 明定的，不是隨便一種 ISO8601
def test_iso_now_matches_schema_pattern():
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z', iso_now())


# ── 2. 目標節點選取（本專案的簡化：只認最後一個可用節點）────────────────────

def test_target_node_takes_last_released_with_position():
    v = make_vehicle()
    nodes = [node('a', 0, 1.0, 1.0), node('b', 1, 2.0, 2.0)]
    assert v._target_node(nodes)['nodeId'] == 'b'


# horizon 節點（released=False）與沒有座標的節點都不能當目標點
def test_target_node_skips_unreleased_and_positionless():
    v = make_vehicle()
    nodes = [
        node('ok', 0, 1.0, 1.0),
        node('horizon', 1, 2.0, 2.0, released=False),
        node('nopos', 2, 0.0, 0.0, with_position=False),
    ]
    assert v._target_node(nodes)['nodeId'] == 'ok'


def test_target_node_returns_none_when_no_usable():
    v = make_vehicle()
    assert v._target_node([node('h', 0, 1.0, 1.0, released=False)]) is None


# ── 3. order 解析與驗證 ─────────────────────────────────────────────────────

# 壞掉的 JSON 不能讓節點掛掉，要轉成規範的 VALIDATION_FAILURE
def test_rejects_malformed_json():
    v = make_vehicle()
    v._handle_order(b'{not json')
    assert v.order is None
    assert v.order_error['errorType'] == 'VALIDATION_FAILURE'


# 9 個必要欄位缺任何一個都要拒絕，且錯誤描述要指出缺了哪個
@pytest.mark.parametrize('missing', ORDER_REQUIRED)
def test_rejects_missing_required_fields(missing):
    payload = make_order()
    payload.pop(missing)
    v = make_vehicle()
    v._handle_order(json.dumps(payload).encode())
    assert v.order is None
    assert v.order_error['errorType'] == 'VALIDATION_FAILURE'
    assert missing in v.order_error['errorDescription']


# 別台車的 order：忽略就好，不是錯誤——不該留下 errorType 汙染自己的 state
def test_ignores_order_for_other_serial():
    v = make_vehicle()
    v._handle_order(json.dumps(make_order(serialNumber='tinyRobot2')).encode())
    assert v.order is None
    assert v.order_error is None


# 重送同一個 orderUpdateId ＝ 冪等丟棄（規範要求），不可重跑
def test_discards_same_order_update_id():
    v = make_vehicle()
    v._handle_order(json.dumps(make_order(orderUpdateId=2)).encode())
    v.reached = True                       # 假裝已經跑完
    v._handle_order(json.dumps(make_order(orderUpdateId=2)).encode())
    assert v.reached is True               # 沒有被重置＝沒有重跑
    assert v.order['orderUpdateId'] == 2


# 比現行小的更新是過期訊息，同樣丟棄
def test_discards_older_order_update_id():
    v = make_vehicle()
    v._handle_order(json.dumps(make_order(orderUpdateId=5)).encode())
    v._handle_order(json.dumps(make_order(orderUpdateId=4)).encode())
    assert v.order['orderUpdateId'] == 5


# 較大的更新要接受，並重置抵達旗標
def test_accepts_newer_order_update_id():
    v = make_vehicle()
    v._handle_order(json.dumps(make_order(orderUpdateId=1)).encode())
    v.reached = True
    v._handle_order(json.dumps(make_order(orderUpdateId=2)).encode())
    assert v.order['orderUpdateId'] == 2
    assert v.reached is False


# 只有 horizon 節點的 order 無法執行——本專案不做 base/horizon，如實拒絕
def test_rejects_order_without_usable_node():
    v = make_vehicle()
    payload = make_order(nodes=[node('h', 0, 1.0, 1.0, released=False)])
    v._handle_order(json.dumps(payload).encode())
    assert v.order is None
    assert v.order_error['errorType'] == 'ORDER_NOT_EXECUTABLE'


# ── 4. actions：不實作就如實回報 FAILED，不假裝完成 ─────────────────────────

def test_actions_reported_failed():
    acts = [{'actionId': 'a1', 'actionType': 'pick'},
            {'actionId': 'a2', 'actionType': 'drop'}]
    v = make_vehicle()
    payload = make_order(nodes=[node('cmd_7', 0, 3.0, 4.0, actions=acts)])
    v._handle_order(json.dumps(payload).encode())
    assert [s['actionStatus'] for s in v.action_states] == ['FAILED', 'FAILED']
    assert [s['actionType'] for s in v.action_states] == ['pick', 'drop']


def test_no_actions_gives_empty_action_states():
    v = make_vehicle()
    v._handle_order(json.dumps(make_order()).encode())
    assert v.action_states == []


# ── 5. 表頭：headerId 依 topic 各自遞增（規範要求）──────────────────────────

def test_header_ids_increment_independently():
    v = make_vehicle()
    assert [v._header('state')['headerId'] for _ in range(3)] == [1, 2, 3]
    assert v._header('connection')['headerId'] == 1      # 不受 state 影響
    assert v._header('state')['headerId'] == 4


# ── 6. state 組裝 ───────────────────────────────────────────────────────────

# 18 個必要欄位一個不少——直接拿官方 schema 的 required 當期望值，不手抄
def test_build_state_has_all_required_fields():
    path = find_schema('state.schema')
    if path is None:
        pytest.skip('找不到 schemas/state.schema（WSL 的 ROS 套件下沒有這個目錄）')
    required = json.loads(path.read_text(encoding='utf-8'))['required']
    v = make_vehicle(robot=robot_state())
    state = v._build_state()
    assert [k for k in required if k not in state] == []


# 還沒收到 /robot_state 時：不可捏造位置，且要說明自己看不到車
def test_state_without_robot_reports_unavailable():
    v = make_vehicle()
    state = v._build_state()
    assert 'mobileRobotPosition' not in state
    assert state['powerSupply'] == {'stateOfCharge': 0.0, 'charging': False}
    assert 'ROBOT_STATE_UNAVAILABLE' in [e['errorType'] for e in state['errors']]


# 有車況時：電量與位置要如實轉換，theta 必須已折疊
def test_state_with_robot_fills_position_and_power():
    v = make_vehicle(robot=robot_state(x=1.234567, y=-2.5, yaw=3 * math.pi,
                                       battery=87.46, mode=RobotMode.MODE_MOVING))
    state = v._build_state()
    assert state['driving'] is True
    assert state['powerSupply']['stateOfCharge'] == 87.5      # 四捨五入到小數 1 位
    assert state['powerSupply']['charging'] is False
    pos = state['mobileRobotPosition']
    assert pos['x'] == 1.235 and pos['y'] == -2.5             # 四捨五入到小數 3 位
    assert pos['mapId'] == 'L1'


# ⚠️ 已知缺陷（2026/08/15 由本測試發現，尚未修）
#
# state.schema 明定 theta ∈ [-3.14159265359, 3.14159265359]，
# 但 _build_state 用的是 round(wrap_pi(yaw), 4)。車頭朝 -x 時 wrap_pi 回傳 π，
# round(π, 4) = 3.1416 > 上限 → 送出的 state 不符 schema。
#
# 觸發窗口約 ±4.6e-5 rad（約 0.0026°），10Hz 下一趟 90 秒任務的機率約 1%，
# 而 state_schema 參數預設為空（不驗證），所以四組實驗都不會報出來。
# tools/vda5050_schema_check.py 只抽驗 25 則，也抽不到。
#
# strict=True：修好之後這個測試會 XPASS 而讓套件變紅，提醒把標記拿掉。
@pytest.mark.xfail(strict=True, reason='round(wrap_pi(π), 4) 會超出 schema 上限')
def test_theta_stays_within_schema_bounds():
    path = find_schema('state.schema')
    if path is None:
        pytest.skip('找不到 schemas/state.schema')
    schema = json.loads(path.read_text(encoding='utf-8'))
    ref = schema['properties']['mobileRobotPosition']['$ref'].split('/')[-1]
    theta = schema['definitions'][ref]['properties']['theta']
    lo, hi = theta['minimum'], theta['maximum']

    # 掃過整個 [-π, π]，任何朝向送出去都必須落在 schema 的界內
    for k in range(0, 2001):
        yaw = -math.pi + k * (2 * math.pi / 2000)
        out = make_vehicle(robot=robot_state(yaw=yaw))._build_state()
        assert lo <= out['mobileRobotPosition']['theta'] <= hi, f'yaw={yaw!r}'


# 充電中：charging 要跟著 RobotMode 走
def test_state_reports_charging_from_robot_mode():
    v = make_vehicle(robot=robot_state(mode=RobotMode.MODE_CHARGING))
    assert v._build_state()['powerSupply']['charging'] is True


# ── 7. 錯誤映射：slotcar 的模式 → 規範的 errorType ─────────────────────────

@pytest.mark.parametrize('mode,expected', [
    (RobotMode.MODE_WAITING, 'BLOCKED_BY_OTHER_ROBOT'),
    (RobotMode.MODE_ADAPTER_ERROR, 'OUTSIDE_OF_CORRIDOR'),
])
def test_replan_error_mapping(mode, expected):
    v = make_vehicle(robot=robot_state(mode=mode))
    assert expected in [e['errorType'] for e in v._errors()]
    assert REPLAN_ERRORS[mode][0] == expected


# 正常行駛不該產生錯誤——否則 bridge 會誤觸發 replan
def test_no_error_when_moving_normally():
    v = make_vehicle(robot=robot_state(mode=RobotMode.MODE_MOVING))
    assert v._errors() == []


# ── 8. 發布觸發：位置變動不算「重要欄位」，否則會以 10Hz 洗頻 ───────────────

def test_signature_ignores_position_changes():
    v = make_vehicle(robot=robot_state(x=1.0, y=1.0))
    before = v._signature()
    v.robot = robot_state(x=9.9, y=9.9)          # 只有位置變
    assert v._signature() == before


def test_signature_changes_on_new_order():
    v = make_vehicle(robot=robot_state())
    before = v._signature()
    v._handle_order(json.dumps(make_order(orderId='99')).encode())
    assert v._signature() != before
