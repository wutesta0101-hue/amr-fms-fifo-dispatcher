# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Testa Wu
from setuptools import setup

package_name = 'fifo_dispatcher'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # M2 步驟 4：自寫 launch（ros2 launch fifo_dispatcher office_vda5050.launch.xml）
        ('share/' + package_name + '/launch', ['launch/office_vda5050.launch.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='amr-fms',
    maintainer_email='wutesta0101@gmail.com',
    description='AMR 車隊管理 FIFO 派工器',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            # ros2 run fifo_dispatcher shadow_bidder
            'shadow_bidder = fifo_dispatcher.shadow_bidder:main',
            # ros2 run fifo_dispatcher dispatcher
            'dispatcher = fifo_dispatcher.dispatcher:main',
            # ros2 run fifo_dispatcher vda5050_vehicle
            'vda5050_vehicle = fifo_dispatcher.vda5050_vehicle:main',
            # 非 ROS 節點，用 install/fifo_dispatcher/lib/fifo_dispatcher/vda5050_bridge 直接執行
            'vda5050_bridge = fifo_dispatcher.vda5050_bridge:main',
        ],
    },
)
