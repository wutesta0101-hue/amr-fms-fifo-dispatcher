#!/usr/bin/env python3
# 測試替身：假的 Gazebo slotcar（**只用於測試，不是交付元件**）
#
# 存在的理由：驗證 bridge → vehicle → 車 → state → bridge 這一整圈，
# 不需要啟動 Gazebo 與整套 RMF。符合原則 10（每個節點要能單獨測試）。
#
# 模仿真實 slotcar 的三個關鍵行為：
#   ① 以 10 Hz 發布 /robot_state
#   ② 訂閱 /robot_path_requests，朝 path 最後一點直線移動
#   ③ RobotState.task_id 原封不動回報 PathRequest.task_id
#   ④ location.t 用「自己的模擬時鐘」（從 0 起算），刻意與牆鐘不同——
#      這正是 2026/08/13 那個「56 年後抵達」bug 的照妖鏡
#
# 執行：
#   source /opt/ros/humble/setup.bash && /usr/bin/python3 tools/fake_slotcar.py

import math
import sys

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_system_default

from builtin_interfaces.msg import Time
from rmf_fleet_msgs.msg import Location, PathRequest, RobotMode, RobotState

RATE = 10.0          # Hz，與真實 slotcar 的 robot_state_update_frequency 一致
V_LIN = 0.5          # m/s，取自 config.yaml
V_ANG = 0.6          # rad/s


class FakeSlotcar(Node):
    # 維護一台假車的位姿，收到路徑就朝目標移動，並持續回報狀態
    def __init__(self):
        super().__init__('fake_slotcar')
        p = self.declare_parameter
        self.name = p('robot_name', 'tinyRobot1').value
        self.x = p('start_x', 10.43).value      # tinyRobot1_charger
        self.y = p('start_y', -5.58).value
        self.yaw = p('start_yaw', 0.0).value
        self.battery = 100.0
        self.task_id = ''
        self.target = None                       # (x, y, yaw)
        self.sim_t = 0.0                         # 自己的模擬時鐘，從 0 起算
        self.seq = 0

        self.create_subscription(PathRequest, '/robot_path_requests',
                                 self.on_path, qos_profile_system_default)
        self.pub = self.create_publisher(RobotState, '/robot_state', 10)
        self.create_timer(1.0 / RATE, self.on_tick)
        self.get_logger().info(
            f'假 slotcar 啟動｜{self.name}｜起點 ({self.x}, {self.y})')

    # 收到路徑請求時觸發：取最後一點當目標，並沿用它的 task_id
    def on_path(self, msg):
        if msg.robot_name != self.name or not msg.path:
            return
        last = msg.path[-1]
        self.task_id = msg.task_id
        self.target = (last.x, last.y, last.yaw)
        dt = last.t.sec - self.sim_t
        self.get_logger().info(
            f'收到路徑 task_id={msg.task_id} → ({last.x:.2f}, {last.y:.2f})｜'
            f'目標時間 {last.t.sec}（我的時鐘 {self.sim_t:.0f}，差 {dt:.0f}s）')
        if abs(dt) > 3600:
            self.get_logger().error(
                f'目標時間與我的時鐘差了 {dt / 31_536_000:.1f} 年——'
                f'指令的時間基準錯了')

    # 每 0.1 秒：往目標移動一步，然後回報狀態
    def on_tick(self):
        self.sim_t += 1.0 / RATE
        moving = False
        if self.target is not None:
            tx, ty, tyaw = self.target
            dist = math.hypot(tx - self.x, ty - self.y)
            step = V_LIN / RATE
            if dist > step:
                self.x += (tx - self.x) / dist * step
                self.y += (ty - self.y) / dist * step
                self.yaw = math.atan2(ty - self.y, tx - self.x)
                moving = True
            else:
                self.x, self.y, self.yaw = tx, ty, tyaw
                self.target = None
                self.get_logger().info(f'抵達 ({tx:.2f}, {ty:.2f})')
        self.publish(moving)

    def publish(self, moving):
        self.seq += 1
        msg = RobotState()
        msg.name = self.name
        msg.model = 'FakeTinyRobot'
        msg.task_id = self.task_id
        msg.seq = self.seq
        msg.mode = RobotMode(mode=RobotMode.MODE_MOVING if moving
                             else RobotMode.MODE_IDLE)
        msg.battery_percent = self.battery
        loc = Location()
        loc.t = Time(sec=int(self.sim_t),
                     nanosec=int((self.sim_t % 1) * 1e9))
        loc.x, loc.y, loc.yaw = float(self.x), float(self.y), float(self.yaw)
        loc.level_name = 'L1'
        msg.location = loc
        msg.path = [loc] if moving else []
        self.pub.publish(msg)


def main():
    rclpy.init()
    node = FakeSlotcar()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    sys.exit(main())
