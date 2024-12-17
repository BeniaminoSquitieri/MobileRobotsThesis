#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import json
import time
import argparse
import math
import threading
import networkx as nx

from .graph_partitioning import load_full_graph, partition_graph
from .path_calculation import calculate_dcpp_route, orientation_rad_to_str

class SlaveState:
    def __init__(self, slave_ns):
        self.slave_ns = slave_ns
        self.assigned_waypoints = []
        self.current_waypoint_index = 0
        self.last_seen_time = 0.0
        self.initial_x = None
        self.initial_y = None
        self.initial_orientation = None
        self.publisher = None
        self.waiting = False

class SlaveNavigationSimulator(Node):
    def __init__(self, robot_namespace, initial_node_label, initial_orientation_str):
        self.robot_namespace = robot_namespace
        self.initial_node_label = initial_node_label
        self.initial_orientation_str = initial_orientation_str
        self.initial_orientation = self.orientation_conversion(initial_orientation_str)
        self.initial_x = None
        self.initial_y = None

        super().__init__('slave_navigation_simulator_node', namespace=self.robot_namespace)

        # Pub/Sub
        self.slave_registration_publisher = self.create_publisher(String, '/slave_registration', 10)
        self.initial_position_publisher = self.create_publisher(String, '/slave_initial_positions', 10)
        self.navigation_commands_subscriber = self.create_subscription(
            String, 'navigation_commands', self.navigation_commands_callback, 10
        )
        self.status_publisher = self.create_publisher(String, '/navigation_status', 10)
        self.heartbeat_publisher = self.create_publisher(String, '/slave_heartbeat', 10)
        self.master_heartbeat_subscriber = self.create_subscription(
            String, '/master_heartbeat', self.master_heartbeat_callback, 10
        )
        self.slave_heartbeat_subscriber = self.create_subscription(
            String, '/slave_heartbeat', self.slave_heartbeat_callback, 10
        )
        self.graph_subscriber = self.create_subscription(
            String, '/navigation_graph', self.navigation_graph_callback, 10
        )
        self.graph_publisher = self.create_publisher(String, '/navigation_graph', 10)
        self.navigation_status_subscriber = self.create_subscription(
            String, '/navigation_status', self.navigation_status_callback, 10
        )

        # Timers
        self.registration_timer = self.create_timer(1.0, self.publish_registration)
        self.heartbeat_timer = self.create_timer(1.0, self.publish_heartbeat)
        self.initial_position_timer = self.create_timer(2.0, self.try_publish_initial_position)
        self.master_check_timer = self.create_timer(10.0, self.check_master_alive)
        self.slave_check_timer = self.create_timer(2.0, self.check_slave_alive)

        # Variabili di stato
        self.master_alive = False
        self.last_master_heartbeat = time.time()
        self.heartbeat_timeout = 5.0
        self.active_slaves = {}
        self.navigation_graph = None
        self.occupied_nodes = set()
        self.partitioning_done = False
        self.current_waypoint_index = 0
        self.initial_position_published = False
        self.is_master = False
        self.master_graph_partitioned = False
        self.lock = threading.Lock()

        # Nuovo timer: se divento master, pubblicherò regolarmente il grafo e controllerò dinamicamente la situazione.
        # Questo timer sarà abilitato solo quando divento master.
        self.dynamic_recalc_timer = None  # Lo creeremo quando diventiamo master

        self.get_logger().info(
            f"[{self.robot_namespace}] Slave simulator initialized with initial node label '{self.initial_node_label}' "
            f"and orientation {self.initial_orientation_str} ({self.initial_orientation} radians)."
        )

    def try_publish_initial_position(self):
        if self.initial_x is not None and self.initial_y is not None and not self.initial_position_published:
            initial_position = {
                'robot_namespace': self.robot_namespace,
                'x': self.initial_x,
                'y': self.initial_y,
                'orientation': self.initial_orientation_str
            }
            msg = String()
            msg.data = json.dumps(initial_position)
            self.initial_position_publisher.publish(msg)
            self.get_logger().debug(f"[{self.robot_namespace}] Published initial position: {initial_position}")
            self.initial_position_published = True
            self.initial_position_timer.cancel()

    def publish_registration(self):
        msg = String()
        msg.data = self.robot_namespace
        self.slave_registration_publisher.publish(msg)
        self.get_logger().debug(f"[{self.robot_namespace}] Published registration.")

    def publish_heartbeat(self):
        heartbeat_msg = String()
        heartbeat_msg.data = self.robot_namespace
        self.heartbeat_publisher.publish(heartbeat_msg)
        self.get_logger().debug(f"[{self.robot_namespace}] Published heartbeat.")

    def master_heartbeat_callback(self, msg):
        self.master_alive = True
        self.last_master_heartbeat = time.time()
        self.get_logger().debug(f"[{self.robot_namespace}] Received master heartbeat.")

    def slave_heartbeat_callback(self, msg):
        slave_ns = msg.data.strip()
        current_time = time.time()
        if slave_ns != self.robot_namespace:
            with self.lock:
                if slave_ns not in self.active_slaves:
                    self.active_slaves[slave_ns] = SlaveState(slave_ns)
                    self.get_logger().info(f"[{self.robot_namespace}] Detected new slave: {slave_ns}")
                self.active_slaves[slave_ns].last_seen_time = current_time
            self.get_logger().debug(f"[{self.robot_namespace}] Received heartbeat from slave {slave_ns}.")

    def check_master_alive(self):
        current_time = time.time()
        if self.master_alive:
            self.master_alive = False
        else:
            if current_time - self.last_master_heartbeat > self.heartbeat_timeout:
                self.get_logger().warn(f"[{self.robot_namespace}] Master heartbeat lost. Initiating master election.")
                self.elect_new_master()

    def check_slave_alive(self):
        current_time = time.time()
        with self.lock:
            for slave_ns in list(self.active_slaves.keys()):
                if current_time - self.active_slaves[slave_ns].last_seen_time > self.heartbeat_timeout:
                    self.get_logger().warn(f"[{self.robot_namespace}] Slave {slave_ns} heartbeat lost. Removing from active slaves.")
                    del self.active_slaves[slave_ns]

    def elect_new_master(self):
        with self.lock:
            candidates = list(self.active_slaves.keys()) + [self.robot_namespace]

        if not candidates:
            self.get_logger().error(f"[{self.robot_namespace}] No candidates available for master election.")
            return

        candidates_sorted = sorted(candidates)
        new_master = candidates_sorted[0]

        if new_master == self.robot_namespace:
            self.get_logger().info(f"[{self.robot_namespace}] Elected as the new master.")
            self.become_master()
        else:
            self.get_logger().info(f"[{self.robot_namespace}] New master is {new_master}.")

    def become_master(self):
        self.is_master = True
        self.get_logger().info(f"[{self.robot_namespace}] Now acting as the master.")

        if self.navigation_graph is not None:
            self.publish_navigation_graph()
            self.get_logger().info(f"[{self.robot_namespace}] Published navigation graph. Starting partitioning and waypoint assignment.")
            self.partition_and_assign_waypoints()
            # Avvia un timer per ricontrollare dinamicamente la situazione e ripubblicare il grafo.
            # Ogni 10 secondi (o il tempo che preferisci) ricontrolla se la situazione è cambiata (numero slave, ecc.)
            self.dynamic_recalc_timer = self.create_timer(10.0, self.dynamic_master_update)
        else:
            self.get_logger().error(f"[{self.robot_namespace}] Navigation graph not available. Cannot become master.")

    def publish_navigation_graph(self):
        if self.navigation_graph is not None:
            graph_data = {
                'nodes': [
                    {'label': node, 'x': data['x'], 'y': data['y'], 'orientation': data.get('orientation', 0.0)}
                    for node, data in self.navigation_graph.nodes(data=True)
                ],
                'edges': [
                    {'from': u, 'to': v, 'weight': data.get('weight', 1.0)}
                    for u, v, data in self.navigation_graph.edges(data=True)
                ]
            }
            msg = String()
            msg.data = json.dumps(graph_data)
            self.graph_publisher.publish(msg)
            self.get_logger().debug(f"[{self.robot_namespace}] Published navigation graph.")

    def dynamic_master_update(self):
        """
        Chiamato periodicamente quando siamo master.
        Controlla se il numero di slave è cambiato, se sono stati aggiunti/rimossi slave.
        In caso affermativo, ripartiziona e riassegna i percorsi.
        Ripubblica anche regolarmente il grafo.
        """
        # Ripubblica sempre il grafo per sicurezza.
        self.publish_navigation_graph()

        # Controlla se serve ripartizionare:
        # Ad esempio, se il numero di slave o le loro posizioni iniziali sono cambiate.
        # Qui potresti inserire una logica più complessa: se il numero di slave attivi è diverso dall'ultima volta
        # o se qualche slave è in waiting da troppo tempo, o se ci sono stati errori, ecc.
        # Per semplicità, supponiamo che controlliamo solo il numero di slave.
        current_num_slaves = len(self.active_slaves) + 1  # +1 per il master
        # Se ad esempio non abbiamo mai partizionato o se il numero di slave cambia, ripartiziona.
        # Qui potresti memorizzare in una variabile il numero di slave dell'ultima partizione e confrontare.
        # Per brevità, ripartizioniamo sempre.
        self.partition_and_assign_waypoints()

    def navigation_graph_callback(self, msg):
        try:
            graph_data = json.loads(msg.data)
            self.navigation_graph = self.load_full_graph_from_data(graph_data)
            self.get_logger().debug(f"[{self.robot_namespace}] Received navigation graph.")

            if self.initial_node_label in self.navigation_graph.nodes:
                node_data = self.navigation_graph.nodes[self.initial_node_label]
                self.initial_x = node_data['x']
                self.initial_y = node_data['y']

            if self.is_master and not self.partitioning_done:
                self.partition_and_assign_waypoints()
        except json.JSONDecodeError as e:
            self.get_logger().error(f"[{self.robot_namespace}] Failed to decode navigation graph: {e}")

    def navigation_commands_callback(self, msg):
        try:
            waypoint_data = json.loads(msg.data)
        except json.JSONDecodeError as e:
            self.get_logger().error(f"[{self.robot_namespace}] Failed to decode navigation command: {e}")
            return

        if isinstance(waypoint_data.get('orientation'), str):
            waypoint_data['orientation'] = self.orientation_conversion(waypoint_data['orientation'])

        threading.Thread(target=self.simulate_navigation, args=(waypoint_data,)).start()

    def simulate_navigation(self, waypoint):
        label = waypoint['label']
        x = waypoint['x']
        y = waypoint['y']
        orientation_rad = waypoint['orientation']
        self.get_logger().info(f"[{self.robot_namespace}] Simulating navigation to {label} at ({x}, {y}) with orientation {orientation_rad} radians.")

        simulated_navigation_time = 15.0
        time.sleep(simulated_navigation_time)

        nav_success = True

        if nav_success:
            self.get_logger().info(f"[{self.robot_namespace}] Reached {label} in {simulated_navigation_time} seconds.")
            self.publish_status("reached", "", simulated_navigation_time, label)
            if self.is_master:
                with self.lock:
                    self.current_waypoint_index += 1
                with self.lock:
                    if label in self.occupied_nodes:
                        self.occupied_nodes.remove(label)
                        self.get_logger().info(f"[{self.robot_namespace}] Node {label} is now free.")
                self.assign_next_waypoint(self.robot_namespace)
                self.assign_waiting_slaves()
        else:
            error_message = f"Simulation of navigation to {label} failed."
            self.get_logger().error(f"[{self.robot_namespace}] {error_message}")
            self.publish_status("error", error_message, simulated_navigation_time, label)

    def publish_status(self, status, error_message, time_taken, current_waypoint):
        status_data = {
            'robot_namespace': self.robot_namespace,
            'status': status,
            'error_message': error_message,
            'time_taken': time_taken,
            'current_waypoint': current_waypoint
        }
        msg = String()
        msg.data = json.dumps(status_data)
        self.status_publisher.publish(msg)
        self.get_logger().info(f"[{self.robot_namespace}] Published status: {status_data}")

    def navigation_status_callback(self, msg):
        try:
            data = json.loads(msg.data)
            slave_ns = data['robot_namespace']
            status = data['status']
            current_waypoint = data['current_waypoint']
            time_taken = data['time_taken']
            error_message = data.get('error_message', '')
        except (json.JSONDecodeError, KeyError) as e:
            self.get_logger().error(f"[{self.robot_namespace}] Invalid navigation status message: {e}")
            return

        current_time = time.time()

        if self.is_master:
            with self.lock:
                if slave_ns in self.active_slaves:
                    slave = self.active_slaves[slave_ns]
                elif slave_ns == self.robot_namespace:
                    slave = self
                else:
                    self.get_logger().warn(f"[{self.robot_namespace}] Received status from unknown slave {slave_ns}.")
                    return
                slave.last_seen_time = current_time

                if status == "reached":
                    if current_waypoint in self.occupied_nodes:
                        self.occupied_nodes.remove(current_waypoint)
                        self.get_logger().info(f"[{self.robot_namespace}] Node {current_waypoint} is now free.")
                    else:
                        self.get_logger().warn(f"[{self.robot_namespace}] Node {current_waypoint} was not marked as occupied.")

                    self.get_logger().info(f"[{self.robot_namespace}] Slave {slave_ns} has reached waypoint {current_waypoint}.")
                    slave.waiting = False
                    self.assign_next_waypoint(slave_ns)
                    self.assign_waiting_slaves()

                elif status == "error":
                    self.get_logger().error(f"[{self.robot_namespace}] Slave {slave_ns} encountered an error: {error_message}")
                    if current_waypoint in self.occupied_nodes:
                        self.occupied_nodes.remove(current_waypoint)
                        self.get_logger().info(f"[{self.robot_namespace}] Node {current_waypoint} is now free due to error.")
                    if slave_ns in self.active_slaves:
                        del self.active_slaves[slave_ns]
                        self.get_logger().warn(f"[{self.robot_namespace}] Removing slave {slave_ns} due to error.")
                        # Ricalcolo partizione e riassegno
                        self.partition_and_assign_waypoints()
        else:
            # Se non è master, non fa nulla di particolare.
            pass

    def partition_and_assign_waypoints(self):
        if self.navigation_graph is None:
            self.get_logger().error(f"[{self.robot_namespace}] Navigation graph not available. Cannot partition and assign waypoints.")
            return

        with self.lock:
            num_slaves = len(self.active_slaves) + 1
            if num_slaves == 0:
                self.get_logger().warn("No active slaves found. Waiting for slaves to register.")
                self.partitioning_done = False
                return

            start_positions = []
            for slave_ns, slave in self.active_slaves.items():
                if slave.initial_x is not None and slave.initial_y is not None:
                    start_positions.append({'x': slave.initial_x, 'y': slave.initial_y})
                else:
                    self.get_logger().warn(f"Slave {slave_ns} initial position not available")

            if self.initial_x is None or self.initial_y is None:
                self.get_logger().error("Master initial position not available, cannot partition.")
                return

            start_positions.append({'x': self.initial_x, 'y': self.initial_y})

            if len(start_positions) != num_slaves:
                self.get_logger().error("Not all slaves have valid initial positions.")
                return

            try:
                subgraphs = partition_graph(self.navigation_graph, num_slaves, start_positions=start_positions)
                self.get_logger().info(f"Partitioned the graph into {len(subgraphs)} subgraphs.")
                self.print_subgraphs(subgraphs)
            except ValueError as e:
                self.get_logger().error(f"Failed to partition graph: {e}")
                return

            all_slaves = list(self.active_slaves.keys()) + [self.robot_namespace]
            all_slaves_sorted = sorted(all_slaves)

            if len(subgraphs) != len(all_slaves_sorted):
                self.get_logger().error("Number of subgraphs does not match number of active slaves.")
                return

            # Riassegna i waypoint a tutti (cancella i vecchi se necessario)
            for slave_ns in all_slaves_sorted:
                if slave_ns in self.active_slaves:
                    self.active_slaves[slave_ns].assigned_waypoints = []
                    self.active_slaves[slave_ns].current_waypoint_index = 0
                    self.active_slaves[slave_ns].waiting = False
                if slave_ns == self.robot_namespace:
                    self.assigned_waypoints = []
                    self.current_waypoint_index = 0

            for idx, slave_ns in enumerate(all_slaves_sorted):
                subgraph = subgraphs[idx]
                waypoints = self.extract_waypoints(subgraph)
                dcpp_route = calculate_dcpp_route(waypoints, subgraph, self.get_logger())
                ordered_route = dcpp_route

                self.get_logger().info(f"DCPP Route for {slave_ns}:")
                for wp in ordered_route:
                    self.get_logger().info(f"  {wp}")

                if slave_ns == self.robot_namespace:
                    self.assigned_waypoints = ordered_route
                    self.assign_next_waypoint(self.robot_namespace)
                else:
                    if slave_ns in self.active_slaves:
                        slave = self.active_slaves[slave_ns]
                        slave.assigned_waypoints = ordered_route
                        self.assign_next_waypoint(slave_ns)
                    else:
                        self.get_logger().warn(f"Slave {slave_ns} not found in active_slaves.")

            self.partitioning_done = True

    def assign_next_waypoint(self, slave_ns):
        with self.lock:
            if slave_ns == self.robot_namespace:
                slave = self
            else:
                slave = self.active_slaves.get(slave_ns, None)

            if slave is None:
                self.get_logger().warn(f"Slave {slave_ns} not found.")
                return

            if len(slave.assigned_waypoints) == 0:
                self.get_logger().warn(f"No waypoints assigned to slave {slave_ns}.")
                return

            waypoint = slave.assigned_waypoints[slave.current_waypoint_index % len(slave.assigned_waypoints)]
            node_label = waypoint['label']

            if node_label in self.occupied_nodes:
                self.get_logger().warn(f"Node {node_label} is already occupied. Slave {slave_ns} must wait.")
                slave.waiting = True
                return

            waypoint_msg = {
                'label': waypoint['label'],
                'x': waypoint['x'],
                'y': waypoint['y'],
                'orientation': orientation_rad_to_str(waypoint['orientation'])
            }
            msg = String()
            msg.data = json.dumps(waypoint_msg)

            if slave_ns == self.robot_namespace:
                self.navigation_commands_callback(msg)
                self.get_logger().info(f"[Master {self.robot_namespace}] Assigned waypoint to itself: {waypoint_msg}")
            else:
                if slave.publisher is None:
                    slave.publisher = self.create_publisher(String, f'/{slave_ns}/navigation_commands', 10)
                slave.publisher.publish(msg)
                self.get_logger().info(f"[Master {self.robot_namespace}] Assigned waypoint to {slave_ns}: {waypoint_msg}")

            self.occupied_nodes.add(node_label)
            slave.current_waypoint_index += 1
            if slave.current_waypoint_index >= len(slave.assigned_waypoints):
                slave.current_waypoint_index = 0

    def assign_waiting_slaves(self):
        with self.lock:
            candidates = list(self.active_slaves.keys()) + [self.robot_namespace]
            for slave_ns in sorted(candidates):
                if slave_ns == self.robot_namespace and self.is_master:
                    slave = self
                else:
                    slave = self.active_slaves.get(slave_ns, None)
                    if slave is None:
                        continue

                if slave.waiting:
                    if len(slave.assigned_waypoints) == 0:
                        self.get_logger().warn(f"No waypoints assigned to slave {slave_ns}.")
                        continue

                    waypoint = slave.assigned_waypoints[slave.current_waypoint_index % len(slave.assigned_waypoints)]
                    node_label = waypoint['label']

                    if node_label not in self.occupied_nodes:
                        waypoint_msg = {
                            'label': waypoint['label'],
                            'x': waypoint['x'],
                            'y': waypoint['y'],
                            'orientation': orientation_rad_to_str(waypoint['orientation'])
                        }
                        msg = String()
                        msg.data = json.dumps(waypoint_msg)

                        if slave_ns == self.robot_namespace:
                            self.navigation_commands_callback(msg)
                            self.get_logger().info(f"[Master {self.robot_namespace}] Assigned waypoint to itself: {waypoint_msg}")
                        else:
                            if slave.publisher is None:
                                slave.publisher = self.create_publisher(String, f'/{slave_ns}/navigation_commands', 10)
                            slave.publisher.publish(msg)
                            self.get_logger().info(f"[Master {self.robot_namespace}] Assigned waypoint to {slave_ns}: {waypoint_msg}")

                        self.occupied_nodes.add(node_label)
                        slave.waiting = False
                        slave.current_waypoint_index += 1
                        if slave.current_waypoint_index >= len(slave.assigned_waypoints):
                            slave.current_waypoint_index = 0
                    else:
                        self.get_logger().warn(f"Node {node_label} is still occupied. Slave {slave_ns} remains in waiting state.")

    def print_subgraphs(self, subgraphs):
        self.get_logger().info("----- Subgraphs After Partition -----")
        for idx, subgraph in enumerate(subgraphs):
            self.get_logger().info(f"Subgraph {idx+1}:")
            self.get_logger().info(f"  Nodes ({len(subgraph.nodes())}):")
            for node, data in subgraph.nodes(data=True):
                x = data.get('x', 0.0)
                y = data.get('y', 0.0)
                orientation = data.get('orientation', 0.0)
                self.get_logger().info(f"    {node}: Position=({x}, {y}), Orientation={orientation} radians")
            self.get_logger().info(f"  Edges ({len(subgraph.edges())}):")
            for u, v, data in subgraph.edges(data=True):
                weight = data.get('weight', 1.0)
                self.get_logger().info(f"    From {u} to {v}, Weight: {weight}")
        self.get_logger().info("----- End of Subgraphs -----")

    def extract_waypoints(self, subgraph):
        waypoints = []
        for node, data in subgraph.nodes(data=True):
            waypoint = {
                'label': node,
                'x': data['x'],
                'y': data['y'],
                'orientation': data.get('orientation', 0.0)
            }
            waypoints.append(waypoint)
        return waypoints

    def orientation_conversion(self, orientation_input):
        if isinstance(orientation_input, str):
            orientation_map = {
                "NORTH": 0.0,
                "EAST": -math.pi / 2,
                "SOUTH": math.pi,
                "WEST": math.pi / 2
            }
            return orientation_map.get(orientation_input.upper(), 0.0)
        elif isinstance(orientation_input, (float, int)):
            return float(orientation_input)
        else:
            return 0.0

    def load_full_graph_from_data(self, graph_data):
        G = nx.DiGraph()
        for node in graph_data['nodes']:
            label = node['label']
            x = node['x']
            y = node['y']
            orientation = node.get('orientation', 0.0)
            G.add_node(label, x=x, y=y, orientation=orientation)

        for edge in graph_data['edges']:
            u = edge['from']
            v = edge['to']
            weight = edge.get('weight', 1.0)
            G.add_edge(u, v, weight=weight)
        return G

def main(args=None):
    rclpy.init(args=args)

    parser = argparse.ArgumentParser(description='Slave Navigation Simulator Node')
    parser.add_argument('--robot_namespace', type=str, default='robot_simulator', help='Robot namespace')
    parser.add_argument('--initial_node_label', type=str, default='node_1', help='Initial node label')
    parser.add_argument('--initial_orientation', type=str, default='NORTH', help='Initial orientation (NORTH,EAST,SOUTH,WEST)')

    args, unknown = parser.parse_known_args()

    node = SlaveNavigationSimulator(
        robot_namespace=args.robot_namespace,
        initial_node_label=args.initial_node_label,
        initial_orientation_str=args.initial_orientation
    )

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass

    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
