# slave_navigation_node.py

#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import json
import time
import math
from fleet_turtlebot4_navigation.path_calculation import calculate_dcpp_route, orientation_conversion
from fleet_turtlebot4_navigation.graph_partitioning import load_full_graph_from_data
# Assicurati che i percorsi di import siano corretti in base alla struttura del tuo pacchetto

class SlaveState:
    """
    Classe per gestire lo stato di ogni robot slave.
    """
    def __init__(self, slave_ns, publisher):
        self.slave_ns = slave_ns
        self.publisher = publisher
        self.assigned_waypoints = []  # Lista di waypoints assegnati
        self.current_waypoint_index = 0  # Indice del prossimo waypoint da assegnare
        self.last_seen_time = 0.0  # Ultima volta che lo slave ha comunicato
        self.initial_x = None  # Posizione X iniziale
        self.initial_y = None  # Posizione Y iniziale
        self.initial_orientation = None  # Orientamento iniziale
        self.is_master = False  # Flag che indica se questo slave è diventato master

class SlaveNavigationNode(Node):
    def __init__(self):
        super().__init__('slave_navigation_node')

        # Dichiarazione e recupero dei parametri ROS 2
        self.declare_parameter('robot_namespace', 'robot_111')
        self.declare_parameter('initial_x', 0.0)
        self.declare_parameter('initial_y', 0.0)
        self.declare_parameter('initial_orientation', 'NORTH')  # Può essere 'NORTH', 'EAST', 'SOUTH', 'WEST'
        self.declare_parameter('robot_id', 111)

        self.robot_namespace = self.get_parameter('robot_namespace').get_parameter_value().string_value
        self.initial_x = float(self.get_parameter('initial_x').get_parameter_value().double_value)
        self.initial_y = float(self.get_parameter('initial_y').get_parameter_value().double_value)
        self.initial_orientation_str = self.get_parameter('initial_orientation').get_parameter_value().string_value
        self.robot_id = int(self.get_parameter('robot_id').get_parameter_value().integer_value)

        # Conversione dell'orientamento
        self.initial_orientation = self.orientation_conversion(self.initial_orientation_str)

        # Publisher per registrare lo slave con il master
        self.slave_registration_publisher = self.create_publisher(String, '/slave_registration', 10)

        # Publisher per inviare la posizione iniziale al master
        self.initial_position_publisher = self.create_publisher(String, '/slave_initial_positions', 10)

        # Subscriber per ricevere i comandi di navigazione dal master
        self.navigation_commands_subscriber = self.create_subscription(
            String,
            '/navigation_commands',
            self.navigation_commands_callback,
            10
        )

        # Publisher per inviare lo stato della navigazione al master
        self.status_publisher = self.create_publisher(String, '/navigation_status', 10)

        # Publisher per inviare messaggi di heartbeat
        self.heartbeat_publisher = self.create_publisher(String, '/slave_heartbeat', 10)

        # Subscriber per ricevere heartbeat dal master
        self.master_heartbeat_subscriber = self.create_subscription(
            String,
            '/master_heartbeat',
            self.master_heartbeat_callback,
            10
        )

        # Subscriber per ricevere heartbeat dagli altri slave
        self.slave_heartbeat_subscriber = self.create_subscription(
            String,
            '/slave_heartbeat',
            self.slave_heartbeat_callback,
            10
        )

        # Subscriber per ricevere il grafo di navigazione dal master
        self.graph_subscriber = self.create_subscription(
            String,
            '/navigation_graph',
            self.navigation_graph_callback,
            10
        )

        # Timer per pubblicare messaggi di registrazione
        self.registration_timer = self.create_timer(1.0, self.publish_registration)

        # Timer per pubblicare messaggi di heartbeat
        self.heartbeat_timer = self.create_timer(1.0, self.publish_heartbeat)

        # Pubblica la posizione iniziale una volta all'avvio
        self.publish_initial_position()

        # Inizializza la lista degli slave attivi
        self.active_slaves = {}  # Chiave: slave_ns, Valore: last_seen_time

        # Flag per il master
        self.is_master = False

        # Navigazione graph
        self.navigation_graph = None

        # Timer per controllare il heartbeat del master
        self.heartbeat_timeout = 5.0  # secondi
        self.last_master_heartbeat = time.time()
        self.master_alive = False
        self.master_check_timer = self.create_timer(1.0, self.check_master_alive)

        # Timer per controllare i heartbeat degli slave
        self.slave_heartbeat_timeout = 5.0  # secondi
        self.slave_check_timer = self.create_timer(2.0, self.check_slave_alive)

        # Inizializza la classe TurtleBot4Navigator
        # self.navigator = TurtleBot4Navigator()  # Assicurati che sia implementato correttamente

        # Log dell'inizializzazione dello slave
        self.get_logger().info(f"[{self.robot_namespace}] Slave node initialized at ({self.initial_x}, {self.initial_y}) with orientation {self.initial_orientation_str} ({self.initial_orientation} radians).")

    def publish_registration(self):
        """
        Pubblica un messaggio di registrazione per indicare che questo slave è attivo.
        """
        msg = String()
        msg.data = self.robot_namespace
        self.slave_registration_publisher.publish(msg)
        self.get_logger().debug(f"[{self.robot_namespace}] Published registration.")

    def publish_initial_position(self):
        """
        Pubblica la posizione iniziale dello slave al master.
        """
        initial_position = {
            'robot_namespace': self.robot_namespace,
            'x': self.initial_x,
            'y': self.initial_y,
            'orientation': self.initial_orientation_str  # Invio orientamento come stringa
        }
        msg = String()
        msg.data = json.dumps(initial_position)
        self.initial_position_publisher.publish(msg)
        self.get_logger().info(f"[{self.robot_namespace}] Published initial position: {initial_position}")

    def publish_heartbeat(self):
        """
        Pubblica un messaggio di heartbeat per indicare che questo slave è attivo.
        """
        heartbeat_msg = String()
        heartbeat_msg.data = self.robot_namespace
        self.heartbeat_publisher.publish(heartbeat_msg)
        self.get_logger().debug(f"[{self.robot_namespace}] Published heartbeat.")

    def master_heartbeat_callback(self, msg):
        """
        Callback per gestire i messaggi di heartbeat del master.
        Aggiorna il timestamp dell'ultimo heartbeat ricevuto dal master.
        """
        self.master_alive = True
        self.last_master_heartbeat = time.time()
        self.get_logger().debug(f"[{self.robot_namespace}] Received master heartbeat.")

    def slave_heartbeat_callback(self, msg):
        """
        Callback per gestire i messaggi di heartbeat degli altri slave.
        Aggiorna la lista degli slave attivi.
        """
        slave_ns = msg.data.strip()
        current_time = time.time()
        if slave_ns != self.robot_namespace:
            self.active_slaves[slave_ns] = current_time
            self.get_logger().debug(f"[{self.robot_namespace}] Received heartbeat from slave {slave_ns}.")

    def check_master_alive(self):
        """
        Controlla se il master è ancora attivo basandosi sull'ultimo heartbeat ricevuto.
        Se il master non è più attivo, avvia l'elezione di un nuovo master.
        """
        current_time = time.time()
        if self.master_alive:
            # Reset del flag, sarà impostato di nuovo se riceviamo un heartbeat
            self.master_alive = False
        else:
            # Nessun heartbeat ricevuto dall'ultimo controllo
            if current_time - self.last_master_heartbeat > self.heartbeat_timeout:
                self.get_logger().warn(f"[{self.robot_namespace}] Master heartbeat lost. Initiating master election.")
                self.elect_new_master()

    def check_slave_alive(self):
        """
        Controlla se gli altri slave sono ancora attivi basandosi sui loro heartbeats.
        Rimuove gli slave che non hanno inviato heartbeat recentemente.
        """
        current_time = time.time()
        for slave_ns in list(self.active_slaves.keys()):
            if current_time - self.active_slaves[slave_ns] > self.slave_heartbeat_timeout:
                self.get_logger().warn(f"[{self.robot_namespace}] Slave {slave_ns} heartbeat lost. Removing from active slaves.")
                del self.active_slaves[slave_ns]

    def elect_new_master(self):
        """
        Esegue l'elezione di un nuovo master tra gli slave attivi.
        Il slave con il namespace più basso (ordinamento lexicografico) diventa il nuovo master.
        """
        # Includi se stesso nella lista dei candidati
        candidates = list(self.active_slaves.keys()) + [self.robot_namespace]

        if not candidates:
            self.get_logger().error(f"[{self.robot_namespace}] No candidates available for master election.")
            return

        # Ordina i candidati in ordine lexicografico
        candidates_sorted = sorted(candidates)

        # Il primo candidato nella lista ordinata diventa il master
        new_master = candidates_sorted[0]

        if new_master == self.robot_namespace:
            self.get_logger().info(f"[{self.robot_namespace}] Elected as the new master.")
            self.become_master()
        else:
            self.get_logger().info(f"[{self.robot_namespace}] New master is {new_master}.")

    def become_master(self):
        """
        Trasforma questo slave in master, eseguendo tutte le operazioni di master.
        """
        self.is_master = True
        self.get_logger().info(f"[{self.robot_namespace}] Now acting as the master.")

        # Pubblica il grafo di navigazione sul topic '/navigation_graph'
        if self.navigation_graph is not None:
            self.publish_navigation_graph()
            self.get_logger().info(f"[{self.robot_namespace}] Published navigation graph. Starting partitioning and waypoint assignment.")
            self.partition_and_assign_waypoints()
        else:
            self.get_logger().error(f"[{self.robot_namespace}] Navigation graph not available. Cannot become master.")

    def publish_navigation_graph(self):
        """
        Pubblica il grafo di navigazione sul topic '/navigation_graph'.
        """
        graph_msg = String()
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
        graph_msg.data = json.dumps(graph_data)
        self.graph_publisher.publish(graph_msg)
        self.get_logger().info("Published navigation graph as master.")

    def navigation_graph_callback(self, msg):
        """
        Callback per gestire il messaggio del grafo di navigazione ricevuto dal master.
        """
        try:
            graph_data = json.loads(msg.data)
            self.navigation_graph = load_full_graph_from_data(graph_data)
            self.get_logger().info(f"[{self.robot_namespace}] Received navigation graph.")
            if self.is_master and not getattr(self, 'master_graph_partitioned', False):
                self.partition_and_assign_waypoints()
        except json.JSONDecodeError as e:
            self.get_logger().error(f"[{self.robot_namespace}] Failed to decode navigation graph: {e}")

    def slave_registration_callback(self, msg):
        # Questo metodo è già definito sopra, evita duplicazioni
        pass

    def initial_position_callback(self, msg):
        # Questo metodo è già definito sopra, evita duplicazioni
        pass

    def navigation_status_callback(self, msg):
        # Questo metodo è già definito sopra, evita duplicazioni
        pass

    def partition_and_assign_waypoints(self):
        """
        Partiziona il grafo di navigazione in base al numero di slave attivi e assegna waypoint a ciascuno slave.
        Calcola il percorso DCPP (circuito Euleriano) per ogni sottografo.
        """
        num_slaves = len(self.slaves)
        if num_slaves == 0:
            self.get_logger().warn("No active slaves found. Waiting for slaves to register.")
            self.partitioning_done = False
            return

        # Raccolta delle posizioni iniziali degli slave
        start_positions = []
        for slave in self.slaves.values():
            if slave.initial_x is not None and slave.initial_y is not None:
                start_positions.append({'x': slave.initial_x, 'y': slave.initial_y})
            else:
                self.get_logger().warn(f"Slave {slave.slave_ns} lacks valid initial position.")

        if len(start_positions) != num_slaves:
            self.get_logger().error("Not all slaves have valid initial positions.")
            return

        # Partiziona il grafo in sottografi basati sul numero di slave e le loro posizioni iniziali
        try:
            subgraphs = partition_graph(self.full_graph, num_slaves, start_positions=start_positions)
            self.get_logger().info(f"Partitioned the graph into {len(subgraphs)} subgraphs.")
        except ValueError as e:
            self.get_logger().error(f"Failed to partition graph: {e}")
            return

        # Ordina gli slave per garantire un'assegnazione coerente (es. ordine alfabetico)
        slaves_sorted = sorted(self.slaves.keys())

        # Assicurati che il numero di sottografi corrisponda al numero di slave
        if len(subgraphs) != len(slaves_sorted):
            self.get_logger().error("Number of subgraphs does not match number of active slaves.")
            return

        # Assegna ogni sottografo a uno slave
        for idx, slave_ns in enumerate(slaves_sorted):
            subgraph = subgraphs[idx]
            waypoints = self.extract_waypoints(subgraph)

            # Verifica che lo slave abbia una posizione iniziale
            slave = self.slaves[slave_ns]
            if slave.initial_x is not None and slave.initial_y is not None:
                # Assicurati che il primo waypoint sia la posizione iniziale dello slave
                # Trova il waypoint più vicino alla posizione iniziale
                min_distance = float('inf')
                initial_wp = None
                for wp in waypoints:
                    distance = math.hypot(wp['x'] - slave.initial_x, wp['y'] - slave.initial_y)
                    if distance < min_distance:
                        min_distance = distance
                        initial_wp = wp
                if initial_wp:
                    # Riordina i waypoints in modo che initial_wp sia il primo
                    waypoints = [initial_wp] + [wp for wp in waypoints if wp != initial_wp]
            else:
                self.get_logger().warn(f"Slave {slave_ns} lacks initial position data.")

            # Calcola il percorso DCPP (circuito Euleriano) per il sottografo
            dcpp_route = calculate_dcpp_route(waypoints, subgraph, self.get_logger())
            # Assegna il percorso allo slave
            self.assign_route_to_slave(slave_ns, dcpp_route)

        self.partitioning_done = True

    def extract_waypoints(self, subgraph):
        """
        Estrae i waypoint da un sottografo.

        Args:
            subgraph (nx.Graph): Sottografo da cui estrarre i waypoint.

        Returns:
            list of dict: Lista di waypoint con 'label', 'x', 'y', e 'orientation'.
        """
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

    def assign_route_to_slave(self, slave_ns, route):
        """
        Assegna un percorso di waypoint a uno slave specifico.
        
        Args:
            slave_ns (str): Namespace dello slave robot.
            route (list of dict): Lista ordinata di waypoint.
        """
        if slave_ns not in self.slaves:
            self.get_logger().error(f"No slave state found for {slave_ns}. Cannot assign route.")
            return

        slave = self.slaves[slave_ns]
        slave.assigned_waypoints = route.copy()
        slave.current_waypoint_index = 0

        # Log dettagliato del percorso assegnato
        self.get_logger().info(f"Assigned DCPP route to {slave_ns}:")
        for wp in route:
            self.get_logger().info(f" - {wp['label']} at ({wp['x']}, {wp['y']}), Orientation: {wp['orientation']} radians")

        # Assegna il primo waypoint
        self.assign_next_waypoint(slave)

        # Log del numero di waypoint assegnati
        self.get_logger().info(f"Assigned {len(route)} waypoints to {slave_ns}.")

    def assign_next_waypoint(self, slave):
        """
        Assegna il prossimo waypoint nella coda allo slave.

        Args:
            slave (SlaveState): Lo slave a cui assegnare il waypoint.
        """
        if slave.current_waypoint_index < len(slave.assigned_waypoints):
            waypoint = slave.assigned_waypoints[slave.current_waypoint_index]
            waypoint_msg = {
                'label': waypoint['label'],
                'x': waypoint['x'],
                'y': waypoint['y'],
                'orientation': orientation_conversion(waypoint['orientation'])
            }
            msg = String()
            msg.data = json.dumps(waypoint_msg)
            slave.publisher.publish(msg)
            self.get_logger().info(f"Assigned waypoint to {slave.slave_ns}: {waypoint_msg}")
        else:
            # Tutti i waypoint sono stati assegnati, ricomincia dal primo
            self.get_logger().info(f"All waypoints have been assigned to {slave.slave_ns}. Restarting the route.")
            slave.current_waypoint_index = 0
            self.assign_next_waypoint(slave)  # Assegna nuovamente il primo waypoint

    def navigation_status_callback(self, msg):
        # Questo metodo è già definito sopra, evita duplicazioni
        pass

    def print_current_status(self):
        """
        Stampa lo stato attuale degli slave attivi.
        """
        num_active_slaves = len(self.slaves)
        self.get_logger().info(f"Current active slaves: {num_active_slaves}")
        for slave_ns in sorted(self.slaves.keys()):
            slave = self.slaves[slave_ns]
            status = "Assigned" if slave.current_waypoint_index < len(slave.assigned_waypoints) else "Completed"
            self.get_logger().info(f"Slave {slave_ns}: Status: {status}")

def load_full_graph_from_data(graph_data):
    """
    Carica un grafo NetworkX da un dizionario contenente nodi e archi.

    Args:
        graph_data (dict): Dizionario con 'nodes' e 'edges'.

    Returns:
        nx.DiGraph: Il grafo diretto caricato.
    """
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
    node = SlaveNavigationNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
