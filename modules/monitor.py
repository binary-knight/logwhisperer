#!/usr/bin/env python3
"""
LogWhisperer Monitor - Real-time log monitoring with intelligent alerting
Production-ready version with enhanced reliability and performance.
"""

import time
import threading
import yaml
import subprocess
import os
import sys
import re
import signal
import hashlib
import queue
import json
from functools import lru_cache
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter, deque
from typing import List, Dict, Optional, Tuple, Any, Set, Pattern, Union, Deque, Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from contextlib import contextmanager
import logging
from types import FrameType

# Try to import optional modules
try:
    from modules.discord_alert import send_alert as _send_alert
    from modules.summarizer import summarize_log_chunk as _summarize_log_chunk
    
    # Use the imported functions
    send_alert: Callable[..., Any] = _send_alert
    summarize_log_chunk: Callable[..., str] = _summarize_log_chunk
except ImportError as e:
    logging.warning(f"Failed to import module: {e}")

    # Define fallback functions with matching signatures
    def send_alert(
        message: str,
        webhook_url: str,
        level: str = "INFO",
        mentions: Optional[Union[List[str], Dict[str, List[str]]]] = None,
        **kwargs: Any
    ) -> bool:
        logging.error("Alert module not available")
        return False

    def summarize_log_chunk(
        lines: List[str], 
        config: Dict[str, Any], 
        use_cache: bool = True
    ) -> str:
        return "[Summarizer module not available]"


# Constants
__version__ = "1.0.0"

# Configure logging
logger = logging.getLogger(__name__)


class LogLevel(Enum):
    """Log severity levels with numeric values for comparison."""

    INFO = 1
    NOTICE = 2
    WARNING = 3
    ERROR = 4
    CRITICAL = 5
    FATAL = 6

    def __lt__(self, other: object) -> bool:
        if self.__class__ is other.__class__:
            return self.value < other.value  # type: ignore
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if self.__class__ is other.__class__:
            return self.value <= other.value  # type: ignore
        return NotImplemented


@dataclass
class PatternSet:
    """Compiled regex patterns for log classification."""

    patterns: Dict[LogLevel, List[Pattern[str]]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, pattern_dict: Dict[str, List[str]]) -> "PatternSet":
        """Create PatternSet from dictionary of patterns."""
        compiled: Dict[LogLevel, List[Pattern[str]]] = {}
        for level_str, patterns in pattern_dict.items():
            try:
                level = LogLevel[level_str]
                compiled[level] = [re.compile(p, re.IGNORECASE) for p in patterns]
            except (KeyError, re.error) as e:
                logger.warning(f"Failed to compile patterns for {level_str}: {e}")
        return cls(patterns=compiled)


@dataclass
class MonitorConfig:
    """Configuration for log monitoring."""

    enabled: bool = True
    escalation_level: LogLevel = LogLevel.ERROR
    batch_size: int = 200
    sleep_interval: float = 1.0
    batch_timeout: float = 30.0
    webhook_url: Optional[str] = None
    send_full_summary: bool = False
    alert_format: str = "discord"
    discord_mentions: Optional[Union[List[str], Dict[str, List[str]]]] = None

    # Performance settings
    max_buffer_size: int = 10000
    max_line_length: int = 10000

    # Rate limiting
    rate_limit_window: int = 60  # seconds
    rate_limit_max_alerts: int = 10

    # Deduplication
    dedup_window: int = 300  # seconds
    dedup_enabled: bool = True

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")

        if self.sleep_interval <= 0:
            raise ValueError("sleep_interval must be positive")

        if self.batch_timeout <= 0:
            raise ValueError("batch_timeout must be positive")

        if self.webhook_url and not self.webhook_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError("webhook_url must be a valid HTTP(S) URL")


@dataclass
class LogEntry:
    """Structured log entry."""

    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    level: LogLevel = LogLevel.INFO
    source: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        """Hash for deduplication."""
        return hash((self.message, self.level))


class RateLimiter:
    """Rate limiter for alerts."""

    def __init__(self, window_seconds: int, max_events: int):
        self.window_seconds = window_seconds
        self.max_events = max_events
        self.events: Deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """Check if an event is allowed."""
        now = time.time()

        with self._lock:
            # Remove old events
            cutoff = now - self.window_seconds
            while self.events and self.events[0] < cutoff:
                self.events.popleft()

            # Check limit
            if len(self.events) >= self.max_events:
                return False

            # Add new event
            self.events.append(now)
            return True

    def reset(self) -> None:
        """Reset the rate limiter."""
        with self._lock:
            self.events.clear()


class Deduplicator:
    """Deduplicator for log entries."""

    def __init__(self, window_seconds: int):
        self.window_seconds = window_seconds
        self.seen: Dict[int, datetime] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = 60  # Clean old entries every minute
        self._last_cleanup = time.time()

    def is_duplicate(self, entry: LogEntry) -> bool:
        """Check if entry is a duplicate."""
        entry_hash = hash(entry)
        now = datetime.now()

        with self._lock:
            # Periodic cleanup
            if time.time() - self._last_cleanup > self._cleanup_interval:
                self._cleanup(now)

            # Check if seen before
            if entry_hash in self.seen:
                if (now - self.seen[entry_hash]).total_seconds() < self.window_seconds:
                    return True

            # Mark as seen
            self.seen[entry_hash] = now
            return False

    def _cleanup(self, now: datetime) -> None:
        """Remove old entries."""
        cutoff = now - timedelta(seconds=self.window_seconds)
        self.seen = {h: t for h, t in self.seen.items() if t > cutoff}
        self._last_cleanup = time.time()


class LogBuffer:
    """Thread-safe log buffer with overflow protection."""

    def __init__(self, max_size: int = 10000):
        self.max_size = max_size
        self._buffer: List[LogEntry] = []
        self._lock = threading.Lock()
        self._overflow_count = 0

    def add(self, entry: LogEntry) -> bool:
        """Add entry to buffer. Returns False if buffer is full."""
        with self._lock:
            if len(self._buffer) >= self.max_size:
                self._overflow_count += 1
                return False
            self._buffer.append(entry)
            return True

    def flush(self) -> Tuple[List[LogEntry], int]:
        """Flush buffer and return entries with overflow count."""
        with self._lock:
            entries = self._buffer[:]
            overflow = self._overflow_count
            self._buffer.clear()
            self._overflow_count = 0
            return entries, overflow

    def size(self) -> int:
        """Get current buffer size."""
        with self._lock:
            return len(self._buffer)


class LogClassifier:
    """Classify log entries by severity."""

    # Default patterns for log classification
    DEFAULT_PATTERNS = {
        "FATAL": [
            r"\bkernel panic\b",
            r"\bsegfault\b",
            r"\braid failure\b",
            r"\bbus error\b",
            r"\bdouble fault\b",
            r"\bcpu lockup\b",
            r"\bhardware failure\b",
            r"\bsystem crash\b",
        ],
        "CRITICAL": [
            r"\bout of memory\b",
            r"\boom\b",
            r"\bdata corruption\b",
            r"\bkernel bug\b",
            r"\bassertion failed\b",
            r"\bstack trace\b",
            r"\bsegmentation fault\b",
            r"\bpage fault\b",
            r"\bsystem halted\b",
            r"\bdisk full\b",
            r"\bfilesystem full\b",
        ],
        "ERROR": [
            r"\berror\b",
            r"\bfailed\b",
            r"\bexception\b",
            r"\bdenied\b",
            r"\btimeout\b",
            r"\bcannot\b",
            r"\brefused\b",
            r"\babort\b",
            r"\bbroken\b",
            r"\bdisconnected\b",
            r"\bnot found\b",
            r"\bno such file\b",
            r"\bpermission denied\b",
            r"\bconnection reset\b",
            r"\binvalid\b",
            r"\billegal\b",
            r"\bfailure\b",
        ],
        "WARNING": [
            r"\bwarning\b",
            r"\bdeprecated\b",
            r"\bunreachable\b",
            r"\bretrying\b",
            r"\bbackoff\b",
            r"\bslow response\b",
            r"\bmissing\b",
            r"\bunknown\b",
            r"\bunexpected\b",
            r"\bdegraded\b",
            r"\bdelayed\b",
        ],
        "NOTICE": [
            r"\brestarted\b",
            r"\bhigh load\b",
            r"\bdisk usage\b",
            r"\blogin\b",
            r"\bsession opened\b",
            r"\bsession closed\b",
            r"\bservice started\b",
            r"\bservice stopped\b",
            r"\binterface up\b",
            r"\binterface down\b",
            r"\bmount\b",
            r"\bunmount\b",
            r"\bconnected\b",
            r"\bdisconnecting\b",
        ],
    }

    def __init__(self, custom_patterns: Optional[Dict[str, List[str]]] = None):
        """Initialize with optional custom patterns."""
        patterns = self.DEFAULT_PATTERNS.copy()
        if custom_patterns:
            patterns.update(custom_patterns)
        self.pattern_set = PatternSet.from_dict(patterns)

    @lru_cache(maxsize=1024)
    def classify(self, message: str) -> LogLevel:
        """Classify a log message by severity level."""
        message_lower = message.lower()

        # Check patterns in order of severity (most severe first)
        for level in [
            LogLevel.FATAL,
            LogLevel.CRITICAL,
            LogLevel.ERROR,
            LogLevel.WARNING,
            LogLevel.NOTICE,
        ]:
            if level in self.pattern_set.patterns:
                for pattern in self.pattern_set.patterns[level]:
                    if pattern.search(message_lower):
                        return level

        return LogLevel.INFO


class LogMonitor:
    """Enhanced log monitoring system with production-ready features."""

    def __init__(
        self,
        source: str = "journalctl",
        file_path: Optional[str] = None,
        container: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ):
        """Initialize the log monitor."""
        self.source = source
        self.container = container
        self.file_path = Path(file_path) if file_path else None

        # Load configuration
        self.full_config = self._load_config()
        self.config = self._parse_monitor_config(self.full_config)

        # Override webhook URL if provided
        if webhook_url:
            self.config.webhook_url = webhook_url

        # Initialize components
        self.classifier = LogClassifier()
        self.buffer = LogBuffer(self.config.max_buffer_size)
        self.rate_limiter = RateLimiter(
            self.config.rate_limit_window, self.config.rate_limit_max_alerts
        )
        self.deduplicator = Deduplicator(self.config.dedup_window)

        # Threading
        self._stop_event = threading.Event()
        self._process_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._queue: queue.Queue[LogEntry] = queue.Queue()

        # Timing
        self.last_flush_time = time.time()

        # Metrics
        self.metrics: Dict[str, Any] = {
            "lines_processed": 0,
            "batches_processed": 0,
            "alerts_sent": 0,
            "alerts_rate_limited": 0,
            "duplicates_filtered": 0,
            "errors": 0,
            "last_error": None,
            "start_time": time.time(),
        }

        # Set up signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        # Validate configuration
        self._validate_config()

        logger.info(f"LogMonitor initialized for source: {self.source}")

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from various sources."""
        config_paths = [
            os.environ.get("LOGWHISPERER_CONFIG"),
            "/etc/logwhisperer/config.yaml",
            "/opt/logwhisperer/config.yaml",
            "config.yaml",
        ]

        for path in filter(None, config_paths):
            try:
                with open(path, "r") as f:
                    config = yaml.safe_load(f)
                    if config:
                        logger.info(f"Loaded configuration from: {path}")
                        return config
            except Exception as e:
                logger.debug(f"Failed to load config from {path}: {e}")
                continue

        logger.warning("No configuration file found, using defaults")
        return {}

    def _parse_monitor_config(self, full_config: Dict[str, Any]) -> MonitorConfig:
        """Parse monitor configuration from full config."""
        monitor_cfg = full_config.get("monitor", {})

        # Parse escalation level
        escalation_str = monitor_cfg.get("escalation_level", "ERROR").upper()
        try:
            escalation_level = LogLevel[escalation_str]
        except KeyError:
            logger.warning(f"Invalid escalation level: {escalation_str}, using ERROR")
            escalation_level = LogLevel.ERROR

        return MonitorConfig(
            enabled=monitor_cfg.get("enabled", True),
            escalation_level=escalation_level,
            batch_size=monitor_cfg.get("batch_size", 200),
            sleep_interval=monitor_cfg.get("sleep_interval", 1.0),
            batch_timeout=monitor_cfg.get("batch_timeout", 30.0),
            webhook_url=monitor_cfg.get("webhook_url"),
            send_full_summary=monitor_cfg.get("send_full_summary", False),
            alert_format=monitor_cfg.get("alert_format", "discord"),
            discord_mentions=monitor_cfg.get("discord_mentions"),
            max_buffer_size=monitor_cfg.get("max_buffer_size", 10000),
            max_line_length=monitor_cfg.get("max_line_length", 10000),
            rate_limit_window=monitor_cfg.get("rate_limit_window", 60),
            rate_limit_max_alerts=monitor_cfg.get("rate_limit_max_alerts", 10),
            dedup_window=monitor_cfg.get("dedup_window", 300),
            dedup_enabled=monitor_cfg.get("dedup_enabled", True),
        )

    def _validate_config(self) -> None:
        """Validate configuration and environment."""
        if self.source == "file":
            if not self.file_path or not self.file_path.exists():
                raise ValueError(f"Log file {self.file_path} does not exist")

        elif self.source == "docker":
            if not self.container:
                raise ValueError("Docker container name is required")

            # Check Docker availability
            try:
                subprocess.run(
                    ["docker", "version"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=5,
                )
            except (subprocess.CalledProcessError, FileNotFoundError):
                raise ValueError("Docker is not available")

    def _signal_handler(self, signum: int, frame: Optional[FrameType]) -> None:
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.stop()

    def start(self) -> None:
        """Start monitoring."""
        logger.info(f"Starting monitor for source: {self.source}")

        try:
            # Start processor thread
            self._process_thread = threading.Thread(
                target=self._process_loop, name="LogProcessor", daemon=True
            )
            self._process_thread.start()

            # Start monitor thread
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, name="LogMonitor"
            )
            self._monitor_thread.start()

            # Wait for monitor thread
            self._monitor_thread.join()

        except Exception as e:
            logger.exception(f"Fatal error: {e}")
            self.metrics["errors"] += 1
            self.metrics["last_error"] = str(e)
            raise

    def stop(self) -> None:
        """Stop monitoring gracefully."""
        logger.info("Stopping monitor...")
        self._stop_event.set()

        # Process remaining entries
        if self.buffer.size() > 0:
            logger.info("Processing remaining buffered entries...")
            entries, _ = self.buffer.flush()
            if entries:
                self._process_batch(entries)

    def get_metrics(self) -> Dict[str, Any]:
        """Return current metrics."""
        uptime = time.time() - self.metrics["start_time"]
        return {
            **self.metrics,
            "uptime_seconds": uptime,
            "lines_per_second": (
                self.metrics["lines_processed"] / uptime if uptime > 0 else 0
            ),
            "buffer_size": self.buffer.size(),
        }

    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        if self.source == "file":
            self._monitor_file()
        elif self.source == "journalctl":
            self._monitor_journalctl()
        elif self.source == "docker":
            self._monitor_docker_logs()
        else:
            raise ValueError(f"Unknown log source: {self.source}")

    def _process_loop(self) -> None:
        """Process queued log entries."""
        while not self._stop_event.is_set():
            try:
                # Check if batch should be flushed
                if self._should_flush_batch():
                    entries, overflow = self.buffer.flush()
                    if entries:
                        self._process_batch(entries)
                        if overflow > 0:
                            logger.warning(
                                f"Buffer overflow: {overflow} entries dropped"
                            )
                    self.last_flush_time = time.time()

                time.sleep(0.1)  # Small sleep to prevent CPU spinning

            except Exception as e:
                logger.error(f"Error in process loop: {e}")
                self.metrics["errors"] += 1
                self.metrics["last_error"] = str(e)

    def _should_flush_batch(self) -> bool:
        """Determine if the batch should be flushed."""
        return self.buffer.size() >= self.config.batch_size or (
            self.buffer.size() > 0
            and time.time() - self.last_flush_time > self.config.batch_timeout
        )

    def _add_log_entry(self, line: str, source: str = "") -> None:
        """Add a log entry to the buffer."""
        if not line.strip():
            return

        # Truncate extremely long lines
        if len(line) > self.config.max_line_length:
            line = line[: self.config.max_line_length] + "... [truncated]"

        # Create log entry
        level = self.classifier.classify(line)
        entry = LogEntry(message=line, level=level, source=source or self.source)

        # Add to buffer
        if not self.buffer.add(entry):
            logger.warning("Buffer full, dropping log entry")

        self.metrics["lines_processed"] += 1

    def _monitor_file(self) -> None:
        """Monitor a file for new log entries."""
        if not self.file_path:
            logger.error("No file path specified")
            return
            
        logger.info(f"Monitoring file: {self.file_path}")

        while not self._stop_event.is_set():
            try:
                with self.file_path.open("r") as f:
                    # Get file info
                    f.seek(0, 2)  # Go to end
                    inode = os.fstat(f.fileno()).st_ino

                    while not self._stop_event.is_set():
                        # Check for file rotation
                        try:
                            if os.stat(self.file_path).st_ino != inode:
                                logger.info("File rotation detected")
                                break
                        except OSError:
                            logger.warning("File deleted/moved, waiting...")
                            time.sleep(5)
                            break

                        # Read new lines
                        where = f.tell()
                        line = f.readline()
                        if not line:
                            f.seek(where)
                            time.sleep(self.config.sleep_interval)
                        else:
                            self._add_log_entry(line.rstrip(), self.file_path.name)

            except Exception as e:
                logger.error(f"Error monitoring file: {e}")
                self.metrics["errors"] += 1
                self.metrics["last_error"] = str(e)
                time.sleep(5)

    def _monitor_journalctl(self) -> None:
        """Monitor system journal logs."""
        logger.info("Monitoring journalctl logs...")

        cmd = [
            "journalctl",
            "-f",
            "-o",
            "json",
            "--output-fields=MESSAGE,PRIORITY,_SYSTEMD_UNIT,_HOSTNAME",
        ]

        # Add priority filter if configured
        priority = self.full_config.get("priority")
        if priority:
            cmd.extend(["-p", priority.lower()])

        process: Optional[subprocess.Popen[str]] = None
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            # Monitor stderr in background
            def log_stderr() -> None:
                if process and process.stderr:
                    for line in process.stderr:
                        if line.strip():
                            logger.warning(f"journalctl stderr: {line.strip()}")

            stderr_thread = threading.Thread(target=log_stderr, daemon=True)
            stderr_thread.start()

            # Read stdout
            if process.stdout:
                for line in process.stdout:
                    if self._stop_event.is_set():
                        break

                    if not line.strip():
                        continue

                    try:
                        # Parse JSON
                        data = json.loads(line)
                        message = data.get("MESSAGE", "")
                        if message:
                            # Add metadata
                            unit = data.get("_SYSTEMD_UNIT", "")
                            hostname = data.get("_HOSTNAME", "")
                            source = f"journalctl:{unit}" if unit else "journalctl"

                            self._add_log_entry(message, source)

                    except json.JSONDecodeError:
                        # Fallback to plain text
                        self._add_log_entry(line.strip(), "journalctl")

        except Exception as e:
            logger.error(f"Error monitoring journalctl: {e}")
            self.metrics["errors"] += 1
            self.metrics["last_error"] = str(e)
        finally:
            if process:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

    def _monitor_docker_logs(self) -> None:
        """Monitor Docker container logs."""
        if not self.container:
            logger.error("No container specified")
            return
            
        logger.info(f"Monitoring Docker container: {self.container}")

        cmd = [
            "docker",
            "logs",
            "-f",
            "--tail",
            "0",
            "--timestamps",
            "--details",
            self.container,
        ]

        process: Optional[subprocess.Popen[str]] = None
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Combine stdout and stderr
                text=True,
                bufsize=1,
            )

            if process.stdout:
                for line in process.stdout:
                    if self._stop_event.is_set():
                        break

                    if not line.strip():
                        continue

                    # Parse timestamp if present
                    parts = line.split(" ", 1)
                    if len(parts) == 2 and "T" in parts[0]:
                        _, message = parts
                        self._add_log_entry(message.strip(), f"docker:{self.container}")
                    else:
                        self._add_log_entry(line.strip(), f"docker:{self.container}")

        except Exception as e:
            logger.error(f"Error monitoring Docker logs: {e}")
            self.metrics["errors"] += 1
            self.metrics["last_error"] = str(e)
        finally:
            if process:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

    def _process_batch(self, entries: List[LogEntry]) -> None:
        """Process a batch of log entries."""
        try:
            logger.info(f"Processing batch of {len(entries)} entries")
            self.metrics["batches_processed"] += 1

            # Filter by severity
            escalated_entries = [
                e for e in entries if e.level >= self.config.escalation_level
            ]

            # Apply deduplication
            if self.config.dedup_enabled:
                unique_entries = []
                for entry in escalated_entries:
                    if not self.deduplicator.is_duplicate(entry):
                        unique_entries.append(entry)
                    else:
                        self.metrics["duplicates_filtered"] += 1
                escalated_entries = unique_entries

            if not escalated_entries and not self.config.send_full_summary:
                logger.debug("No escalated entries to alert on")
                return

            # Check rate limit
            if not self.rate_limiter.allow():
                logger.warning("Alert rate limited")
                self.metrics["alerts_rate_limited"] += 1
                return

            # Prepare summaries
            all_messages = [e.message for e in entries]
            escalated_messages = [e.message for e in escalated_entries]

            # Generate summaries
            full_summary = ""
            escalated_summary = ""

            if self.config.send_full_summary and all_messages:
                full_summary = summarize_log_chunk(all_messages, self.full_config)

            if escalated_messages:
                escalated_summary = summarize_log_chunk(
                    escalated_messages, self.full_config
                )

            # Prepare alert content
            alert_content = self._format_alert(
                entries, escalated_entries, full_summary, escalated_summary
            )

            if not alert_content:
                logger.debug("No content to alert")
                return

            # Send alert
            if self.config.webhook_url:
                # Determine alert level
                max_level = max(
                    (e.level for e in escalated_entries), default=LogLevel.INFO
                )

                # Get mentions from config
                mentions = self.config.discord_mentions

                send_alert(
                    alert_content,
                    self.config.webhook_url,
                    level=max_level.name,
                    mentions=mentions,
                )
                self.metrics["alerts_sent"] += 1
            else:
                logger.warning("No webhook URL configured")

        except Exception as e:
            logger.error(f"Error processing batch: {e}")
            self.metrics["errors"] += 1
            self.metrics["last_error"] = str(e)

    def _format_alert(
        self,
        all_entries: List[LogEntry],
        escalated_entries: List[LogEntry],
        full_summary: str,
        escalated_summary: str,
    ) -> str:
        """Format alert content."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        source_label = (
            self.file_path.name if self.file_path else self.container or self.source
        )

        # Count by level
        level_counts = Counter(e.level.name for e in all_entries)
        escalated_counts = Counter(e.level.name for e in escalated_entries)

        content = f"**📄 Source:** `{source_label}`\n"
        content += f"**🕒 Time:** `{timestamp}`\n"
        content += f"**📊 Total Entries:** {len(all_entries)}\n"

        # Level breakdown
        if level_counts:
            content += "\n**Level Breakdown:**\n"
            for level in [
                LogLevel.FATAL,
                LogLevel.CRITICAL,
                LogLevel.ERROR,
                LogLevel.WARNING,
                LogLevel.NOTICE,
                LogLevel.INFO,
            ]:
                count = level_counts.get(level.name, 0)
                if count > 0:
                    emoji = self._get_level_emoji(level)
                    content += f"{emoji} {level.name}: {count}\n"

        # Add summaries
        if self.config.send_full_summary and full_summary:
            content += f"\n**🧠 Batch Summary:**\n{full_summary.strip()}\n"

        if escalated_summary:
            content += (
                f"\n**⚠️ Escalated Findings ({len(escalated_entries)} entries):**\n"
            )
            content += f"{escalated_summary.strip()}\n"

        # Add sample messages for context
        if escalated_entries and len(escalated_entries) <= 5:
            content += "\n**Sample Messages:**\n"
            for entry in escalated_entries[:5]:
                level_emoji = self._get_level_emoji(entry.level)
                # Truncate long messages
                msg = entry.message
                if len(msg) > 200:
                    msg = msg[:197] + "..."
                content += f"{level_emoji} {msg}\n"

        return content

    def _get_level_emoji(self, level: LogLevel) -> str:
        """Get emoji for log level."""
        emoji_map = {
            LogLevel.FATAL: "💀",
            LogLevel.CRITICAL: "🔴",
            LogLevel.ERROR: "❌",
            LogLevel.WARNING: "⚠️",
            LogLevel.NOTICE: "ℹ️",
            LogLevel.INFO: "📝",
        }
        return emoji_map.get(level, "•")


def main() -> None:
    """Main function for testing."""
    import argparse

    parser = argparse.ArgumentParser(description="LogWhisperer Monitor")
    parser.add_argument(
        "--source", default="journalctl", choices=["journalctl", "file", "docker"]
    )
    parser.add_argument("--file", help="Log file path")
    parser.add_argument("--container", help="Docker container name")
    parser.add_argument("--webhook", help="Discord webhook URL")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    try:
        monitor = LogMonitor(
            source=args.source,
            file_path=args.file,
            container=args.container,
            webhook_url=args.webhook,
        )
        monitor.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()