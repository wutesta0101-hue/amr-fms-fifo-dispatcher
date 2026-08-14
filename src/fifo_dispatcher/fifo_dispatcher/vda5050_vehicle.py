#!/usr/bin/env python3
# M2 步驟 1：VDA5050 3.0 模擬車輛（車上軟體那一層）
#
# 北向：MQTT —— 訂閱 order、發布 state 與 connection
# 南向：ROS 2 —— 訂閱 /robot_state（Gazebo slotcar 回報）、
#                 發布 /robot_path_requests（驅動 slotcar）
#
# 一個行程只服務一台車：serial_number 就是車名，對應 VDA5050「車上軟體」
# 的定位；多台車就起多個節點。
#
# 可單獨啟動測試（原則 10）：沒有 Gazebo 也會照常發 state，
# 只是省略 mobileRobotPosition 並附上 robotStateUnavailable 警告。
#
# 欄位一律以 schemas/{order,state,connection}.schema（官方 3.0）為準，
# 不照抄網路上的 2.x 範例（agvPosition→mobileRobotPosition、batteryState→powerSupply）。
#
# 刻意的簡化（詳見 notes/M2設計決策-VDA5050橋接.md 第五節）：
#   ① order 只取「最後一個 released 且帶 nodePosition 的 node」當目標點，
#      路徑規劃留給 RMF；edges 一律忽略。
#   ② 停車＝下一張目標點等於目前位置的 order（PathRequest 前後兩點相同），
#      因此不需要 instantActions topic。
#   ③ 不實作 actions；order 若帶 actions，回報 FAILED 並附 WARNING 錯誤。

import json
import math
import time
from collections import deque
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import rclpy
from rclpy._rclpy_pybind11 import RCLError
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_system_default

from builtin_interfaces.msg import Time
from rmf_fleet_msgs.msg import Location, PathRequest, RobotMode, RobotState

from fifo_dispatcher.version import code_version

VDA5050_VERSION = '3.0.0'
MAJOR = 'v' + VDA5050_VERSION.split('.')[0]

# order 的 9 個必要欄位（order.schema 的 required）
ORDER_REQUIRED = ('headerId', 'timestamp', 'version', 'manufacturer',
                  'serialNumber', 'orderId', 'orderUpdateId', 'nodes', 'edges')

# slotcar 用這兩個模式表達「需要重新規劃」，state 中轉成 errors 回報，
# 由 vda5050_bridge 換算成 RMF 的 replan（見設計文件 9.2）
#
# errorType 一律用規範的 UPPER_SNAKE_CASE，能對應預定義值的就用預定義值——
# 理由與對照表見 vda5050_bridge.py 的 REPLAN_ERRORS 註解（2026/08/14 查證後修正）。
REPLAN_ERRORS = {
    RobotMode.MODE_WAITING: ('BLOCKED_BY_OTHER_ROBOT', 'WARNING',
                             '被其他車輛擋住（slotcar 的 MODE_WAITING）'),
    RobotMode.MODE_ADAPTER_ERROR: ('OUTSIDE_OF_CORRIDOR', 'FATAL',
                                   '收到的計畫與實際位置差距過大'),
}


# ISO8601（YYYY-MM-DDTHH:mm:ss.fffZ）—— schema 的 timestamp 格式
def iso_now():
    now = datetime.now(timezone.utc)
    return now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond // 1000:03d}Z'


# theta 的值域是 [-pi, pi]（mobileRobotPosition 的 schema 限制），
# slotcar 的 yaw 不保證落在這個範圍內
def wrap_pi(yaw):
    return math.atan2(math.sin(yaw), math.cos(yaw))


class VDA5050Vehicle(Node):
    # 建立 MQTT 連線（含 Last Will）與 ROS 收發，然後靠一個定時器推動整台車
    def __init__(self):
        super().__init__('vda5050_vehicle')
        p = self.declare_parameter
        self.manufacturer = p('manufacturer', 'rmfdemos').value
        self.serial = p('serial_number', 'tinyRobot1').value   # ＝車名
        self.fleet = p('fleet_name', 'tinyRobot').value
        self.period = p('state_period_sec', 1.0).value
        self.v_lin = p('nominal_linear_velocity', 0.5).value    # config.yaml 的值
        self.v_ang = p('nominal_angular_velocity', 0.6).value
        self.tolerance = p('arrival_tolerance_m', 0.5).value   # 抵達的容許誤差
        # 場域最遠兩點 15.8m ≈ 32s，實測單趟任務 60–90s；120s 已是寬鬆上限
        self.max_eta = p('max_eta_sec', 120.0).value
        # 車輛認領新 task_id 的寬限時間（車況 10Hz，3 秒已很寬鬆）
        self.adopt_timeout = p('adopt_timeout_sec', 3.0).value
        iface = p('interface_name', 'vda5050').value
        host = p('mqtt_host', 'localhost').value
        port = p('mqtt_port', 1883).value
        schema_path = p('state_schema', '').value               # 空字串＝不驗證
        log_path = p('log_path', '/tmp/vda5050_vehicle.jsonl').value

        base = f'{iface}/{MAJOR}/{self.manufacturer}/{self.serial}'
        self.topic_order = f'{base}/order'
        self.topic_state = f'{base}/state'
        self.topic_conn = f'{base}/connection'

        self.robot = None        # 最近一次 /robot_state（None＝還沒看到車）
        self.order = None        # {'orderId', 'orderUpdateId', 'node', 'sent'}
        self.reached = False     # 目前的 order 是否已抵達
        self.last_node_id = ''   # state 的 lastNodeId
        self.last_node_seq = 0
        self.action_states = []  # 我們不執行 actions，只如實回報 FAILED
        self.order_error = None  # 最近一次 order 被拒的原因（收到新 order 才清掉）
        self.header = {'state': 0, 'connection': 0}
        self.inbox = deque()     # MQTT 執行緒 → ROS 定時器的交接處
        self.last_sig = None     # 上次發布時的「重要欄位」快照
        self.last_pub = 0.0
        self.min_pub_interval = p('min_publish_interval_sec', 0.1).value

        self.log = open(log_path, 'a', buffering=1, encoding='utf-8')
        self.validator = self._load_validator(schema_path)
        # 紀錄的第一行是版本標記，供比對三個元件是否為同一版（見 version.py）
        self._write({'event': 'run_started', 'fleet': self.fleet,
                     'state_period_sec': self.period, **code_version()})

        self.create_subscription(RobotState, '/robot_state',
                                 self.on_robot_state, 100)
        self.path_pub = self.create_publisher(
            PathRequest, '/robot_path_requests', qos_profile_system_default)

        self.mqtt = self._connect_mqtt(host, port)
        # 兩個定時器分工：
        #   inbox 0.1s —— 收到 order 後最多 0.1 秒就送出 PathRequest
        #   heartbeat  —— 沒有任何變化時的保底發布（規範要求至少每 30s 一次）
        # 真正重要的 state 不靠定時器：狀態一變就立刻發（見 _maybe_publish）。
        self.create_timer(0.1, self.on_inbox_tick)
        self.create_timer(self.period, self._publish_state)
        self.get_logger().info(
            f'VDA5050 車輛啟動｜{self.manufacturer}/{self.serial}｜'
            f'broker {host}:{port}｜state 每 {self.period}s｜紀錄 {log_path}')

    # 選用：對照官方 schema 自我檢查每一則 state（步驟 2 的即時版）
    def _load_validator(self, path):
        if not path:
            return None
        import jsonschema
        with open(path, encoding='utf-8') as f:
            schema = json.load(f)
        return jsonschema.validators.validator_for(schema)(schema)

    # 依規範第 1016–1026 行：先設 Last Will，連上後才主動發 ONLINE
    def _connect_mqtt(self, host, port):
        client = mqtt.Client(client_id=f'vda5050_{self.serial}_{int(time.time())}')
        client.will_set(self.topic_conn,
                        self._connection_msg('CONNECTION_BROKEN'),
                        qos=1, retain=True)
        client.on_connect = self.on_mqtt_connect
        client.on_message = self.on_mqtt_message
        client.connect_async(host, port, keepalive=15)
        client.loop_start()      # paho 自己開一條執行緒
        return client

    def _write(self, record):
        record['ts'] = round(time.time(), 3)
        record['serial'] = self.serial
        self.log.write(json.dumps(record, ensure_ascii=False) + '\n')

    # 每則訊息共用的表頭；headerId 依 topic 各自遞增（規範要求）
    def _header(self, topic):
        self.header[topic] += 1
        return {
            'headerId': self.header[topic],
            'timestamp': iso_now(),
            'version': VDA5050_VERSION,
            'manufacturer': self.manufacturer,
            'serialNumber': self.serial,
        }

    def _connection_msg(self, state):
        msg = self._header('connection')
        msg['connectionState'] = state
        return json.dumps(msg)

    # ── MQTT 執行緒的兩個回呼 ────────────────────────────────────
    # 連上（或斷線重連）時觸發：重新訂閱 order，並宣告自己 ONLINE
    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc != 0:
            self.get_logger().error(f'MQTT 連線被拒（rc={rc}）')
            return
        client.subscribe(self.topic_order, qos=1)
        client.publish(self.topic_conn, self._connection_msg('ONLINE'),
                       qos=1, retain=True)
        self.get_logger().info(f'MQTT 已連線｜訂閱 {self.topic_order}')

    # 收到 order 時觸發：只丟進佇列，實際處理留在 ROS 執行緒，避免跨執行緒競爭
    def on_mqtt_message(self, client, userdata, msg):
        self.inbox.append(msg.payload)

    # ── ROS 端 ──────────────────────────────────────────────────
    # slotcar 週期回報（10Hz）：更新車況後**立刻**判定抵達並視需要發 state。
    # 這裡是延遲的關鍵路徑——2026/08/13 實測，原本改在 1 秒定時器裡做，
    # 每段路徑平均多花 0.88 秒，一個任務 12–15 段就是十幾秒的周轉時間。
    def on_robot_state(self, msg):
        if msg.name != self.serial:
            return
        self.robot = msg
        self._drive()
        self._check_arrival()
        self._maybe_publish()

    # 一律以車自己回報的時鐘為準（模擬環境的 /clock 從 0 起算，牆鐘對不上）
    def _robot_time(self):
        if self.robot is None:
            return 0.0
        t = self.robot.location.t
        return t.sec + t.nanosec / 1e9

    # 0.1 秒定時器：把 MQTT 執行緒收到的 order 拿到 ROS 執行緒處理並立刻驅動車輛
    def on_inbox_tick(self):
        if not self.inbox:
            return
        while self.inbox:
            self._handle_order(self.inbox.popleft())
        self._drive()
        self._maybe_publish()

    # 解析 order：檢查必要欄位、依 orderUpdateId 做冪等，取出目標節點
    def _handle_order(self, payload):
        try:
            order = json.loads(payload)
        except json.JSONDecodeError as err:
            self._reject(None, 'VALIDATION_FAILURE', f'JSON 解析失敗：{err}')
            return

        missing = [k for k in ORDER_REQUIRED if k not in order]
        if missing:
            self._reject(order.get('orderId'), 'VALIDATION_FAILURE',
                         f'缺少必要欄位：{",".join(missing)}')
            return
        if order['serialNumber'] != self.serial:
            self.get_logger().warning(
                f"order 的 serialNumber={order['serialNumber']} 不是本車，忽略")
            return

        order_id = order['orderId']
        update_id = order['orderUpdateId']
        if self.order and self.order['orderId'] == order_id \
                and update_id <= self.order['orderUpdateId']:
            # 重送或過期的更新；規範要求丟棄而非重跑
            self.get_logger().info(
                f'丟棄舊的 order 更新（{order_id} #{update_id}）')
            return

        node = self._target_node(order['nodes'])
        if node is None:
            self._reject(order_id, 'ORDER_NOT_EXECUTABLE',
                         'nodes 中沒有帶 nodePosition 的 released 節點')
            return

        self.order = {'orderId': order_id, 'orderUpdateId': update_id,
                      'node': node, 'sent': False, 'sent_at': 0.0,
                      'adopted': False}
        self.reached = False
        self.order_error = None
        self.action_states = self._unsupported_actions(order)
        pos = node['nodePosition']
        self.get_logger().info(
            f"收到 order {order_id} #{update_id} → node {node['nodeId']} "
            f"({pos['x']:.2f}, {pos['y']:.2f}) @ {pos['mapId']}")
        self._write({'event': 'order_received', 'orderId': order_id,
                     'orderUpdateId': update_id, 'nodeId': node['nodeId'],
                     'x': pos['x'], 'y': pos['y'], 'mapId': pos['mapId']})

    # 本專案的簡化：只認最後一個「released 且帶 nodePosition」的節點當目標點
    def _target_node(self, nodes):
        usable = [n for n in nodes
                  if n.get('released') and n.get('nodePosition')]
        return usable[-1] if usable else None

    # 我們不執行任何 action；如實回報 FAILED，不假裝完成
    def _unsupported_actions(self, order):
        states = []
        for item in list(order['nodes']) + list(order['edges']):
            for act in item.get('actions', []):
                states.append({
                    'actionId': act['actionId'],
                    'actionType': act['actionType'],
                    'actionStatus': 'FAILED',
                })
        if states:
            self.get_logger().warning(
                f'order 含 {len(states)} 個 action，本車未實作，回報 FAILED')
        return states

    def _reject(self, order_id, error_type, description):
        self.order_error = {'errorType': error_type, 'errorLevel': 'WARNING',
                            'errorDescription': description}
        if order_id:
            self.order_error['errorReferences'] = [
                {'referenceKey': 'orderId', 'referenceValue': str(order_id)}]
        self.get_logger().error(f'order 被拒（{error_type}）：{description}')
        self._write({'event': 'order_rejected', 'orderId': order_id,
                     'errorType': error_type, 'reason': description})

    # 有 order 但還沒送出路徑時觸發：把目標點翻成 PathRequest 驅動 slotcar。
    # 要等 /robot_state 到齊才能送，因為 path 的第一點必須是車目前的位置。
    def _drive(self):
        if not self.order or self.order['sent'] or self.robot is None:
            return
        pos = self.order['node']['nodePosition']
        cur = self.robot.location
        theta = float(pos.get('theta', cur.yaw))

        dist = math.hypot(pos['x'] - cur.x, pos['y'] - cur.y)
        # 抵達時間估計，算法與 rmf_demos 的 fleet_manager 一致
        eta = dist / self.v_lin + abs(wrap_pi(theta - cur.yaw)) / self.v_ang
        # 量級檢查：office 場域 15.6 × 9.3 m，最遠兩點 15.8 m ≈ 32 秒
        # （見 notes/物理量與環境常數.md §3）。算出幾百秒就是有東西錯了，
        # 與其送出去讓車用荒謬的速度爬，不如當場拒絕並說清楚。
        if eta > self.max_eta:
            self._reject(self.order['orderId'], 'IMPLAUSIBLE_ETA',
                         f'估計 {eta:.0f}s 超過上限 {self.max_eta}s'
                         f'（距離 {dist:.1f}m / {self.v_lin}m/s）——請檢查座標與速度參數')
            self.order['sent'] = True      # 不重試，等下一張 order
            return

        target = Location()
        target.x = float(pos['x'])
        target.y = float(pos['y'])
        target.yaw = theta
        target.level_name = pos['mapId']
        # ⚠️ 時間基準必須取自「車自己回報的時間戳」，不能用 self.get_clock()。
        # 模擬環境跑的是 /clock（開場從 0 起算），節點若沒設 use_sim_time
        # 就會填進牆鐘的 17 億秒——slotcar 會算出趨近於零的速度，
        # 看起來就是「指令送到了、車卻不動」。用車的時鐘則不管誰在跑都對。
        target.t = Time(sec=cur.t.sec + int(eta),
                        nanosec=cur.t.nanosec)

        req = PathRequest()
        req.fleet_name = self.fleet
        req.robot_name = self.serial
        req.path = [cur, target]
        # task_id 直接沿用 orderId：slotcar 會原封不動回報，成為抵達判定的依據
        req.task_id = self.order['orderId']
        self.path_pub.publish(req)
        self.order['sent'] = True
        self.order['sent_at'] = self._robot_time()   # 用車的時鐘記，寬限期才對得上
        # 兩個時間戳一起印：數量級不合時，一眼就看得出來
        self.get_logger().info(
            f"送出 PathRequest → ({target.x:.2f}, {target.y:.2f})｜"
            f"距離 {dist:.2f}m｜預估 {eta:.1f}s｜"
            f"t {cur.t.sec} → {target.t.sec}｜task_id={req.task_id}")

    # 抵達＝停下來、路徑清空、回報的是本張 order，**而且人真的在目標點旁邊**。
    # 少了最後一項會誤判：別人（RMF 的 fleet_manager）把車叫回去時，
    # 車一樣是「停著、path 空」，但它停在原地不是停在目標點。
    def _check_arrival(self):
        if not self.order or not self.order['sent'] or self.reached:
            return
        r = self.robot
        if r is None:
            return
        if r.task_id != self.order['orderId']:
            # 剛送出的那幾則車況還是舊的 task_id，這是正常的傳遞延遲，不是被接管。
            # 等它超過寬限時間仍沒認領，才算真的沒收到；
            # 若曾經認領過又被換掉，那就是有別人在指揮同一台車（例如 fleet_manager）。
            waited = self._robot_time() - self.order['sent_at']
            if self.order['adopted']:
                self._reject(self.order['orderId'], 'OTHER_ORDER_ACTIVE',
                             f'車輛改為執行 task_id={r.task_id}，本張 order 已被接管')
                self.order['adopted'] = False       # 只報一次，不洗版
            elif waited > self.adopt_timeout and self.order_error is None:
                self._reject(self.order['orderId'], 'ORDER_NOT_ACCEPTED',
                             f'送出後 {waited:.1f}s 車輛仍未認領（目前 task_id={r.task_id!r}）')
            return
        self.order['adopted'] = True
        self.order_error = None
        idle = r.mode.mode in (RobotMode.MODE_IDLE, RobotMode.MODE_CHARGING)
        if not (idle and len(r.path) == 0):
            return
        pos = self.order['node']['nodePosition']
        gap = math.hypot(pos['x'] - r.location.x, pos['y'] - r.location.y)
        if gap > self.tolerance:
            self._reject(self.order['orderId'], 'NODE_UNREACHABLE',
                         f'車已停止但距目標 {gap:.2f}m（容許 {self.tolerance}m）')
            return
        self.reached = True
        self.last_node_id = self.order['node']['nodeId']
        self.last_node_seq = self.order['node']['sequenceId']
        self.get_logger().info(f'抵達 node {self.last_node_id}（誤差 {gap:.2f}m）')
        self._write({'event': 'node_reached',
                     'orderId': self.order['orderId'],
                     'nodeId': self.last_node_id,
                     'gap_m': round(gap, 3),
                     'x': round(r.location.x, 2),
                     'y': round(r.location.y, 2)})

    # ── state ───────────────────────────────────────────────────
    # 18 個必要欄位一個不少；沒有資料的用空陣列或預設值，不省略
    def _build_state(self):
        s = self._header('state')
        s['orderId'] = self.order['orderId'] if self.order else ''
        s['orderUpdateId'] = self.order['orderUpdateId'] if self.order else 0
        s['lastNodeId'] = self.last_node_id
        s['lastNodeSequenceId'] = self.last_node_seq
        s['nodeStates'] = ([] if self.reached or not self.order else
                           [{'nodeId': self.order['node']['nodeId'],
                             'sequenceId': self.order['node']['sequenceId'],
                             'released': True,
                             'nodePosition': self.order['node']['nodePosition']}])
        s['edgeStates'] = []           # 單節點 order，本來就沒有 edge
        s['actionStates'] = self.action_states
        s['instantActionStates'] = []
        s['operatingMode'] = 'AUTOMATIC'
        s['safetyState'] = {'activeEmergencyStop': 'NONE',
                            'fieldViolation': False}
        s['errors'] = self._errors()

        r = self.robot
        if r is None:
            # 還沒收到 /robot_state：位置未知，其餘欄位給安全的預設值
            s['driving'] = False
            s['powerSupply'] = {'stateOfCharge': 0.0, 'charging': False}
            return s

        s['driving'] = r.mode.mode == RobotMode.MODE_MOVING
        s['powerSupply'] = {
            'stateOfCharge': round(float(r.battery_percent), 1),
            'charging': r.mode.mode == RobotMode.MODE_CHARGING,
        }
        s['mobileRobotPosition'] = {
            'x': round(float(r.location.x), 3),
            'y': round(float(r.location.y), 3),
            'theta': round(wrap_pi(r.location.yaw), 4),
            'mapId': r.location.level_name,
            'localized': True,       # 模擬環境的位置由 Gazebo 直接給，必然可信
        }
        return s

    # 錯誤來源有二：order 被拒，以及 slotcar 回報需要重新規劃的兩種模式
    def _errors(self):
        errors = [self.order_error] if self.order_error else []
        if self.robot is None:
            errors.append({
                'errorType': 'ROBOT_STATE_UNAVAILABLE',
                'errorLevel': 'WARNING',
                'errorDescription': '尚未收到 /robot_state，位置與電量未知',
            })
            return errors
        entry = REPLAN_ERRORS.get(self.robot.mode.mode)
        if entry:
            errors.append({'errorType': entry[0], 'errorLevel': entry[1],
                           'errorDescription': entry[2]})
        return errors

    # 規範要求 state 在「重要欄位改變時」立即發布，週期發布只是保底。
    # 這個簽章就是「重要欄位」的定義：訂單、抵達的節點、行駛與否、錯誤集合。
    # 位置變化不列入——它每 0.1 秒都在變，交給週期發布即可。
    def _signature(self):
        r = self.robot
        return (
            self.order['orderId'] if self.order else '',
            self.order['orderUpdateId'] if self.order else 0,
            self.last_node_id,
            self.reached,
            r.mode.mode == RobotMode.MODE_MOVING if r else False,
            tuple(e['errorType'] for e in self._errors()),
        )

    # 狀態有變才發，並限制最短間隔避免洗頻（車況 10Hz，這裡最快也是 10Hz）
    def _maybe_publish(self):
        sig = self._signature()
        if sig == self.last_sig:
            return
        if time.time() - self.last_pub < self.min_pub_interval:
            return
        self._publish_state()

    # 發一則 state；QoS 0（可容忍遺失），order/connection 才用 QoS 1
    def _publish_state(self):
        state = self._build_state()
        if self.validator is not None:
            errs = sorted(self.validator.iter_errors(state), key=str)
            for e in errs:
                self.get_logger().error(f'state 不符 schema：{e.message}')
        self.mqtt.publish(self.topic_state, json.dumps(state), qos=0)
        self.last_pub = time.time()
        self.last_sig = self._signature()

    # 關閉前呼叫：依規範主動宣告 OFFLINE，讓對方分辨「正常離線」與「斷線」
    def shutdown(self):
        try:
            self.mqtt.publish(self.topic_conn, self._connection_msg('OFFLINE'),
                              qos=1, retain=True).wait_for_publish()
            self.mqtt.loop_stop()
            self.mqtt.disconnect()
        finally:
            self.log.close()


# 進入點：ros2 run fifo_dispatcher vda5050_vehicle
def main():
    rclpy.init()
    node = VDA5050Vehicle()
    try:
        rclpy.spin(node)
    # Ctrl+C 在 Humble 會以 ExternalShutdownException 現身；
    # 兩者都要接，否則離線流程會被 traceback 打斷、exit code 變成 1
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    # Ctrl+C 會送給整個 process group，ros2 run 會再轉送一次，
    # 於是 context 在關閉過程中第二次被關掉。只有在 context 真的還活著時，
    # 這個例外才代表真正的錯誤。
    except RCLError:
        if rclpy.ok():
            raise
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
