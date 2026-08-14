#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
# M3b 上位系統派工器
#
# 角色：FMS/WCS 上位系統——只決定「派哪台車」，不負責機器人怎麼走。
#   任務來源（本節點產生）→ 派工策略決定車輛 → 發布 robot_task_request
#   → RMF fleet adapter 執行（路徑規劃、交通協商、開門、充電）
#
# 與 M3a 的 shadow_bidder 不同：這支會真的發布任務，並量測真實周轉時間。
#
# 時間軸一律使用「模擬時鐘」（取自 /fleet_states 的 location.t），
# 因為實測 RTF ≈ 0.9，牆鐘與模擬時鐘會漂移。

import json
import math
import time
import uuid

import rclpy
import yaml
from rclpy._rclpy_pybind11 import RCLError
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import QoSProfile
from rclpy.qos import QoSDurabilityPolicy as Durability
from rclpy.qos import QoSHistoryPolicy as History
from rclpy.qos import QoSReliabilityPolicy as Reliability

from rmf_fleet_msgs.msg import FleetState
from rmf_task_msgs.msg import ApiRequest, ApiResponse

from fifo_dispatcher.version import code_version

DEFAULT_NAV_GRAPH = ('/root/rmf_ws/install/rmf_demos_maps/share/rmf_demos_maps/'
                     'maps/office/nav_graphs/0.yaml')

# 訂閱端使用 RELIABLE + TRANSIENT_LOCAL；發布端若不一致會靜默收不到
API_QOS = QoSProfile(
    history=History.KEEP_LAST,
    depth=10,
    reliability=Reliability.RELIABLE,
    durability=Durability.TRANSIENT_LOCAL,
)


class Dispatcher(Node):
    # 產生任務 → 依策略選車 → 發布 robot_task_request → 量測周轉時間
    def __init__(self):
        super().__init__('fifo_dispatcher')
        p = self.declare_parameter
        # fifo    ：取閒置最久的車（本專案的基準策略）
        # nearest ：取離目的地最近的車
        # rmf     ：不指定車，改發 dispatch_task_request 讓 RMF 投標自選（對照基準組）
        self.policy = p('policy', 'fifo').value
        self.fleet = p('fleet', 'tinyRobot').value
        self.places = [s.strip() for s in
                       p('places', 'pantry,lounge,supplies,patrol_A1,patrol_B'
                         ).value.split(',') if s.strip()]
        self.total = p('count', 10).value                  # 要產生幾個任務
        self.interval = p('interval_sec', 30.0).value       # 每隔幾秒產生一個
        self.timeout = p('assign_timeout_sec', 180.0).value  # 逾時未開始即判定失敗
        log_path = p('log_path', '/tmp/fifo_dispatch.jsonl').value
        self.graph = self._load_places(p('nav_graph', DEFAULT_NAV_GRAPH).value)

        self.log = open(log_path, 'a', buffering=1, encoding='utf-8')
        self.sim_now = 0.0        # 最近一次 /fleet_states 回報的模擬時間
        self.robots = {}          # {車名: {task_id, pos, battery}}
        self.idle_since = {}      # {車名: 進入閒置的模擬時間} —— FIFO 的依據
        self.reserved = {}        # {車名: 我方 task_id} —— 已指派但車尚未動起來
        self.queue = []           # 等待指派的任務（FIFO 順序）
        self.inflight = {}        # {task_id: 紀錄}
        self.created = 0
        self.t_start = None       # 任務排程的計時起點；第一次看到車隊時才設定

        # 紀錄的第一行是版本標記，不是任務——M4 讀檔時要先濾掉 event 欄位
        # （`df = df[df.get('event').isna()]`），否則會多出一列空任務
        self._write({'event': 'run_started', 'policy': self.policy,
                     'fleet': self.fleet, 'count': self.total,
                     'interval_sec': self.interval, **code_version()})

        self.create_subscription(FleetState, '/fleet_states', self.on_fleet_state, 10)
        self.create_subscription(ApiResponse, '/task_api_responses',
                                 self.on_api_response, API_QOS)
        self.pub = self.create_publisher(ApiRequest, '/task_api_requests', API_QOS)
        self.create_timer(1.0, self.on_tick)

        self.get_logger().info(
            f'派工器啟動｜策略={self.policy}｜車隊={self.fleet}｜'
            f'任務 {self.total} 個 / 每 {self.interval}s｜紀錄 {log_path}')

    # 讀導航圖取具名地點座標；nearest 策略與距離欄位需要
    def _load_places(self, path):
        try:
            g = yaml.safe_load(open(path))
        except OSError as err:
            self.get_logger().warning(f'讀不到導航圖，距離欄位將為 null：{err}')
            return {}
        return {v[2]['name']: (v[0], v[1])
                for v in g['levels']['L1']['vertices'] if v[2].get('name')}

    def _write(self, record):
        self.log.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _distance(self, robot, place):
        if place not in self.graph or robot not in self.robots:
            return None
        px, py = self.graph[place]
        rx, ry = self.robots[robot]['pos']
        return round(math.hypot(px - rx, py - ry), 2)

    # 可用車＝真實閒置、且未被本節點預約的
    def _available(self):
        return [n for n, r in self.robots.items()
                if r['task_id'] == '' and n not in self.reserved]

    # ── 派工策略 ───────────────────────────────────────────────
    # M5 若要加入新演算法（SPT、匈牙利、OR-Tools），在此擴充即可，
    # 其餘流程（產生任務、發布、量測）完全不必改動。
    def _choose(self, candidates, place):
        if self.policy == 'nearest':
            known = {n: self._distance(n, place) for n in candidates}
            known = {n: d for n, d in known.items() if d is not None}
            if known:
                pick = min(known, key=known.get)
                return pick, f'距離最近（{pick}={known[pick]}m）'
        # 預設 FIFO：取閒置最久的那台，不看距離、電量、位置
        pick = min(candidates, key=lambda n: self.idle_since.get(n, 0.0))
        idle = round(self.sim_now - self.idle_since.get(pick, self.sim_now), 1)
        return pick, f'閒置最久（{pick}={idle}s）'

    # 每秒觸發：依排程產生新任務，然後嘗試把佇列中的任務指派出去
    def on_tick(self):
        if not self.robots:
            return                      # 尚未收到車隊狀態，先不動作
        # 計時起點設在「第一次看到車隊」而非節點啟動，
        # 否則場景較慢啟動時，前幾個任務會被壓縮成連續產生，
        # 各組的任務到達節奏就不一致，比較即失效。
        if self.t_start is None:
            self.t_start = time.time()
        if self.created < self.total:
            due = self.created * self.interval
            if time.time() - self.t_start >= due:
                self._create_task()
        self._drain_queue()
        self._check_timeouts()

    # 產生一個任務並放入佇列；目的地依序輪替，確保各策略的序列完全相同
    def _create_task(self):
        place = self.places[self.created % len(self.places)]
        task_id = f'fifo_{uuid.uuid4()}'
        self.queue.append({
            'task_id': task_id,
            'seq': self.created,
            'place': place,
            'policy': self.policy,
            't_created': round(self.sim_now, 2),
            'wall_created': round(time.time(), 3),
        })
        self.created += 1
        self.get_logger().info(
            f'[{self.created}/{self.total}] 產生任務 → {place}（佇列 {len(self.queue)}）')

    # 有可用車時，依 FIFO 順序把佇列前端的任務指派出去。
    # rmf 模式不需要挑車，任務一產生就交給 RMF，由它自行投標與排隊。
    def _drain_queue(self):
        while self.queue:
            if self.policy == 'rmf':
                task = self.queue.pop(0)
                self._assign(task, None, 'RMF 自行投標選車', list(self.robots))
                continue
            candidates = self._available()
            if not candidates:
                return
            task = self.queue[0]
            robot, reason = self._choose(candidates, task['place'])
            self.queue.pop(0)
            self._assign(task, robot, reason, candidates)

    # 發布 robot_task_request，並記下決策當下所有候選車的狀態
    def _assign(self, task, robot, reason, candidates):
        task['robot'] = robot
        task['reason'] = reason
        task['t_assigned'] = round(self.sim_now, 2)
        task['wait_assign'] = round(task['t_assigned'] - task['t_created'], 2)
        task['candidates'] = {
            n: {
                'idle_sec': round(self.sim_now - self.idle_since.get(n, self.sim_now), 1),
                'dist': self._distance(n, task['place']),
                'battery': self.robots[n]['battery'],
            } for n in candidates
        }
        # 選中的車比「當下最近的車」多走幾公尺；策略優劣的直接指標
        dists = {n: c['dist'] for n, c in task['candidates'].items()
                 if c['dist'] is not None}
        task['dist_penalty'] = (round(dists[robot] - min(dists.values()), 2)
                                if dists and robot in dists else None)

        payload = {
            'type': 'robot_task_request' if robot else 'dispatch_task_request',
            'request': {
                'unix_millis_earliest_start_time': 0,
                'category': 'patrol',
                'description': {'places': [task['place']], 'rounds': 1},
            },
        }
        if robot:
            payload['robot'] = robot
            payload['fleet'] = self.fleet

        msg = ApiRequest()
        msg.request_id = task['task_id']
        msg.json_msg = json.dumps(payload)
        self.pub.publish(msg)

        # rmf 模式下車輛由 RMF 決定，此時不預約任何車
        if robot:
            self.reserved[robot] = task['task_id']
        self.inflight[task['task_id']] = task
        self.get_logger().info(
            f"指派 {task['place']} → {robot or '（RMF 決定）'}｜{reason}")

    # RMF 回覆請求結果時觸發。
    # ① 被拒絕 → 立即結案，避免資料靜默遺失
    # ② rmf 模式 → 回應裡才有 RMF 指定的 booking id，須改用它追蹤車輛狀態
    def on_api_response(self, msg):
        task = self.inflight.get(msg.request_id)
        if task is None:
            return
        try:
            body = json.loads(msg.json_msg)
        except json.JSONDecodeError:
            return

        if not body.get('success', True):
            task['status'] = 'rejected'
            task['errors'] = body.get('errors')
            self._finish(task)
            return

        booking = body.get('state', {}).get('booking', {}).get('id')
        if booking and booking != task['task_id']:
            # /fleet_states 顯示的是 booking id，因此以它為追蹤鍵
            task['request_id'] = task['task_id']
            task['task_id'] = booking
            self.inflight.pop(msg.request_id, None)
            self.inflight[booking] = task

    # /fleet_states 週期送達：更新車輛狀態，並偵測 task_id 的轉換
    def on_fleet_state(self, msg):
        for r in msg.robots:
            self.sim_now = r.location.t.sec + r.location.t.nanosec / 1e9
            prev = self.robots.get(r.name, {}).get('task_id')
            self.robots[r.name] = {
                'task_id': r.task_id,
                'pos': [round(r.location.x, 2), round(r.location.y, 2)],
                'battery': round(r.battery_percent, 1),
            }
            if r.task_id == '':
                self.idle_since.setdefault(r.name, self.sim_now)
            else:
                self.idle_since.pop(r.name, None)
            if prev is not None and r.task_id != prev:
                self._on_transition(r.name, prev, r.task_id)

    # task_id 改變時觸發：新值＝我方任務代表開始，舊值＝我方任務代表結束
    def _on_transition(self, robot, old, new):
        started = self.inflight.get(new)
        if started is not None and 't_started' not in started:
            started['t_started'] = round(self.sim_now, 2)
            started['wait_sec'] = round(started['t_started'] - started['t_created'], 2)
            # rmf 模式在此才知道 RMF 選了哪一台
            if started.get('robot') is None:
                started['robot'] = robot

        done = self.inflight.get(old)
        if done is not None:
            done['t_done'] = round(self.sim_now, 2)
            done['turnaround_sec'] = round(done['t_done'] - done['t_created'], 2)
            done['status'] = 'completed'
            self.reserved.pop(robot, None)
            self._finish(done)

    # rmf 模式沒有預約車輛，逾時檢查僅適用於已指定車的策略
    def _release(self, task):
        if task.get('robot'):
            self.reserved.pop(task['robot'], None)

    # 逾時仍未開始執行者判定失敗；避免資料靜默遺失（M3a 的教訓）
    def _check_timeouts(self):
        for task in list(self.inflight.values()):
            if 't_started' in task or 't_assigned' not in task:
                continue
            if self.sim_now - task['t_assigned'] > self.timeout:
                task['status'] = 'timeout_not_started'
                self._release(task)
                self._finish(task)

    # 寫檔並從追蹤中移除；無論成功、拒絕或逾時都會留下紀錄
    def _finish(self, task):
        self.inflight.pop(task['task_id'], None)
        self._write(task)
        mark = {'completed': '✓', 'rejected': '✗拒絕',
                'timeout_not_started': '✗逾時'}.get(task['status'], '?')
        extra = (f"｜等待 {task.get('wait_sec')}s"
                 f"｜周轉 {task.get('turnaround_sec')}s"
                 if task['status'] == 'completed' else '')
        self.get_logger().info(
            f"{mark} {task['place']} @ {task.get('robot')}{extra}"
            f"｜剩餘 {len(self.queue)} 排隊 / {len(self.inflight)} 執行中")

    # 關閉前呼叫：把仍在佇列或執行中的任務也記錄下來，不讓資料消失
    def flush(self):
        for task in list(self.inflight.values()):
            task['status'] = task.get('status', 'incomplete_at_shutdown')
            self._write(task)
        for task in self.queue:
            task['status'] = 'never_assigned'
            self._write(task)


# 進入點：ros2 run fifo_dispatcher dispatcher
def main():
    rclpy.init()
    node = Dispatcher()
    try:
        rclpy.spin(node)
    # Ctrl+C 在 Humble 會以 ExternalShutdownException 現身；
    # 只接 KeyboardInterrupt 的話，收尾會被 traceback 打斷、結束碼變成 1
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    # Ctrl+C 送給整個 process group，ros2 run 會再轉送一次，
    # context 因此在關閉過程中被第二次關掉。只有 context 還活著時才是真的錯誤。
    except RCLError:
        if rclpy.ok():
            raise
    finally:
        node.flush()
        node.log.close()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
