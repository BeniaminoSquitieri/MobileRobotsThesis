from setuptools import setup
import os
from glob import glob

package_name = 'fleet_turtlebot4_navigation'

setup(
    name=package_name,
    version='0.0.1',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # Includi altri file necessari (es. mappe, JSON)
        (os.path.join('share', package_name, 'map'), glob('map/*.json')),
    ],
    install_requires=['setuptools', 'networkx', 'numpy', 'scikit-learn'],
    zip_safe=True,
    maintainer='beniamino',
    maintainer_email='bennibeniamino@gmail.com',
    description='Fleet Turtlebot4 Navigation Package',
    license='Apache License 2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'master_navigation_node = fleet_turtlebot4_navigation.master_navigation_node:main',
            'backup_master_navigation_node = fleet_turtlebot4_navigation.backup_master_navigation_node:main',
            'simulated_slave_navigation_node = fleet_turtlebot4_navigation.simulated_slave_navigation_node:main',
            'graph_partitioning = fleet_turtlebot4_navigation.graph_partitioning:main',
            'path_calculation = fleet_turtlebot4_navigation.path_calculation:main',
            'slave_navigation_node = fleet_turtlebot4_navigation.slave_navigation_node:main',
        ],
    },
)
