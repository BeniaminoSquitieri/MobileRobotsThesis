#!/usr/bin/env python3

import launch
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'robot_namespace',
            default_value='turtlebot1',
            description='Namespace del robot'
        ),
        # Non usiamo più initial_x e initial_y, ma initial_node_label
        DeclareLaunchArgument(
            'initial_node_label',
            default_value='node_14',
            description='Etichetta del nodo iniziale in cui si trova il robot'
        ),

        Node(
            package='fleet_turtlebot4_navigation',
            executable='simulated_slave_navigation_node',  
            name='simulated_slave_navigation_node',
            namespace=LaunchConfiguration('robot_namespace'),
            output='screen',
            arguments=[
                '--robot_namespace', LaunchConfiguration('robot_namespace'),
                '--initial_node_label', LaunchConfiguration('initial_node_label'),
                # Rimosso l'argomento 'initial_orientation' poiché non è più necessario
            ]
        )
    ])
