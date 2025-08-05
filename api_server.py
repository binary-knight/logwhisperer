#!/usr/bin/env python3
"""
LogWhisperer API Server - RESTful API and WebSocket interface
Provides real-time log data and metrics to the web dashboard
Production-ready version with proper application factory
"""

import os
import sys
import json
import time
import threading
import logging
import asyncio
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass, asdict
from collections import deque
import queue

from flask import Flask, jsonify, request, Response
try:
    from flask_cors import CORS
except ImportError:
    # Fallback if flask-cors is not installed
    def CORS(app, **kwargs):
        @app.after_request
        def after_request(response):
            response.headers.add('Access-Control-Allow-Origin', '*')
            response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
            response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
            return response
        return app
from flask_socketio import SocketIO, emit, join_room, leave_room
import yaml

# Import LogWhisperer modules
try:
    from modules.monitor import LogMonitor, LogLevel, LogEntry
    from modules.summarizer import get_summarizer
except ImportError:
    # Development imports
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from modules.monitor import LogMonitor, LogLevel, LogEntry
    from modules.summarizer import get_summarizer

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_PORT = 5124  # API port (different from web UI)
MAX_LOG_BUFFER = 10000
MAX_METRICS_HISTORY = 3600  # 1 hour of metrics

# Global API instance for signal handling
_api_instance = None


@dataclass
class LogEvent:
    """Log event for API consumption"""
    id: str
    timestamp: str
    level: str
    source: str
    message: str
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class MetricPoint:
    """Time series metric point"""
    timestamp: float
    value: float
    

@dataclass
class SystemMetrics:
    """System metrics snapshot"""
    timestamp: str
    logs_per_second: float
    total_logs: int
    error_count: int
    warning_count: int
    error_rate: float
    active_alerts: int
    buffer_size: int
    uptime: float
    

class MetricsStore:
    """Store and aggregate metrics over time"""
    
    def __init__(self, max_history: int = MAX_METRICS_HISTORY):
        self.max_history = max_history
        self.logs_per_second: deque = deque(maxlen=max_history)
        self.error_rates: deque = deque(maxlen=max_history)
        self.total_logs = 0
        self.error_count = 0
        self.warning_count = 0
        self.level_counts = {}
        self.start_time = time.time()
        self._lock = threading.Lock()
        
    def update(self, log_entry: LogEvent) -> None:
        """Update metrics with new log entry"""
        with self._lock:
            self.total_logs += 1
            
            # Count by level
            level = log_entry.level
            self.level_counts[level] = self.level_counts.get(level, 0) + 1
            
            if level in ["ERROR", "CRITICAL", "FATAL"]:
                self.error_count += 1
            elif level == "WARNING":
                self.warning_count += 1
                
    def add_rate_sample(self, logs_per_sec: float, error_rate: float) -> None:
        """Add rate samples"""
        with self._lock:
            now = time.time()
            self.logs_per_second.append(MetricPoint(now, logs_per_sec))
            self.error_rates.append(MetricPoint(now, error_rate))
            
    def get_snapshot(self) -> SystemMetrics:
        """Get current metrics snapshot"""
        with self._lock:
            uptime = time.time() - self.start_time
            current_lps = self.logs_per_second[-1].value if self.logs_per_second else 0
            current_error_rate = self.error_rates[-1].value if self.error_rates else 0
            
            return SystemMetrics(
                timestamp=datetime.now(timezone.utc).isoformat(),
                logs_per_second=current_lps,
                total_logs=self.total_logs,
                error_count=self.error_count,
                warning_count=self.warning_count,
                error_rate=current_error_rate * 100,  # Convert to percentage
                active_alerts=self.level_counts.get("CRITICAL", 0) + self.level_counts.get("FATAL", 0),
                buffer_size=0,  # Will be set by API
                uptime=uptime
            )
            
    def get_time_series(self, metric: str, minutes: int = 5) -> List[Dict[str, Any]]:
        """Get time series data for charts"""
        with self._lock:
            cutoff = time.time() - (minutes * 60)
            
            if metric == "logs_per_second":
                data = self.logs_per_second
            elif metric == "error_rate":
                data = self.error_rates
            else:
                return []
                
            # Filter by time and format for charts
            return [
                {"x": datetime.fromtimestamp(p.timestamp).isoformat(), "y": p.value}
                for p in data
                if p.timestamp > cutoff
            ]


class LogBuffer:
    """Circular buffer for recent logs"""
    
    def __init__(self, maxsize: int = MAX_LOG_BUFFER):
        self.buffer = deque(maxlen=maxsize)
        self._lock = threading.Lock()
        self._id_counter = 0
        
    def add(self, entry: LogEntry) -> LogEvent:
        """Add log entry and return formatted event"""
        with self._lock:
            self._id_counter += 1
            
            event = LogEvent(
                id=f"log_{self._id_counter}",
                timestamp=entry.timestamp.isoformat(),
                level=entry.level.name,
                source=entry.source,
                message=entry.message,
                metadata=entry.metadata
            )
            
            self.buffer.append(event)
            return event
            
    def get_recent(self, count: int = 100, since_id: Optional[str] = None) -> List[LogEvent]:
        """Get recent logs, optionally since a specific ID"""
        with self._lock:
            if since_id:
                # Find the ID and return everything after it
                try:
                    since_num = int(since_id.split('_')[1])
                    return [
                        log for log in self.buffer
                        if int(log.id.split('_')[1]) > since_num
                    ][-count:]
                except (ValueError, IndexError):
                    pass
                    
            return list(self.buffer)[-count:]
            
    def search(self, query: str, count: int = 100) -> List[LogEvent]:
        """Search logs by content"""
        with self._lock:
            query_lower = query.lower()
            results = []
            
            for log in reversed(self.buffer):
                if query_lower in log.message.lower():
                    results.append(log)
                    if len(results) >= count:
                        break
                        
            return results
            
    def get_by_level(self, levels: List[str], count: int = 100) -> List[LogEvent]:
        """Get logs filtered by level"""
        with self._lock:
            results = []
            
            for log in reversed(self.buffer):
                if log.level in levels:
                    results.append(log)
                    if len(results) >= count:
                        break
                        
            return results


class LogWhispererAPI:
    """API server for LogWhisperer dashboard"""
    
    def __init__(self, config_path: str = "/etc/logwhisperer/config.yaml", start_monitor: bool = True):
        self.config_path = config_path
        self.start_monitor_on_init = start_monitor
        
        # Create Flask app
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = os.urandom(24).hex()
        CORS(self.app)  # Enable CORS for API access
        
        # Initialize SocketIO with production settings
        self.socketio = SocketIO(
            self.app, 
            cors_allowed_origins="*", 
            async_mode='eventlet',
            logger=False,
            engineio_logger=False
        )
        
        # Load configuration
        self.config = self._load_config(config_path)
        
        # Initialize components
        self.log_buffer = LogBuffer()
        self.metrics_store = MetricsStore()
        self.monitor: Optional[LogMonitor] = None
        self.monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # WebSocket clients
        self.clients: Set[str] = set()
        
        # Message queue for monitor -> API communication
        self.log_queue: queue.Queue = queue.Queue(maxsize=10000)
        
        # Set up routes
        self._setup_routes()
        self._setup_socketio()
        
        # Store reference for signal handling
        global _api_instance
        _api_instance = self
        
        # Start monitor if requested
        if self.start_monitor_on_init:
            self.start_monitor()
        
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration"""
        try:
            with open(config_path, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Failed to load config from {config_path}: {e}")
            return {}
            
    def _setup_routes(self):
        """Set up REST API routes"""
        
        @self.app.route('/api/health')
        def health_check():
            """Health check endpoint"""
            return jsonify({
                'status': 'healthy',
                'version': '1.0.0',
                'monitor_running': self.monitor is not None and self.monitor_thread.is_alive()
            })
            
        @self.app.route('/api/metrics')
        def get_metrics():
            """Get current system metrics"""
            snapshot = self.metrics_store.get_snapshot()
            snapshot.buffer_size = len(self.log_buffer.buffer)
            return jsonify(asdict(snapshot))
            
        @self.app.route('/api/metrics/history/<metric>')
        def get_metric_history(metric):
            """Get historical metric data"""
            minutes = request.args.get('minutes', 5, type=int)
            data = self.metrics_store.get_time_series(metric, minutes)
            return jsonify({'metric': metric, 'data': data})
            
        @self.app.route('/api/logs')
        def get_logs():
            """Get recent logs with filtering"""
            count = request.args.get('count', 100, type=int)
            since_id = request.args.get('since_id')
            levels = request.args.getlist('level')
            search = request.args.get('search')
            
            if search:
                logs = self.log_buffer.search(search, count)
            elif levels:
                logs = self.log_buffer.get_by_level(levels, count)
            else:
                logs = self.log_buffer.get_recent(count, since_id)
                
            return jsonify({
                'logs': [asdict(log) for log in logs],
                'total': len(logs)
            })
            
        @self.app.route('/api/logs/stats')
        def get_log_stats():
            """Get log statistics"""
            with self.metrics_store._lock:
                return jsonify({
                    'total': self.metrics_store.total_logs,
                    'by_level': self.metrics_store.level_counts,
                    'error_count': self.metrics_store.error_count,
                    'warning_count': self.metrics_store.warning_count
                })
                
        @self.app.route('/api/logs/export')
        def export_logs():
            """Export logs as JSON or CSV"""
            format = request.args.get('format', 'json')
            count = request.args.get('count', 1000, type=int)
            
            logs = self.log_buffer.get_recent(count)
            
            if format == 'csv':
                def generate():
                    yield 'id,timestamp,level,source,message\n'
                    for log in logs:
                        yield f'{log.id},{log.timestamp},{log.level},{log.source},"{log.message}"\n'
                        
                return Response(generate(), mimetype='text/csv',
                              headers={'Content-Disposition': 'attachment; filename=logs.csv'})
            else:
                return jsonify([asdict(log) for log in logs])
                
        @self.app.route('/api/config')
        def get_config():
            """Get sanitized configuration"""
            safe_config = {
                'source': self.config.get('source', 'unknown'),
                'model': self.config.get('model', 'unknown'),
                'monitor': {
                    'escalation_level': self.config.get('monitor', {}).get('escalation_level', 'ERROR'),
                    'batch_size': self.config.get('monitor', {}).get('batch_size', 200)
                }
            }
            return jsonify(safe_config)
            
        @self.app.route('/api/summarize', methods=['POST'])
        def summarize():
            """Summarize a set of logs"""
            data = request.json
            log_ids = data.get('log_ids', [])
            
            if not log_ids:
                return jsonify({'error': 'No log IDs provided'}), 400
                
            # Get logs by IDs
            logs = []
            with self.log_buffer._lock:
                for log in self.log_buffer.buffer:
                    if log.id in log_ids:
                        logs.append(log.message)
                        
            if not logs:
                return jsonify({'error': 'No logs found'}), 404
                
            try:
                summarizer = get_summarizer(self.config)
                response = summarizer.summarize(logs)
                return jsonify({
                    'summary': response.summary,
                    'processing_time': response.processing_time,
                    'line_count': len(logs)
                })
            except Exception as e:
                logger.error(f"Summarization failed: {e}")
                return jsonify({'error': str(e)}), 500
                
    def _setup_socketio(self):
        """Set up WebSocket handlers"""
        
        @self.socketio.on('connect')
        def handle_connect():
            """Client connected"""
            client_id = request.sid
            self.clients.add(client_id)
            join_room('live_logs')
            
            # Send initial data
            emit('connected', {'client_id': client_id})
            
            # Send recent logs
            recent_logs = self.log_buffer.get_recent(50)
            emit('logs_batch', {
                'logs': [asdict(log) for log in recent_logs]
            })
            
            # Send current metrics
            metrics = self.metrics_store.get_snapshot()
            metrics.buffer_size = len(self.log_buffer.buffer)
            emit('metrics_update', asdict(metrics))
            
            logger.info(f"Client connected: {client_id}")
            
        @self.socketio.on('disconnect')
        def handle_disconnect():
            """Client disconnected"""
            client_id = request.sid
            self.clients.discard(client_id)
            leave_room('live_logs')
            logger.info(f"Client disconnected: {client_id}")
            
        @self.socketio.on('subscribe')
        def handle_subscribe(data):
            """Subscribe to specific log levels or sources"""
            levels = data.get('levels', [])
            sources = data.get('sources', [])
            
            # Store subscription preferences (simplified for this example)
            emit('subscribed', {
                'levels': levels,
                'sources': sources
            })
            
    def process_log_entry(self, entry: LogEntry) -> None:
        """Process a log entry from the monitor"""
        # Add to buffer and get formatted event
        event = self.log_buffer.add(entry)
        
        # Update metrics
        self.metrics_store.update(event)
        
        # Emit to connected clients
        self.socketio.emit('new_log', asdict(event), room='live_logs')
        
    def start_monitor(self):
        """Start the log monitor with API integration"""
        if self.monitor:
            logger.warning("Monitor already running")
            return
            
        try:
            # Create monitor instance
            source = self.config.get('source', 'journalctl')
            
            # Create a custom monitor that sends logs to our API
            class APILogMonitor(LogMonitor):
                def __init__(self, api_server, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.api_server = api_server
                    
                def _process_batch(self, entries):
                    # Call parent processing
                    result = super()._process_batch(entries)
                    
                    # Send to API
                    for entry in entries:
                        self.api_server.process_log_entry(entry)
                        
                    return result
                    
            self.monitor = APILogMonitor(
                self,
                source=source,
                file_path=self.config.get('log_file_path'),
                container=self.config.get('docker_container'),
                webhook_url=self.config.get('monitor', {}).get('webhook_url')
            )
            
            # Start monitor in thread
            self.monitor_thread = threading.Thread(
                target=self.monitor.start,
                name="LogMonitorThread",
                daemon=True
            )
            self.monitor_thread.start()
            
            # Start metrics updater
            self._start_metrics_updater()
            
            logger.info("Log monitor started")
            
        except Exception as e:
            logger.error(f"Failed to start monitor: {e}")
            
    def _start_metrics_updater(self):
        """Start background thread to calculate and broadcast metrics"""
        def update_metrics():
            last_total = 0
            
            while not self._stop_event.is_set():
                try:
                    # Calculate rates
                    current_total = self.metrics_store.total_logs
                    logs_per_sec = (current_total - last_total) / 1.0  # 1 second interval
                    last_total = current_total
                    
                    error_rate = (
                        self.metrics_store.error_count / current_total
                        if current_total > 0 else 0
                    )
                    
                    # Store samples
                    self.metrics_store.add_rate_sample(logs_per_sec, error_rate)
                    
                    # Get snapshot and broadcast
                    metrics = self.metrics_store.get_snapshot()
                    metrics.buffer_size = len(self.log_buffer.buffer)
                    
                    self.socketio.emit('metrics_update', asdict(metrics), room='live_logs')
                    
                except Exception as e:
                    logger.error(f"Error updating metrics: {e}")
                    
                self._stop_event.wait(1)  # Update every second
                
        thread = threading.Thread(target=update_metrics, daemon=True)
        thread.start()
        
def create_app(config_path: str = "/etc/logwhisperer/config.yaml"):
    """Create and configure the Flask application for production use"""
    # Create API instance without starting monitor yet
    api = LogWhispererAPI(config_path, start_monitor=False)
    
    # Set up signal handlers
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        if _api_instance:
            _api_instance.stop()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start the monitor after app is created
    api.start_monitor()
    
    return api.app, api.socketio


def run_development_server():
    """Run the development server - DO NOT use in production"""
    import argparse
    
    parser = argparse.ArgumentParser(description="LogWhisperer API Server (Development)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind to")
    parser.add_argument("--config", default="/etc/logwhisperer/config.yaml", help="Config file path")
    
    args = parser.parse_args()
    
    logger.warning("Running in DEVELOPMENT mode. Use gunicorn for production!")
    
    # Create app
    app, socketio = create_app(args.config)
    
    # Run with eventlet
    socketio.run(
        app,
        host=args.host,
        port=args.port,
        debug=True,
        use_reloader=False
    )


if __name__ == "__main__":
    run_development_server()