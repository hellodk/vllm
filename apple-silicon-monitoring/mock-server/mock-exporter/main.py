#!/usr/bin/env python3
"""
Mock Apple Silicon Exporter

Simulates the apple-silicon-exporter for testing the monitoring pipeline.
Generates realistic metrics for GPU, thermal, power, and system stats.
"""

import random
import math
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# Configuration
PORT = 5900
UPDATE_INTERVAL = 5  # seconds

# Simulated state
class MockAppleSiliconState:
    def __init__(self, node_id: int = 1):
        self.node_id = node_id
        self.start_time = time.time()
        
        # GPU state
        self.gpu_base_utilization = random.uniform(30, 70)
        self.gpu_memory_total = 16 * 1024 * 1024 * 1024  # 16GB
        self.gpu_memory_base = random.uniform(0.4, 0.7) * self.gpu_memory_total
        
        # Thermal state
        self.thermal_level = "nominal"
        self.cpu_throttled = False
        self.gpu_throttled = False
        
        # Power state
        self.base_power = random.uniform(25, 45)
        
    def update(self):
        """Update state with realistic variations"""
        t = time.time() - self.start_time
        
        # Simulate workload patterns (sinusoidal with noise)
        workload_factor = 0.5 + 0.3 * math.sin(t / 60) + 0.2 * math.sin(t / 300)
        workload_factor = max(0.1, min(1.0, workload_factor + random.gauss(0, 0.1)))
        
        # GPU utilization varies with workload
        self.gpu_utilization = min(100, max(0, 
            self.gpu_base_utilization * workload_factor * (1 + random.gauss(0, 0.15))
        ))
        
        # GPU memory correlates with utilization
        memory_factor = 0.5 + 0.5 * (self.gpu_utilization / 100)
        self.gpu_memory_used = int(self.gpu_memory_base * memory_factor * (1 + random.gauss(0, 0.05)))
        self.gpu_memory_used = min(self.gpu_memory_total, max(0, self.gpu_memory_used))
        
        # GPU temperature correlates with utilization
        self.gpu_temperature = 40 + 40 * (self.gpu_utilization / 100) + random.gauss(0, 2)
        self.gpu_temperature = max(35, min(95, self.gpu_temperature))
        
        # Power consumption
        self.system_power = self.base_power + 40 * (self.gpu_utilization / 100) + random.gauss(0, 3)
        self.gpu_power = self.system_power * 0.6
        self.ecpu_power = self.system_power * 0.1
        self.pcpu_power = self.system_power * 0.2
        self.ane_power = self.system_power * 0.05
        
        # ANE utilization (occasionally active)
        self.ane_utilization = random.choice([0, 0, 0, random.uniform(10, 50)])
        
        # Thermal state (occasionally throttle)
        if self.gpu_temperature > 85:
            self.thermal_level = "heavy"
            self.gpu_throttled = random.random() > 0.3
        elif self.gpu_temperature > 75:
            self.thermal_level = "moderate"
            self.gpu_throttled = random.random() > 0.8
        else:
            self.thermal_level = "nominal"
            self.gpu_throttled = False
            
        self.cpu_throttled = self.gpu_throttled and random.random() > 0.5
        
        # System memory
        self.memory_total = 32 * 1024 * 1024 * 1024  # 32GB
        self.memory_used = int(self.memory_total * random.uniform(0.3, 0.6))
        
        # CPU usage
        self.cpu_user = random.uniform(5, 30)
        self.cpu_system = random.uniform(2, 15)
        self.cpu_idle = 100 - self.cpu_user - self.cpu_system

# Global state
state = MockAppleSiliconState()

def generate_metrics():
    """Generate Prometheus format metrics"""
    state.update()
    
    hostname = f"mac-mini-mock-{state.node_id:03d}"
    
    metrics = []
    
    # GPU metrics
    metrics.append(f'# HELP apple_gpu_utilization_percent GPU utilization percentage')
    metrics.append(f'# TYPE apple_gpu_utilization_percent gauge')
    metrics.append(f'apple_gpu_utilization_percent{{gpu="0",instance="{hostname}"}} {state.gpu_utilization:.2f}')
    
    metrics.append(f'# HELP apple_gpu_memory_used_bytes GPU memory currently in use')
    metrics.append(f'# TYPE apple_gpu_memory_used_bytes gauge')
    metrics.append(f'apple_gpu_memory_used_bytes{{gpu="0",instance="{hostname}"}} {state.gpu_memory_used}')
    
    metrics.append(f'# HELP apple_gpu_memory_total_bytes Total GPU memory available')
    metrics.append(f'# TYPE apple_gpu_memory_total_bytes gauge')
    metrics.append(f'apple_gpu_memory_total_bytes{{gpu="0",instance="{hostname}"}} {state.gpu_memory_total}')
    
    metrics.append(f'# HELP apple_gpu_temperature_celsius GPU temperature in Celsius')
    metrics.append(f'# TYPE apple_gpu_temperature_celsius gauge')
    metrics.append(f'apple_gpu_temperature_celsius{{gpu="0",instance="{hostname}"}} {state.gpu_temperature:.1f}')
    
    metrics.append(f'# HELP apple_gpu_power_watts GPU power consumption in watts')
    metrics.append(f'# TYPE apple_gpu_power_watts gauge')
    metrics.append(f'apple_gpu_power_watts{{gpu="0",instance="{hostname}"}} {state.gpu_power:.2f}')
    
    # CPU power metrics
    metrics.append(f'# HELP apple_cpu_power_watts CPU cluster power consumption in watts')
    metrics.append(f'# TYPE apple_cpu_power_watts gauge')
    metrics.append(f'apple_cpu_power_watts{{cluster="efficiency",instance="{hostname}"}} {state.ecpu_power:.2f}')
    metrics.append(f'apple_cpu_power_watts{{cluster="performance",instance="{hostname}"}} {state.pcpu_power:.2f}')
    
    # ANE metrics
    metrics.append(f'# HELP apple_ane_power_watts Apple Neural Engine power consumption in watts')
    metrics.append(f'# TYPE apple_ane_power_watts gauge')
    metrics.append(f'apple_ane_power_watts{{instance="{hostname}"}} {state.ane_power:.2f}')
    
    metrics.append(f'# HELP apple_ane_utilization_percent Apple Neural Engine utilization percentage')
    metrics.append(f'# TYPE apple_ane_utilization_percent gauge')
    metrics.append(f'apple_ane_utilization_percent{{instance="{hostname}"}} {state.ane_utilization:.2f}')
    
    # System power
    metrics.append(f'# HELP apple_system_power_watts Total system power consumption in watts')
    metrics.append(f'# TYPE apple_system_power_watts gauge')
    metrics.append(f'apple_system_power_watts{{instance="{hostname}"}} {state.system_power:.2f}')
    
    # Thermal metrics
    metrics.append(f'# HELP apple_thermal_pressure Thermal pressure level')
    metrics.append(f'# TYPE apple_thermal_pressure gauge')
    for level in ["nominal", "moderate", "heavy", "critical"]:
        value = 1 if level == state.thermal_level else 0
        metrics.append(f'apple_thermal_pressure{{level="{level}",instance="{hostname}"}} {value}')
    
    metrics.append(f'# HELP apple_thermal_throttle_active Whether thermal throttling is active')
    metrics.append(f'# TYPE apple_thermal_throttle_active gauge')
    metrics.append(f'apple_thermal_throttle_active{{type="cpu",instance="{hostname}"}} {1 if state.cpu_throttled else 0}')
    metrics.append(f'apple_thermal_throttle_active{{type="gpu",instance="{hostname}"}} {1 if state.gpu_throttled else 0}')
    
    # Memory metrics
    metrics.append(f'# HELP apple_memory_used_bytes System memory in use')
    metrics.append(f'# TYPE apple_memory_used_bytes gauge')
    metrics.append(f'apple_memory_used_bytes{{instance="{hostname}"}} {state.memory_used}')
    
    metrics.append(f'# HELP apple_memory_total_bytes Total system memory')
    metrics.append(f'# TYPE apple_memory_total_bytes gauge')
    metrics.append(f'apple_memory_total_bytes{{instance="{hostname}"}} {state.memory_total}')
    
    # CPU usage
    metrics.append(f'# HELP apple_cpu_usage_percent CPU usage percentage')
    metrics.append(f'# TYPE apple_cpu_usage_percent gauge')
    metrics.append(f'apple_cpu_usage_percent{{cpu="0",mode="user",instance="{hostname}"}} {state.cpu_user:.2f}')
    metrics.append(f'apple_cpu_usage_percent{{cpu="0",mode="system",instance="{hostname}"}} {state.cpu_system:.2f}')
    metrics.append(f'apple_cpu_usage_percent{{cpu="0",mode="idle",instance="{hostname}"}} {state.cpu_idle:.2f}')
    
    # Scrape metadata
    metrics.append(f'# HELP apple_scrape_success Whether the scrape was successful')
    metrics.append(f'# TYPE apple_scrape_success gauge')
    metrics.append(f'apple_scrape_success{{collector="iokit",instance="{hostname}"}} 1')
    metrics.append(f'apple_scrape_success{{collector="powermetrics",instance="{hostname}"}} 1')
    metrics.append(f'apple_scrape_success{{collector="metal",instance="{hostname}"}} 1')
    metrics.append(f'apple_scrape_success{{collector="system",instance="{hostname}"}} 1')
    
    metrics.append(f'# HELP apple_scrape_duration_seconds Duration of the scrape')
    metrics.append(f'# TYPE apple_scrape_duration_seconds gauge')
    metrics.append(f'apple_scrape_duration_seconds{{collector="iokit",instance="{hostname}"}} {random.uniform(0.01, 0.05):.4f}')
    metrics.append(f'apple_scrape_duration_seconds{{collector="powermetrics",instance="{hostname}"}} {random.uniform(0.5, 1.5):.4f}')
    metrics.append(f'apple_scrape_duration_seconds{{collector="metal",instance="{hostname}"}} {random.uniform(0.01, 0.03):.4f}')
    metrics.append(f'apple_scrape_duration_seconds{{collector="system",instance="{hostname}"}} {random.uniform(0.005, 0.02):.4f}')
    
    return '\n'.join(metrics) + '\n'


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/metrics':
            metrics = generate_metrics()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', len(metrics))
            self.end_headers()
            self.wfile.write(metrics.encode('utf-8'))
        elif self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress request logging
        pass


def main():
    import os
    global state
    node_id = int(os.environ.get('NODE_ID', '1'))
    port = int(os.environ.get('PORT', str(PORT)))
    
    state = MockAppleSiliconState(node_id=node_id)
    
    server = HTTPServer(('0.0.0.0', port), MetricsHandler)
    print(f"Mock Apple Silicon Exporter starting on port {port} (node {node_id})")
    print(f"Metrics available at http://localhost:{port}/metrics")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == '__main__':
    main()
