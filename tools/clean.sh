#!/usr/bin/env bash
# 徹底清除所有 RMF / Gazebo / 實驗相關行程。
# 起因：原本的 pkill -f "rmf_" 沒有殺掉 rmf_demos_fleet_adapter，
#       導致每跑一組就殘留一個 adapter，多個 adapter 同時控制同一批車輛而衝突。
echo "=== 清除前 ==="
ps -eo pid,etimes,cmd | grep -E 'rmf|ign gazebo|rviz2|fifo_dispatcher' | grep -v grep | wc -l

# ⚠️ 這裡不可以殺 rg.sh / ra.sh——run_group.sh 會呼叫本腳本，殺了會自殺。
#    要手動全停時，另外執行：pkill -f 'ra\.sh'; pkill -f 'rg\.sh'
pkill -f 'lib/fifo_dispatcher/'
pkill -f 'ros2 launch'
pkill -f 'rmf_demos_fleet_adapter'
pkill -f 'rmf_demos_panel'
pkill -f 'rmf_fleet_adapter'
pkill -f 'rmf_task_ros2'
pkill -f 'rmf_traffic_ros2'
pkill -f 'rmf_visualization'
pkill -f 'rmf_building_map'
pkill -f 'ros_ign_bridge'
pkill -f 'ros_gz_bridge'
pkill -f 'ign gazebo'
pkill -f rviz2
sleep 5

# 仍有殘留就強制
pkill -9 -f 'rmf_demos_fleet_adapter' 2>/dev/null
pkill -9 -f 'ign gazebo' 2>/dev/null
sleep 3

echo "=== 清除後（應為 0）==="
ps -eo pid,cmd | grep -E 'rmf|ign gazebo|rviz2|fifo_dispatcher' | grep -v grep | wc -l
ps -eo pid,etimes,cmd | grep -E 'rmf|ign gazebo|rviz2|fifo_dispatcher' | grep -v grep | cut -c1-70
