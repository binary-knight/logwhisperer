#!/usr/bin/env python3
"""
LogWhisperer OSS - Open Source Log Summarization Tool
Enhanced version with integrated advanced summarization capabilities.
"""

import yaml
import subprocess
import json
import os
import sys
import time
import signal
import argparse
import requests
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union, TextIO, TYPE_CHECKING
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from contextlib import contextmanager
import logging
from types import FrameType

# Import the enhanced summarizer
if TYPE_CHECKING:
    from modules.summarizer import OllamaSummarizer, SummaryResponse, LogAnalyzer
else:
    try:
        # Try the compiled module path first
        from modules.summarizer import OllamaSummarizer, SummaryResponse, LogAnalyzer
    except ImportError:
        try:
            # Fallback for development/testing
            from summarizer import OllamaSummarizer, SummaryResponse, LogAnalyzer
        except ImportError:
            print("Error: summarizer module not found.")
            sys.exit(1)

# Version constant
__version__ = "1.0.0"

# Configure logging
logger = logging.getLogger(__name__)


class LogSource(Enum):
    """Enumeration of supported log sources."""
    JOURNALCTL = "journalctl"
    FILE = "file"
    DOCKER = "docker"


class Priority(Enum):
    """Systemd journal priority levels."""
    EMERG = "emerg"
    ALERT = "alert"
    CRIT = "crit"
    ERR = "err"
    WARNING = "warning"
    NOTICE = "notice"
    INFO = "info"
    DEBUG = "debug"


@dataclass
class Config:
    """Configuration data class with validation."""
    model: str = "mistral"
    source: str = "journalctl"
    log_file_path: str = "/var/log/syslog"
    priority: str = "err"
    entries: int = 500
    timeout: int = 60
    docker_container: Optional[str] = None
    ollama_host: str = "http://localhost:11434"
    lines_per_prompt: int = 50
    prompt: Optional[str] = None
    report_dir: str = "reports"
    cache_ttl: int = 300  # Cache TTL for summarizer
    analyze_logs: bool = True  # Whether to analyze logs before summarization
    use_cache: bool = True  # Whether to use caching

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        # Validate source
        if self.source not in [s.value for s in LogSource]:
            raise ValueError(f"Invalid source: {self.source}")

        # Validate priority
        if self.priority not in [p.value for p in Priority]:
            raise ValueError(f"Invalid priority: {self.priority}")

        # Validate numeric ranges
        if self.entries < 1 or self.entries > 100000:
            raise ValueError("entries must be between 1 and 100000")

        if self.timeout < 1 or self.timeout > 3600:
            raise ValueError("timeout must be between 1 and 3600 seconds")

        if self.lines_per_prompt < 1 or self.lines_per_prompt > 1000:
            raise ValueError("lines_per_prompt must be between 1 and 1000")

        if self.cache_ttl < 0 or self.cache_ttl > 86400:
            raise ValueError("cache_ttl must be between 0 and 86400 seconds")


class LogWhispererError(Exception):
    """Base exception for LogWhisperer errors."""
    pass


class OllamaError(LogWhispererError):
    """Exception for Ollama-related errors."""
    pass


class LogSourceError(LogWhispererError):
    """Exception for log source errors."""
    pass


class Spinner:
    """Enhanced spinner with context manager support."""
    def __init__(self, message: str = "Processing"):
        self.message = message
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "Spinner":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def start(self) -> None:
        """Start the spinner."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        """Spin animation loop."""
        chars = "|/-\\"
        idx = 0

        while self._running:
            if sys.stdout.isatty():  # Only show spinner in terminal
                sys.stdout.write(f"\r{self.message}... {chars[idx % len(chars)]}")
                sys.stdout.flush()
            time.sleep(0.1)
            idx += 1

    def stop(self) -> None:
        """Stop the spinner."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=0.5)

        if sys.stdout.isatty():
            sys.stdout.write("\r" + " " * (len(self.message) + 10) + "\r")
            sys.stdout.flush()


class ModelManager:
    """Manage Ollama models."""
    def __init__(self, host: str = "http://localhost:11434", timeout: int = 60):
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": f"LogWhisperer/{__version__}"})

    def __enter__(self) -> "ModelManager":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._session.close()

    @lru_cache(maxsize=32)
    def list_models(self) -> List[str]:
        """List available models."""
        try:
            response = self._session.get(f"{self.host}/api/tags", timeout=10)
            response.raise_for_status()
            data = response.json()
            return [model.get("name", "") for model in data.get("models", [])]
        except requests.RequestException as e:
            raise OllamaError(f"Failed to list models: {e}")

    def model_exists(self, model: str) -> bool:
        """Check if a model exists."""
        try:
            response = self._session.post(
                f"{self.host}/api/show", json={"name": model}, timeout=10
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def pull_model(self, model: str, progress_callback: Optional[Any] = None) -> bool:
        """Pull a model with optional progress callback."""
        logger.info(f"Pulling model '{model}'...")

        try:
            response = self._session.post(
                f"{self.host}/api/pull",
                json={"name": model, "stream": True},
                stream=True,
                timeout=300,
            )
            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    data = json.loads(line)
                    if progress_callback and "status" in data:
                        progress_callback(data)

                    if data.get("status") == "success":
                        logger.info("Model pulled successfully")
                        return True

            return False

        except requests.RequestException as e:
            raise OllamaError(f"Failed to pull model '{model}': {e}")


class LogReader:
    """Unified log reader for different sources."""
    def __init__(self, source: LogSource, config: Config):
        self.source = source
        self.config = config
        self._stop_event = threading.Event()

    def read_logs(self) -> List[Dict[str, Any]]:
        """Read logs from the configured source."""
        if self.source == LogSource.JOURNALCTL:
            return self._read_journalctl()
        elif self.source == LogSource.FILE:
            return self._read_file()
        elif self.source == LogSource.DOCKER:
            return self._read_docker()
        else:
            raise LogSourceError(f"Unknown source: {self.source}")

    def _read_journalctl(self) -> List[Dict[str, Any]]:
        """Read from systemd journal."""
        cmd = [
            "journalctl",
            "-p", self.config.priority,
            "-n", str(self.config.entries),
            "--output", "json",
            "--no-pager",
        ]

        try:
            logger.debug(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                timeout=30,
            )

            if not result.stdout.strip():
                return []

            logs: List[Dict[str, Any]] = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    try:
                        logs.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse journal line: {e}")

            return logs

        except subprocess.CalledProcessError as e:
            raise LogSourceError(f"journalctl failed: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise LogSourceError("journalctl command timed out")

    def _read_file(self) -> List[Dict[str, Any]]:
        """Read from a log file."""
        path = Path(self.config.log_file_path)

        if not path.exists():
            raise LogSourceError(f"Log file does not exist: {path}")

        if not path.is_file():
            raise LogSourceError(f"Path is not a file: {path}")

        if not os.access(path, os.R_OK):
            raise LogSourceError(f"Cannot read file: {path}")

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                # Read last N lines efficiently
                lines: List[str] = []

                # For small files, just read all and slice
                if path.stat().st_size < 10 * 1024 * 1024:  # 10MB
                    lines = f.readlines()[-self.config.entries:]
                else:
                    # For large files, use a more efficient approach
                    lines = self._tail_file(f, self.config.entries)

            # Convert to log format
            logs: List[Dict[str, Any]] = []
            for line in lines:
                line = line.strip()
                if line:
                    logs.append({
                        "__REALTIME_TIMESTAMP": str(int(time.time() * 1000000)),
                        "MESSAGE": line,
                        "_SOURCE": "file",
                    })

            return logs

        except IOError as e:
            raise LogSourceError(f"Failed to read file: {e}")

    def _tail_file(self, file_obj: TextIO, num_lines: int) -> List[str]:
        """Efficiently read last N lines from a file."""
        BLOCK_SIZE = 1024
        blocks: List[str] = []
        lines_found: List[str] = []

        file_obj.seek(0, 2)  # Go to end
        file_length = file_obj.tell()

        while len(lines_found) < num_lines and file_length > 0:
            # Calculate block size
            if file_length < BLOCK_SIZE:
                block_size = file_length
            else:
                block_size = BLOCK_SIZE

            file_obj.seek(file_length - block_size)
            blocks.append(file_obj.read(block_size))

            lines_found = "".join(reversed(blocks)).splitlines()
            file_length -= block_size

        return lines_found[-num_lines:]

    def _read_docker(self) -> List[Dict[str, Any]]:
        """Read from Docker container logs."""
        if not self.config.docker_container:
            raise LogSourceError("Docker container name not specified")

        # Check if docker is available
        try:
            subprocess.run(
                ["docker", "version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=5,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise LogSourceError("Docker is not available")

        cmd = [
            "docker", "logs",
            "--tail", str(self.config.entries),
            "--timestamps",
            self.config.docker_container,
        ]

        try:
            logger.debug(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                timeout=30,
            )

            logs: List[Dict[str, Any]] = []
            for line in result.stdout.strip().splitlines():
                if line:
                    # Parse timestamp if present
                    parts = line.split(" ", 1)
                    if len(parts) == 2 and "T" in parts[0]:
                        timestamp_str, message = parts
                        try:
                            timestamp = datetime.fromisoformat(
                                timestamp_str.replace("Z", "+00:00")
                            )
                            logs.append({
                                "__REALTIME_TIMESTAMP": str(
                                    int(timestamp.timestamp() * 1000000)
                                ),
                                "MESSAGE": message,
                                "_SOURCE": "docker",
                                "_CONTAINER": self.config.docker_container,
                            })
                        except ValueError:
                            # If timestamp parsing fails, use whole line
                            logs.append({
                                "MESSAGE": line,
                                "_SOURCE": "docker",
                                "_CONTAINER": self.config.docker_container,
                            })
                    else:
                        logs.append({
                            "MESSAGE": line,
                            "_SOURCE": "docker",
                            "_CONTAINER": self.config.docker_container,
                        })

            return logs

        except subprocess.CalledProcessError as e:
            # Check if container exists
            if "No such container" in e.stderr:
                raise LogSourceError(
                    f"Container not found: {self.config.docker_container}"
                )
            raise LogSourceError(f"Docker logs failed: {e.stderr}")


class ReportGenerator:
    """Generate and save summary reports."""
    def __init__(self, report_dir: str = "reports"):
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(exist_ok=True)

    def save_summary(
        self,
        summary_response: SummaryResponse,
        raw_logs: List[Dict[str, Any]],
        config: Config,
        mode: str = "single"
    ) -> Path:
        """Save enhanced summary to a markdown file."""
        timestamp = datetime.now()
        filename = f"log_summary_{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}.md"
        filepath = self.report_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            # Header
            f.write(f"# Log Summary Report\n\n")
            f.write(f"**Generated:** {timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Version:** LogWhisperer v{__version__}\n")
            f.write(f"**Processing Time:** {summary_response.processing_time:.2f}s\n")
            if summary_response.cached:
                f.write("**Source:** Cached result\n")

            # Configuration
            f.write("\n## Configuration\n\n")
            f.write(f"- **Source:** {config.source}\n")
            f.write(f"- **Model:** {config.model}\n")
            f.write(f"- **Mode:** {mode}\n")
            f.write(f"- **Entries Processed:** {len(raw_logs)}\n")
            if config.source == "journalctl":
                f.write(f"- **Priority:** {config.priority}\n")
            elif config.source == "docker":
                f.write(f"- **Container:** {config.docker_container}\n")
            elif config.source == "file":
                f.write(f"- **File:** {config.log_file_path}\n")

            # Log Analysis (if available)
            if summary_response.metadata:
                f.write("\n## 📊 Log Analysis\n\n")
                
                if "error_keywords" in summary_response.metadata:
                    f.write(f"- **Error Keywords Found:** {summary_response.metadata['error_keywords']}\n")
                if "warning_keywords" in summary_response.metadata:
                    f.write(f"- **Warning Keywords Found:** {summary_response.metadata['warning_keywords']}\n")
                
                if "time_range" in summary_response.metadata and summary_response.metadata["time_range"]:
                    time_range = summary_response.metadata["time_range"]
                    f.write(f"- **Time Range:** {time_range['duration']}\n")
                    f.write(f"  - Start: {time_range['start']}\n")
                    f.write(f"  - End: {time_range['end']}\n")
                
                if "common_ips" in summary_response.metadata and summary_response.metadata["common_ips"]:
                    f.write("\n### Most Common IPs\n")
                    for ip, count in summary_response.metadata["common_ips"]:
                        f.write(f"- {ip}: {count} occurrences\n")
                
                if "common_patterns" in summary_response.metadata and summary_response.metadata["common_patterns"]:
                    f.write("\n### Common Patterns\n")
                    for pattern_info in summary_response.metadata["common_patterns"][:5]:
                        f.write(f"- Pattern ({pattern_info['count']}x): `{pattern_info['pattern']}`\n")

            # Summary
            f.write("\n## 🔍 Summary\n\n")
            f.write(summary_response.summary + "\n")

            # Error information if present
            if summary_response.error:
                f.write("\n## ⚠️ Errors\n\n")
                f.write(f"An error occurred during summarization: {summary_response.error}\n")

            # Raw messages sample
            f.write("\n## 📜 Raw Log Sample\n\n")
            if len(raw_logs) > 100:
                f.write(f"*Showing last 100 of {len(raw_logs)} messages*\n\n")

            for log in raw_logs[-100:]:
                message_text = log.get("MESSAGE", str(log))
                # Escape markdown special characters
                message_text = message_text.replace("|", "\\|")
                f.write(f"- {message_text}\n")

        logger.info(f"Summary saved to: {filepath}")
        return filepath


class LogWhisperer:
    """Main LogWhisperer application class with enhanced summarization."""
    def __init__(self, config: Config):
        self.config = config
        
        # Initialize the enhanced summarizer
        self.summarizer = OllamaSummarizer(
            host=config.ollama_host,
            model=config.model,
            timeout=config.timeout,
            cache_ttl=config.cache_ttl
        )
        
        # Initialize other components
        self.model_manager = ModelManager(config.ollama_host, config.timeout)
        self.log_reader = LogReader(LogSource(config.source), config)
        self.report_generator = ReportGenerator(config.report_dir)
        self._shutdown = threading.Event()

        # Set up signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum: int, frame: Optional[FrameType]) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self._shutdown.set()

    def ensure_model_available(self) -> None:
        """Ensure the configured model is available."""
        if not self.summarizer.ensure_model_available():
            logger.info(f"Model '{self.config.model}' not found locally")

            def progress_callback(data: Dict[str, Any]) -> None:
                status = data.get("status", "")
                if "pulling" in status and "completed" in data and "total" in data:
                    completed = data["completed"]
                    total = data["total"]
                    percent = (completed / total * 100) if total > 0 else 0
                    sys.stdout.write(f"\rPulling model: {percent:.1f}%")
                    sys.stdout.flush()

            try:
                self.model_manager.pull_model(self.config.model, progress_callback)
                sys.stdout.write("\n")
            except OllamaError as e:
                logger.error(f"Failed to pull model: {e}")
                raise

    def summarize_logs(self, logs: List[Dict[str, Any]]) -> SummaryResponse:
        """Summarize log entries using the enhanced summarizer."""
        # Extract messages
        messages: List[str] = []
        for entry in logs:
            if "MESSAGE" in entry:
                messages.append(entry["MESSAGE"])

        if not messages:
            return SummaryResponse(
                summary="No log messages found to summarize.",
                model=self.config.model,
                processing_time=0.0,
                line_count=0,
                cached=False
            )

        logger.info(f"Summarizing {len(messages)} log entries...")

        # Use the enhanced summarizer
        response = self.summarizer.summarize(
            lines=messages,
            prompt_template=self.config.prompt,
            use_cache=self.config.use_cache,
            analyze_first=self.config.analyze_logs
        )

        return response

    def run_once(self) -> None:
        """Run a single summarization."""
        try:
            # Ensure model is available
            self.ensure_model_available()

            # Read logs
            with Spinner("Reading log entries"):
                logs = self.log_reader.read_logs()

            if not logs:
                logger.info("No log entries found")
                return

            logger.info(f"{len(logs)} log entries retrieved")

            # Generate summary
            with Spinner("Generating summary"):
                summary_response = self.summarize_logs(logs)

            # Display summary
            print("\n" + "=" * 50)
            print("SUMMARY")
            print("=" * 50 + "\n")
            print(summary_response.summary)
            print("\n" + "=" * 50)
            
            # Display cache stats
            cache_stats = self.summarizer.get_cache_stats()
            if cache_stats["hits"] + cache_stats["misses"] > 0:
                print(f"\nCache Stats - Hit Rate: {cache_stats['hit_rate']:.1%} "
                      f"({cache_stats['hits']} hits, {cache_stats['misses']} misses)")
            print("=" * 50 + "\n")

            # Save report
            self.report_generator.save_summary(
                summary_response, logs, self.config, mode="single"
            )

        except (LogSourceError, OllamaError) as e:
            logger.error(f"Error: {e}")
            sys.exit(1)
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            sys.exit(1)

    def run_follow(self, interval: int = 60) -> None:
        """Run in follow mode, continuously monitoring logs."""
        logger.info(f"Starting follow mode, summarizing every {interval} seconds")
        
        # Track processed logs to avoid re-summarizing
        processed_hashes = set()

        try:
            # Ensure model is available
            self.ensure_model_available()

            while not self._shutdown.is_set():
                try:
                    # Read logs
                    logs = self.log_reader.read_logs()

                    if logs:
                        # Create a hash of the logs to detect new entries
                        log_hash = hash(tuple(log.get("MESSAGE", "") for log in logs))
                        
                        if log_hash not in processed_hashes:
                            logger.info(f"Processing {len(logs)} log entries")

                            # Generate summary
                            summary_response = self.summarize_logs(logs)

                            # Save report
                            self.report_generator.save_summary(
                                summary_response, logs, self.config, mode="follow"
                            )
                            
                            # Remember this batch
                            processed_hashes.add(log_hash)
                            
                            # Keep only recent hashes to prevent memory growth
                            if len(processed_hashes) > 100:
                                processed_hashes = set(list(processed_hashes)[-50:])
                        else:
                            logger.debug("No new log entries since last check")
                    else:
                        logger.debug("No log entries found")

                    # Display cache stats periodically
                    cache_stats = self.summarizer.get_cache_stats()
                    if cache_stats["hits"] > 0 and cache_stats["hits"] % 10 == 0:
                        logger.info(f"Cache performance - Hit rate: {cache_stats['hit_rate']:.1%}")

                    # Wait for next interval
                    self._shutdown.wait(interval)

                except Exception as e:
                    logger.error(f"Error in follow loop: {e}")
                    # Continue after error
                    self._shutdown.wait(min(interval, 10))

        except KeyboardInterrupt:
            logger.info("Follow mode stopped by user")
        finally:
            logger.info("Shutting down follow mode")
            # Display final cache stats
            cache_stats = self.summarizer.get_cache_stats()
            logger.info(f"Final cache stats - Size: {cache_stats['size']}, "
                       f"Hit rate: {cache_stats['hit_rate']:.1%}")


def load_config(args: argparse.Namespace) -> Config:
    """Load configuration from file and command line arguments."""
    config_dict: Dict[str, Any] = {}

    # Load from file if specified
    if hasattr(args, "config") and args.config:
        config_path = Path(args.config)
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    config_dict = yaml.safe_load(f) or {}
                logger.info(f"Loaded configuration from: {config_path}")
            except yaml.YAMLError as e:
                logger.error(f"Invalid YAML in config file: {e}")
                sys.exit(1)
    else:
        # Try default locations
        for path in ["/etc/logwhisperer/config.yaml", "config.yaml"]:
            if Path(path).exists():
                try:
                    with open(path, "r") as f:
                        config_dict = yaml.safe_load(f) or {}
                    logger.info(f"Loaded configuration from: {path}")
                    break
                except:
                    pass

    # Remove monitor config as it's not supported in OSS
    config_dict.pop('monitor', None)

    # Override with command line arguments
    cli_overrides = {
        "model": args.model,
        "source": args.source,
        "log_file_path": args.logfile,
        "entries": args.entries,
        "priority": args.priority,
        "docker_container": args.container,
        "ollama_host": args.ollama_host,
        "timeout": args.timeout,
        "cache_ttl": args.cache_ttl if hasattr(args, "cache_ttl") else None,
        "use_cache": args.no_cache is False if hasattr(args, "no_cache") else None,
        "analyze_logs": args.no_analysis is False if hasattr(args, "no_analysis") else None,
    }

    for key, value in cli_overrides.items():
        if value is not None:
            config_dict[key] = value

    # Create config object
    try:
        return Config(**config_dict)
    except (TypeError, ValueError) as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    logging.basicConfig(
        level=log_level, 
        format=log_format, 
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    # Reduce noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="LogWhisperer OSS - AI-powered log summarization with advanced features",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Summarize recent system errors with analysis
  %(prog)s --source journalctl --priority err
  
  # Analyze Docker container logs without caching
  %(prog)s --source docker --container myapp --no-cache
  
  # Monitor a log file continuously with custom interval
  %(prog)s --source file --logfile /var/log/app.log --follow --interval 30
  
  # List available models
  %(prog)s --list-models
  
  # Show cache statistics
  %(prog)s --cache-stats
        """,
    )

    parser.add_argument(
        "--version", action="version", version=f"LogWhisperer OSS v{__version__}"
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )

    parser.add_argument("--config", help="Path to configuration file")

    # Source options
    parser.add_argument(
        "--source",
        choices=["journalctl", "file", "docker"],
        help="Log source (default: journalctl)",
    )

    parser.add_argument("--logfile", help="Path to log file (when source=file)")

    parser.add_argument(
        "--container", help="Docker container name (when source=docker)"
    )

    # Filter options
    parser.add_argument("--entries", type=int, help="Number of log entries to analyze")

    parser.add_argument(
        "--priority",
        choices=[p.value for p in Priority],
        help="Minimum log priority (journalctl only)",
    )

    # Model options
    parser.add_argument("--model", help="Ollama model to use")

    parser.add_argument("--ollama-host", help="Ollama API host")

    parser.add_argument("--timeout", type=int, help="Request timeout in seconds")

    # Cache options
    parser.add_argument(
        "--cache-ttl", type=int, help="Cache TTL in seconds (default: 300)"
    )

    parser.add_argument(
        "--no-cache", action="store_true", help="Disable caching"
    )

    parser.add_argument(
        "--cache-stats", action="store_true", help="Show cache statistics and exit"
    )

    # Analysis options
    parser.add_argument(
        "--no-analysis", action="store_true", help="Skip log analysis"
    )

    # Operation modes
    parser.add_argument(
        "--follow", action="store_true", help="Continuously monitor logs"
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Interval between summaries in follow mode (default: 60s)",
    )

    parser.add_argument(
        "--list-models", action="store_true", help="List available Ollama models"
    )

    args = parser.parse_args()

    # Set up logging
    setup_logging(args.verbose)

    # Handle special commands
    if args.list_models:
        try:
            config = load_config(args)
            with ModelManager(config.ollama_host) as manager:
                models = manager.list_models()
                print("Available Ollama models:")
                for model in models:
                    print(f"  - {model}")
        except Exception as e:
            logger.error(f"Failed to list models: {e}")
            sys.exit(1)
        return

    if hasattr(args, "cache_stats") and args.cache_stats:
        try:
            config = load_config(args)
            summarizer = OllamaSummarizer(
                host=config.ollama_host,
                model=config.model,
                timeout=config.timeout,
                cache_ttl=config.cache_ttl
            )
            stats = summarizer.get_cache_stats()
            print("Cache Statistics:")
            print(f"  - Size: {stats['size']} entries")
            print(f"  - Hits: {stats['hits']}")
            print(f"  - Misses: {stats['misses']}")
            print(f"  - Hit Rate: {stats['hit_rate']:.1%}")
        except Exception as e:
            logger.error(f"Failed to get cache stats: {e}")
            sys.exit(1)
        return

    # Load configuration
    config = load_config(args)

    # Create and run LogWhisperer
    app = LogWhisperer(config)

    if args.follow:
        app.run_follow(args.interval)
    else:
        app.run_once()


if __name__ == "__main__":
    main()