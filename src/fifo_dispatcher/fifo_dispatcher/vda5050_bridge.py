#!/usr/bin/env python3
# M2 步驟 3：VDA5050 橋接（廠商的車隊管理軟體那一層）
#
# 北向：HTTP —— 實作 rmf_demos 的五個端點，**被 RMF 的 fleet_adapter 呼叫**
# 南向：MQTT —— 發 VDA5050 order、收 state
#
# 這支取代 rmf_demos 的 fleet_manager（依原則 11 不改它的原始碼，改成不啟動它）。
# 端點路徑、查詢參數、回應欄位全部照抄 RobotClientAPI.py 的呼叫方式，
# 少一個欄位 adapter 就會取不到值。
#
# ⚠️ 刻意不是 ROS 節點：真實世界的廠商車隊管理軟體不跑在 ROS 上，
#    這條線的價值就在於「兩端只靠標準協定溝通」。代價是 `ros2 node list`
#    看不到它，驗證改用 curl（北向）與 mosquitto_sub（南向）。
#
# cmd_id 與 orderId 的對應：orderId = str(cmd_id)、nodeId = f'cmd_{cmd_id}'。
# 車輛抵達後會把 nodeId 填進 state 的 lastNodeId，那就是「這個 cmd 完成了」的證據。

import argparse
import json
import math
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import paho.mqtt.client as mqtt
import uvicorn
import yaml
from fastapi import FastAPI
from pydantic import BaseModel

from fifo_dispatcher.version import code_version

VDA5050_VERSION = '3.0.0'
MAJOR = 'v' + VDA5050_VERSION.split('.')[0]

# 車輛用這些 errorType 表達「這張 order 走不下去了」（見 vda5050_vehicle.py）。
# VDA5050 沒有 replan 這個概念，這裡就是協定與 RMF 語彙的接縫：
# 只要車說它卡住、被接管、沒收到、或走到一半停了，RMF 都應該重新規劃。
# 缺了任何一項，adapter 會一直空等一個永遠不會完成的 cmd_id。
#
# ⚠️ 命名為何全大寫（2026/08/14 查證規範後修正）：
#   規範對 errorType 的定義是「extensible enumeration including the following
#   predefined values」，預定義值一律 UPPER_SNAKE_CASE。原本這裡用的是自創的
#   camelCase（blockedByOtherRobot…），**schema 驗證照樣會過**（errorType 型別
#   只是 string，沒有 enum 限制），但那是「合 schema、不合規範」——
#   換一台真的廠商車輛，它送 NODE_UNREACHABLE，我們比對 nodeNotReached 就永遠
#   對不上，replan 不會觸發，adapter 空等到逾時。這正好打中 VDA5050 的存在理由。
REPLAN_ERRORS = {
    # ── 規範預定義值 ──────────────────────────────────────────
    'OUTSIDE_OF_CORRIDOR',     # 計畫與實際位置差太遠（slotcar MODE_ADAPTER_ERROR）
    'OTHER_ORDER_ACTIVE',      # 有別人在指揮同一台車
    'NODE_UNREACHABLE',        # 車停了，但沒停在目標點
    'VALIDATION_FAILURE',      # order 格式不合
    # ── 自訂值（規範允許廠商擴充；沿用同樣的命名慣例）──────────
    'BLOCKED_BY_OTHER_ROBOT',  # 被別台車擋住（slotcar MODE_WAITING）
    'ORDER_NOT_ACCEPTED',      # 送出後車輛始終沒認領
    'IMPLAUSIBLE_ETA',         # 估計時間不合量級，車輛拒絕執行
    'ORDER_NOT_EXECUTABLE',    # order 內容不可執行（無對應的預定義值）
}

app = FastAPI()


class Request(BaseModel):
    map_name: Optional[str] = None
    task: Optional[str] = None
    destination: Optional[dict] = None
    data: Optional[dict] = None
    speed_limit: Optional[float] = None
    toggle: Optional[bool] = None


class Response(BaseModel):
    data: Optional[dict] = None
    success: bool
    msg: str


def iso_now():
    now = datetime.now(timezone.utc)
    return now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond // 1000:03d}Z'


# theta 的值域是 [-pi, pi]（order schema 的 nodePosition 限制）
def wrap_pi(yaw):
    return math.atan2(math.sin(yaw), math.cos(yaw))


class Bridge:
    # 建立 MQTT 連線、訂閱每台車的 state，並掛上五個 HTTP 端點
    def __init__(self, config, args):
        fleet = config['rmf_fleet']
        self.fleet_name = fleet['name']
        self.v_lin = fleet['limits']['linear'][0]
        self.v_ang = fleet['limits']['angular'][0]
        self.manufacturer = args.manufacturer
        self.interface = args.interface_name
        self.lock = threading.Lock()      # MQTT 執行緒與 HTTP 執行緒共用 robots
        self.log = open(args.log_path, 'a', buffering=1, encoding='utf-8')

        self.robots = {name: {
            'state': None,         # 最近一則 VDA5050 state
            'cmd': None,           # {'cmd_id', 'x', 'y', 'yaw', 'node_id'}
            'last_completed': None,
            'teleop': False,
            'header': 0,
        } for name in config['robots']}

        # 紀錄的第一行是版本標記，供比對三個元件是否為同一版（見 version.py）
        self._write({'event': 'run_started', 'fleet': self.fleet_name,
                     'robots': list(self.robots), **code_version()})

        self.mqtt = mqtt.Client(client_id=f'vda5050_bridge_{int(time.time())}')
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_message = self._on_message
        self.mqtt.connect_async(args.mqtt_host, args.mqtt_port, keepalive=15)
        self.mqtt.loop_start()

        self._routes()
        print(f'VDA5050 bridge｜車隊 {self.fleet_name}｜車輛 {list(self.robots)}｜'
              f'broker {args.mqtt_host}:{args.mqtt_port}｜紀錄 {args.log_path}',
              flush=True)

    def _topic(self, robot, kind):
        return f'{self.interface}/{MAJOR}/{self.manufacturer}/{robot}/{kind}'

    def _write(self, record):
        record['ts'] = round(time.time(), 3)
        self.log.write(json.dumps(record, ensure_ascii=False) + '\n')

    # ── MQTT 執行緒 ─────────────────────────────────────────────
    # 連上（或重連）時觸發：訂閱每一台車的 state
    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            print(f'MQTT 連線被拒（rc={rc}）', flush=True)
            return
        for robot in self.robots:
            client.subscribe(self._topic(robot, 'state'), qos=0)
        print(f'MQTT 已連線｜訂閱 {len(self.robots)} 台車的 state', flush=True)

    # 收到 state 時觸發：更新車況，並判斷目前的 cmd 是否已完成。
    # ⚠️ 鎖只圈住 dict 的更新。JSON 解析、寫檔、印訊息全部留在鎖外——
    #    這條路徑每秒會被呼叫二十幾次，鎖握得越久，HTTP 那側被擋得越久。
    def _on_message(self, client, userdata, msg):
        try:
            state = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        robot = state.get('serialNumber')
        if robot not in self.robots:
            return
        done = None
        with self.lock:
            r = self.robots[robot]
            r['state'] = state
            cmd = r['cmd']
            # 完成的判定：車回報的 lastNodeId 就是我們這次下的節點
            if cmd and state.get('lastNodeId') == cmd['node_id']:
                r['last_completed'] = cmd['cmd_id']
                r['cmd'] = None
                done = cmd['cmd_id']
        if done is not None:
            self._write({'event': 'cmd_completed', 'robot': robot,
                         'cmd_id': done})
            print(f'[{robot}] cmd {done} 完成', flush=True)

    # 把一個目標點包成單節點 order 發出去（本專案的簡化，見設計文件 §5）
    def _send_order(self, robot, cmd_id, x, y, yaw, map_name):
        r = self.robots[robot]
        r['header'] += 1
        node_id = f'cmd_{cmd_id}'
        order = {
            'headerId': r['header'],
            'timestamp': iso_now(),
            'version': VDA5050_VERSION,
            'manufacturer': self.manufacturer,
            'serialNumber': robot,
            'orderId': str(cmd_id),
            'orderUpdateId': 0,
            'nodes': [{
                'nodeId': node_id,
                'sequenceId': 0,
                'released': True,
                'actions': [],
                'nodePosition': {'x': float(x), 'y': float(y),
                                 'theta': wrap_pi(float(yaw)),
                                 'mapId': map_name},
            }],
            'edges': [],
        }
        self.mqtt.publish(self._topic(robot, 'order'), json.dumps(order), qos=1)
        r['cmd'] = {'cmd_id': cmd_id, 'x': float(x), 'y': float(y),
                    'yaw': float(yaw), 'node_id': node_id}
        self._write({'event': 'order_sent', 'robot': robot, 'cmd_id': cmd_id,
                     'x': float(x), 'y': float(y), 'mapId': map_name})
        print(f'[{robot}] 發出 order {cmd_id} → ({x:.2f}, {y:.2f}) @ {map_name}',
              flush=True)

    # 組出 adapter 要的七個欄位（格式取自 fleet_manager.get_robot_state）
    def _robot_data(self, robot):
        r = self.robots[robot]
        state = r['state']
        if state is None or 'mobileRobotPosition' not in state:
            return None            # 車還沒回報過，照 fleet_manager 的行為回 success=False
        pos = state['mobileRobotPosition']
        data = {
            'robot_name': robot,
            'map_name': pos['mapId'],
            'position': {'x': pos['x'], 'y': pos['y'], 'yaw': pos['theta']},
            'battery': state['powerSupply']['stateOfCharge'],   # 0–100
            'destination_arrival': None,
            'last_completed_request': r['last_completed'],
            'replan': any(e.get('errorType') in REPLAN_ERRORS
                          for e in state.get('errors', [])),
        }
        cmd = r['cmd']
        if cmd is not None:
            # 剩餘時間估計，算法與 fleet_manager 一致（設計文件 §9.1）
            dist = math.hypot(cmd['x'] - pos['x'], cmd['y'] - pos['y'])
            ori = abs(wrap_pi(cmd['yaw'] - pos['theta']))
            data['destination_arrival'] = {
                'cmd_id': cmd['cmd_id'],
                'duration': dist / self.v_lin + ori / self.v_ang,
            }
        return data

    # ── 五個 HTTP 端點 ──────────────────────────────────────────
    # RobotClientAPI 呼叫時帶／不帶結尾斜線都有，兩種都註冊，不倚賴轉址
    def _routes(self):
        bridge = self

        @app.get('/open-rmf/rmf_demos_fm/status/', response_model=Response)
        @app.get('/open-rmf/rmf_demos_fm/status', response_model=Response)
        def status(robot_name: Optional[str] = None):
            with bridge.lock:
                if robot_name is None:
                    everyone = [bridge._robot_data(n) for n in bridge.robots]
                    if any(d is None for d in everyone):
                        return {'data': {}, 'success': False,
                                'msg': '尚有車輛未回報 state'}
                    return {'data': {'all_robots': everyone},
                            'success': True, 'msg': ''}
                if robot_name not in bridge.robots:
                    return {'data': {}, 'success': False,
                            'msg': f'未知的車輛 {robot_name}'}
                data = bridge._robot_data(robot_name)
                if data is None:
                    return {'data': {}, 'success': False,
                            'msg': f'{robot_name} 尚未回報 state'}
                return {'data': data, 'success': True, 'msg': ''}

        @app.post('/open-rmf/rmf_demos_fm/navigate/', response_model=Response)
        @app.post('/open-rmf/rmf_demos_fm/navigate', response_model=Response)
        def navigate(robot_name: str, cmd_id: int, dest: Request):
            if robot_name not in bridge.robots or not dest.destination:
                return {'success': False, 'msg': '車輛不存在或缺少 destination'}
            d = dest.destination
            with bridge.lock:
                bridge._send_order(robot_name, cmd_id, d['x'], d['y'], d['yaw'],
                                   dest.map_name)
            msg = ''
            if dest.speed_limit:
                # 單節點 order 沒有 edge，speed_limit 無處可放——說出來，不假裝支援
                msg = f'speed_limit={dest.speed_limit} 未實作（單節點 order 無 edge）'
            return {'success': True, 'msg': msg}

        @app.get('/open-rmf/rmf_demos_fm/stop_robot/', response_model=Response)
        @app.get('/open-rmf/rmf_demos_fm/stop_robot', response_model=Response)
        def stop_robot(robot_name: str, cmd_id: int):
            if robot_name not in bridge.robots:
                return {'success': False, 'msg': f'未知的車輛 {robot_name}'}
            with bridge.lock:
                state = bridge.robots[robot_name]['state']
                if state is None or 'mobileRobotPosition' not in state:
                    return {'success': False, 'msg': '尚未收到 state，不知道車在哪'}
                p = state['mobileRobotPosition']
                # 停車＝下一個目標點就是現在的位置（見設計文件 §5 的簡化）
                bridge._send_order(robot_name, cmd_id, p['x'], p['y'],
                                   p['theta'], p['mapId'])
            return {'success': True, 'msg': ''}

        @app.post('/open-rmf/rmf_demos_fm/start_task/', response_model=Response)
        @app.post('/open-rmf/rmf_demos_fm/start_task', response_model=Response)
        def start_task(robot_name: str, cmd_id: int, task: Request):
            # docking 需要 DockSummary（ROS topic），而本橋接刻意不接 ROS。
            # 本專案只跑 patrol 任務，不會走到這裡；如實回報未實作。
            bridge._write({'event': 'start_task_rejected', 'robot': robot_name,
                           'cmd_id': cmd_id, 'task': task.task})
            return {'success': False,
                    'msg': 'start_task（docking）未實作：本橋接不接 ROS，取不到 DockSummary'}

        @app.post('/open-rmf/rmf_demos_fm/toggle_action/', response_model=Response)
        @app.post('/open-rmf/rmf_demos_fm/toggle_action', response_model=Response)
        def toggle_action(robot_name: str, mode: Request):
            if robot_name not in bridge.robots:
                return {'success': False, 'msg': f'未知的車輛 {robot_name}'}
            with bridge.lock:
                bridge.robots[robot_name]['teleop'] = bool(mode.toggle)
            return {'success': True, 'msg': ''}


# 進入點：ros2 run fifo_dispatcher vda5050_bridge -c <config.yaml>
def main(argv=sys.argv):
    parser = argparse.ArgumentParser(
        prog='vda5050_bridge',
        description='VDA5050 橋接：北向 HTTP（被 RMF 呼叫）、南向 MQTT（對車輛）')
    parser.add_argument('-c', '--config_file', required=True,
                        help='與 fleet_adapter 相同的 config.yaml')
    parser.add_argument('--mqtt_host', default='localhost')
    parser.add_argument('--mqtt_port', type=int, default=1883)
    parser.add_argument('--manufacturer', default='rmfdemos')
    parser.add_argument('--interface_name', default='vda5050')
    parser.add_argument('--log_path', default='/tmp/vda5050_bridge.jsonl')
    args = parser.parse_args(argv[1:])

    with open(args.config_file, encoding='utf-8') as f:
        config = yaml.safe_load(f)

    bridge = Bridge(config, args)
    fm = config['rmf_fleet']['fleet_manager']
    # ⚠️ timeout_graceful_shutdown 不能省：fleet_adapter 會保持 keep-alive 連線，
    #    uvicorn 預設會等到連線自己關閉才退出，等不到就變成殺不掉的孤兒行程
    #    （2026/08/13 實測：一個殘留的 bridge 佔住 22011，害後面兩組實驗中止）。
    server = uvicorn.Server(uvicorn.Config(
        app, host=fm['ip'], port=fm['port'], log_level='warning',
        timeout_graceful_shutdown=3))
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.mqtt.loop_stop()
        bridge.mqtt.disconnect()
        bridge.log.close()


if __name__ == '__main__':
    main(sys.argv)
