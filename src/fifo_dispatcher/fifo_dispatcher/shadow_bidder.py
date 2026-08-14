#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
# 影子投標器（M3a）
# 旁聽 Open-RMF 的派工過程，同時用 FIFO 算出「我會派哪一台」，
# 兩者配對後寫成 JSON Lines。只訂閱、不發布，因此完全不影響現有系統運作。
#
# FIFO 的完整定義（兩個層次，缺一不可）：
#   ① 任務佇列——沒有車可用時，任務排隊等待，不是棄權
#   ② 挑車規則——有車可用時，取閒置最久的那台，不看距離、電量、位置
#
# 紀錄的重點不只是「選了誰」，而是「為什麼、等了多久」——每筆都存下決策當下
# 所有候選車的閒置時間、位置、距離與電量，讓決策可被事後檢驗。

import json
import math
import time

import rclpy
import yaml
from rclpy._rclpy_pybind11 import RCLError
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from rmf_fleet_msgs.msg import FleetState
from rmf_task_msgs.msg import BidNotice, BidResponse, DispatchStates

# DispatchState.status 的最終狀態值：任務已交付給車隊
STATUS_DISPATCHED = 3

DEFAULT_NAV_GRAPH = ('/root/rmf_ws/install/rmf_demos_maps/share/rmf_demos_maps/'
                     'maps/office/nav_graphs/0.yaml')

# FIFO 指派後的虛擬工時模型。
# 影子模式下 RMF 可能把任務派給別台車，被 FIFO 選中的車在現實中不會變忙，
# 因此不能用真實車輛狀態來釋放 FIFO 的佔用，必須自行估算工時。
NOMINAL_SPEED = 0.5    # m/s，取自 tinyRobot 的 slotcar 設定（nominal drive speed）
TASK_OVERHEAD = 10.0   # 秒，起停與作業的固定額外時間
DEFAULT_TASK_SEC = 60.0  # 秒，無距離資料時的預設工時


class ShadowBidder(Node):
    # 訂閱四個 topic：閒置狀態、招標、投標回應、最終指派結果。
    def __init__(self):
        super().__init__('shadow_bidder')
        path = self.declare_parameter('log_path', '/tmp/fifo_shadow.jsonl').value
        nav_path = self.declare_parameter('nav_graph', DEFAULT_NAV_GRAPH).value
        # 行緩衝：每寫一行立即落地，節點被 Ctrl+C 時不會遺失已完成的紀錄
        self.log = open(path, 'a', buffering=1, encoding='utf-8')
        self.places = self._load_places(nav_path)

        self.idle_since = {}  # {機器人: 開始閒置的時間} —— 真實車隊狀態
        self.robots = {}      # {機器人: {pos, battery}} —— 最近一次回報的狀態
        self.pending = {}     # {task_id: 尚未寫檔的決策紀錄}

        # FIFO 自己的世界觀：它指派出去的車在估算的工時內視為忙碌，
        # 任務排不到車就進佇列
        self.fifo_busy = {}   # {機器人: 預估可再接單的時間戳}
        self.queue = []       # 等待中的 task_id，依到達順序

        self.create_subscription(FleetState, '/fleet_states', self.on_fleet_state, 10)
        self.create_subscription(BidNotice, '/rmf_task/bid_notice', self.on_bid_notice, 10)
        self.create_subscription(BidResponse, '/rmf_task/bid_response', self.on_bid_response, 10)
        self.create_subscription(DispatchStates, '/dispatch_states', self.on_dispatch_states, 10)

        self.get_logger().info(
            f'shadow_bidder 已啟動｜地點 {len(self.places)} 個｜紀錄寫入 {path}')

    # 讀導航圖，取出具名地點的座標；供計算「車離目的地多遠」使用
    def _load_places(self, nav_path):
        try:
            graph = yaml.safe_load(open(nav_path))
        except OSError as err:
            self.get_logger().warning(f'讀不到導航圖，距離欄位將為 null：{err}')
            return {}
        vertices = graph['levels']['L1']['vertices']
        return {v[2]['name']: (v[0], v[1]) for v in vertices if v[2].get('name')}

    # 從任務描述取出目的地名稱：patrol 用第一個 place，clean 用 zone
    def _target(self, req):
        desc = req.get('description', {})
        if req.get('category') == 'patrol':
            places = desc.get('places') or []
            return places[0] if places else None
        return desc.get('zone')

    # 直線距離（非實際路徑長度），僅用於判斷「哪台車明顯比較近」
    def _distance(self, robot, target):
        if target not in self.places or robot not in self.robots:
            return None
        tx, ty = self.places[target]
        rx, ry = self.robots[robot]['pos']
        return round(math.hypot(tx - rx, ty - ry), 2)

    # FIFO 眼中可用的車＝真實閒置、且先前指派的估算工時已結束的
    def _available(self, now):
        return [n for n in self.idle_since if self.fifo_busy.get(n, 0.0) <= now]

    # /fleet_states 週期送達時觸發：
    # 維護真實閒置狀態與各車位置；車輛回到閒置時，視為 FIFO 指派的工作也完成
    def on_fleet_state(self, msg):
        now = time.time()
        for robot in msg.robots:
            self.robots[robot.name] = {
                'pos': [round(robot.location.x, 2), round(robot.location.y, 2)],
                'battery': round(robot.battery_percent, 1),
            }
            if robot.task_id == '':
                self.idle_since.setdefault(robot.name, now)
            else:
                self.idle_since.pop(robot.name, None)
        self._drain_queue()

    # dispatcher 發出招標時觸發：
    # 蒐集候選車狀態 → 有車可用就立刻指派，沒車就排隊等待
    def on_bid_notice(self, msg):
        now = time.time()
        req = json.loads(msg.request)
        target = self._target(req)

        # 候選＝當下閒置的車。同時記錄 FIFO 依據（idle_sec）與它刻意忽略的資訊（dist）
        candidates = {
            name: {
                'idle_sec': round(now - since, 1),
                'pos': self.robots.get(name, {}).get('pos'),
                'dist': self._distance(name, target),
                'battery': self.robots.get(name, {}).get('battery'),
            }
            for name, since in self.idle_since.items()
        }

        record = {
            'task_id': msg.task_id,
            'category': req.get('category'),
            'target': target,
            'description': req.get('description'),
            't_notice': round(now, 3),
            'candidates': candidates,
            'queue_len_at_notice': len(self.queue),
            '_rmf_done': False,
            '_fifo_done': False,
        }
        self.pending[msg.task_id] = record

        available = self._available(now)
        if available:
            self._assign(record, self._longest_idle(available), now)
        else:
            self.queue.append(msg.task_id)
            record['fifo_status'] = 'queued'
            record['fifo_reason'] = f'無可用車輛，進入佇列（第 {len(self.queue)} 位）'

    # FIFO 的挑車規則：閒置最久的那台。
    # 不看距離、不看電量、不看位置——這個「刻意的笨」正是 M5 要拿來對照的基準。
    def _longest_idle(self, names):
        return min(names, key=lambda n: self.idle_since[n])

    # 指派：寫入人選、等待時間與理由，並依估算工時佔用該車
    def _assign(self, record, robot, now):
        wait = round(now - record['t_notice'], 1)
        dist = record['candidates'].get(robot, {}).get('dist')
        est = DEFAULT_TASK_SEC if dist is None else dist / NOMINAL_SPEED + TASK_OVERHEAD

        record['fifo_choice'] = robot
        record['fifo_wait_sec'] = wait
        record['fifo_status'] = 'immediate' if wait < 0.5 else 'from_queue'
        record['fifo_reason'] = self._reason(record, robot, wait)
        record['fifo_est_sec'] = round(est, 1)
        record['_fifo_done'] = True
        self.fifo_busy[robot] = now + est
        self._maybe_write(record)

    # 決策理由：說明「為什麼是這台」，數值與措辭必須一致
    def _reason(self, record, robot, wait):
        if wait >= 0.5:
            return f'佇列等待 {wait}s 後，{robot} 空出'
        cands = record['candidates']
        if len(cands) <= 1:
            return f'僅 {robot} 可用，無選擇空間'
        top = cands[robot]['idle_sec']
        others = {n: c['idle_sec'] for n, c in cands.items() if n != robot}
        # 顯示精度是 0.1 秒；差距小於此值時不可寫成「大於」，否則理由與數字矛盾
        if all(abs(top - v) < 0.1 for v in others.values()):
            return f'閒置時間相同（皆 {top}s），取佇列順序最前者 {robot}'
        detail = ', '.join(f'{n}={v}s' for n, v in others.items())
        return f'閒置最久（{robot}={top}s > {detail}）'

    # 有車空出時觸發：依序把佇列中最早的任務指派出去
    def _drain_queue(self):
        now = time.time()
        while self.queue:
            available = self._available(now)
            if not available:
                return
            task_id = self.queue.pop(0)
            record = self.pending.get(task_id)
            if record is not None:
                self._assign(record, self._longest_idle(available), now)

    # 車隊回覆投標時觸發：補上 RMF 的建議人選與成本原值
    def on_bid_response(self, msg):
        record = self.pending.get(msg.task_id)
        if record and msg.has_proposal:
            record['rmf_choice'] = msg.proposal.expected_robot_name
            record['rmf_prev_cost'] = round(msg.proposal.prev_cost, 6)
            record['rmf_new_cost'] = round(msg.proposal.new_cost, 6)
            record['rmf_delta_cost'] = round(
                msg.proposal.new_cost - msg.proposal.prev_cost, 6)

    # /dispatch_states 週期送出「所有任務」的完整快照時觸發。
    # 只在任務首次進入 DISPATCHED 時記錄結果，重複的快照會被 _rmf_done 擋掉。
    def on_dispatch_states(self, msg):
        for state in list(msg.active) + list(msg.finished):
            record = self.pending.get(state.task_id)
            if record is None or record['_rmf_done']:
                continue
            if state.status != STATUS_DISPATCHED or not state.assignment.is_assigned:
                continue
            record['final_choice'] = state.assignment.expected_robot_name
            record['t_settled'] = round(time.time(), 3)
            record['_rmf_done'] = True
            self._maybe_write(record)

    # FIFO 與 RMF 兩邊都有結果後才寫檔，確保每個 task_id 只有一行完整紀錄
    def _maybe_write(self, record):
        if not (record['_rmf_done'] and record['_fifo_done']):
            return
        final = record['final_choice']
        record['match'] = record['fifo_choice'] == final
        # FIFO 選的車比實際派出的車多走幾公尺（依招標當下的位置計算）
        record['dist_penalty'] = self._penalty(record, final)

        self.pending.pop(record['task_id'], None)
        for key in ('_rmf_done', '_fifo_done'):
            record.pop(key)
        self.log.write(json.dumps(record, ensure_ascii=False) + '\n')

        verdict = '一致' if record['match'] else '★不一致'
        wait = record['fifo_wait_sec']
        extra = f'（等待 {wait}s）' if wait >= 0.5 else ''
        penalty = record['dist_penalty']
        extra += f'（多走 {penalty}m）' if penalty else ''
        self.get_logger().info(
            f"{record['task_id']}: FIFO={record['fifo_choice']} "
            f"RMF={final} {verdict}{extra}")

    # 距離代價：FIFO 人選與實際人選的距離差，兩者皆有距離資料時才計算
    def _penalty(self, record, final):
        cands = record['candidates']
        a = cands.get(record['fifo_choice'], {}).get('dist')
        b = cands.get(final, {}).get('dist')
        return round(a - b, 2) if a is not None and b is not None else None

    # 關閉前呼叫：把仍在佇列中、尚未指派的任務也記錄下來，避免資料悄悄消失
    def flush_unassigned(self):
        for task_id in self.queue:
            record = self.pending.get(task_id)
            if record is None or not record['_rmf_done']:
                continue
            record['fifo_choice'] = None
            record['fifo_status'] = 'still_queued_at_shutdown'
            record['fifo_wait_sec'] = round(time.time() - record['t_notice'], 1)
            record['match'] = False
            record['dist_penalty'] = None
            for key in ('_rmf_done', '_fifo_done'):
                record.pop(key, None)
            self.log.write(json.dumps(record, ensure_ascii=False) + '\n')


# 進入點：ros2 run fifo_dispatcher shadow_bidder
def main():
    rclpy.init()
    node = ShadowBidder()
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
        node.flush_unassigned()
        node.log.close()
        node.destroy_node()
        # 訊號處理器可能已經關過 context，用 try_shutdown 才不會二次關閉出錯
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
