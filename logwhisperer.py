#!/usr/bin/env python3
"""
LogWhisperer - AI-powered log intelligence
Main CLI entry point with enhanced error handling and production features.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__))) # Import visibility when running as __main__
import time
import yaml
import argparse
import logging
import signal
import threading
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

# Version should be managed in one place
__version__ = "1.0.0"

# Development bypass - set this environment variable for local development
DEVELOPMENT_MODE = os.environ.get("LOGWHISPERER_DEV_MODE") == "true"
DEVELOPMENT_KEY = os.environ.get("LOGWHISPERER_DEV_KEY") == "germangreenhousefour"

# Configure logging
def setup_logging(verbose: bool = False) -> None:
    """Configure logging with appropriate format and level."""
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Create logs directory if it doesn't exist
    log_dir = Path("/var/log/logwhisperer")
    if log_dir.exists() and os.access(log_dir, os.W_OK):
        log_file = log_dir / f"logwhisperer_{datetime.now().strftime('%Y%m%d')}.log"
        logging.basicConfig(
            level=log_level,
            format=log_format,
            handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
        )
    else:
        # Fallback to stdout only if can't write to log directory
        logging.basicConfig(
            level=log_level,
            format=log_format,
            handlers=[logging.StreamHandler(sys.stdout)],
        )

    # Set third-party loggers to WARNING to reduce noise
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Custom exception for configuration errors."""
    pass


class LogWhispererError(Exception):
    """Base exception for LogWhisperer errors."""
    pass


def check_license() -> bool:
    """
    Check if license is valid or if running in development mode.
    
    Returns:
        True if licensed or in dev mode, False otherwise
    """
    # Development bypass
    if DEVELOPMENT_MODE and DEVELOPMENT_KEY:
        logger.info("Running in development mode")
        return True
    
    # Check for license module
    try:
        # Try multiple import methods for Nuitka compatibility
        try:
            # First try the standard import
            from modules.license import validate_license, check_license_cli
        except ImportError:
            # Try importing from the current package (for compiled binary)
            import importlib
            import importlib.util
            
            # When compiled with Nuitka, modules might be at the top level
            try:
                license_module = importlib.import_module('license')
            except ImportError:
                # Try with explicit path
                spec = importlib.util.find_spec('modules.license')
                if spec and spec.loader:
                    license_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(license_module)
                else:
                    raise ImportError("Cannot find license module")
            
            validate_license = license_module.validate_license
            check_license_cli = license_module.check_license_cli
        
        # Don't check license for certain commands
        if len(sys.argv) > 1 and sys.argv[1] in ['--version', '--help', 'check-license', 'activate']:
            return True
        
        # Validate license
        is_valid, message = validate_license()
        
        if is_valid:
            logger.info(f"License validated: {message}")
            return True
        else:
            print(f"\n❌ License Error: {message}")
            print("\n📋 To activate LogWhisperer Pro:")
            print("1. Purchase a license from: https://gumroad.com/l/logwhisperer")
            print("2. Run: logwhisperer activate")
            print("   OR")
            print("   Set environment variable: export LOGWHISPERER_LICENSE_KEY='your-license-key'")
            print("\n💡 For help: logwhisperer check-license")
            
            # Special message for developers
            if os.environ.get("USER") in ["your-username", "dev", "developer"]:
                print("\n🔧 Developer Mode:")
                print("   export LOGWHISPERER_DEV_MODE=true")
                print("   export LOGWHISPERER_DEV_KEY=your-secret-dev-key-here")
            
            return False
            
    except ImportError as e:
        # IMPORTANT: Return False for Pro features when license module is missing
        logger.warning(f"License module not found: {e}")
        
        # Check what command is being run
        if len(sys.argv) > 1:
            command = sys.argv[1]
            # Only allow OSS features without license module
            if command in ['summarize', 'test', '--version', '--help']:
                logger.info("Running in OSS mode (limited features)")
                return True
            else:
                # Block Pro features
                print("\n❌ License module not found. This feature requires LogWhisperer Pro.")
                print("\n📋 To use Pro features:")
                print("1. Purchase LogWhisperer Pro from: https://gumroad.com/l/logwhisperer")
                print("2. Ensure you have the Pro version installed")
                print("\n💡 OSS features available:")
                print("  logwhisperer summarize  - One-time log summarization")
                print("  logwhisperer test       - Run diagnostics")
                return False
        
        # Default to blocking access if we can't determine the command
        return False


def load_full_config(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load configuration from various locations with validation.

    Args:
        path: Optional path to config file

    Returns:
        Dictionary containing configuration

    Raises:
        ConfigError: If configuration is invalid
    """
    # Priority order for config locations
    config_paths = [
        path,
        os.environ.get("LOGWHISPERER_CONFIG"),
        "/etc/logwhisperer/config.yaml",
        "/opt/logwhisperer/config.yaml",
        Path.home() / ".logwhisperer" / "config.yaml",
        Path.cwd() / "config.yaml",
    ]

    for config_path in filter(None, config_paths):
        config_path = Path(str(config_path))
        if config_path.exists() and config_path.is_file():
            try:
                with open(config_path, "r") as f:
                    config = yaml.safe_load(f)
                    if config is None:
                        config = {}
                    logger.info(f"Loaded configuration from: {config_path}")
                    validate_config(config)
                    return config
            except yaml.YAMLError as e:
                raise ConfigError(f"Invalid YAML in {config_path}: {e}")
            except Exception as e:
                logger.warning(f"Failed to load config from {config_path}: {e}")
                continue

    logger.warning("No configuration file found, using defaults")
    return get_default_config()


def get_default_config() -> Dict[str, Any]:
    """Return default configuration."""
    return {
        "model": "mistral",
        "source": "journalctl",
        "priority": "err",
        "entries": 500,
        "timeout": 120,
        "ollama_host": "http://localhost:11434",
        "lines_per_prompt": 50,
        "monitor": {
            "enabled": True,
            "escalation_level": "ERROR",
            "batch_size": 50,
            "sleep_interval": 1.0,
            "send_full_summary": False,
            "alert_format": "discord",
        },
    }


def validate_config(config: Dict[str, Any]) -> None:
    """
    Validate configuration values.

    Args:
        config: Configuration dictionary

    Raises:
        ConfigError: If configuration is invalid
    """
    # Validate source
    valid_sources = ["journalctl", "file", "docker"]
    if "source" in config and config["source"] not in valid_sources:
        raise ConfigError(
            f"Invalid source: {config['source']}. Must be one of {valid_sources}"
        )

    # Validate numeric values
    numeric_fields = [
        ("entries", 1, 100000),
        ("timeout", 1, 3600),
        ("lines_per_prompt", 1, 1000),
    ]

    for field, min_val, max_val in numeric_fields:
        if field in config:
            value = config[field]
            if (
                not isinstance(value, (int, float))
                or value < min_val
                or value > max_val
            ):
                raise ConfigError(
                    f"{field} must be a number between {min_val} and {max_val}"
                )

    # Validate monitor config
    if "monitor" in config:
        monitor = config["monitor"]
        valid_levels = ["INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "FATAL"]
        if (
            "escalation_level" in monitor
            and monitor["escalation_level"] not in valid_levels
        ):
            raise ConfigError(
                f"Invalid escalation_level. Must be one of {valid_levels}"
            )


def import_with_fallback(module_name: str, package: str = "modules"):
    """
    Import a module with fallback error handling.

    Args:
        module_name: Name of the module to import
        package: Package name

    Returns:
        Imported module or None
    """
    try:
        # Try standard import first
        if package:
            return __import__(f"{package}.{module_name}", fromlist=[module_name])
        return __import__(module_name)
    except ImportError:
        # Try alternative import methods for Nuitka
        try:
            import importlib
            # Try without package prefix (for compiled binaries)
            return importlib.import_module(module_name)
        except ImportError:
            # Try to find the module in sys.modules
            import sys
            for key in sys.modules:
                if key.endswith(f'.{module_name}') or key == module_name:
                    return sys.modules[key]
            
            logger.error(f"Failed to import {module_name} from {package}")
            return None


def run_monitor(args: argparse.Namespace) -> None:
    """
    Run the monitoring mode (PRO feature).

    Args:
        args: Command line arguments
    """
    # Check license for PRO features
    if not check_license():
        sys.exit(1)
    
    # Import monitor module
    monitor_module = import_with_fallback("monitor")
    if not monitor_module:
        logger.error("Monitor module not found. Is this the PRO version?")
        sys.exit(1)

    try:
        config = load_full_config(args.config)
        monitor_cfg = config.get("monitor", {})

        # Validate monitor is enabled
        if not monitor_cfg.get("enabled", True):
            logger.error("Monitoring is disabled in configuration")
            sys.exit(1)

        # Get configuration values with precedence: CLI args > config > defaults
        source = args.source or config.get("source", "journalctl")
        file_path = args.file or config.get("log_file_path")
        container = args.container or config.get("docker_container")
        webhook_url = args.webhook or monitor_cfg.get("webhook_url")

        # Validate required arguments
        if source == "file" and not file_path:
            logger.error("The --file argument is required when --source is 'file'")
            sys.exit(1)

        if source == "docker" and not container:
            logger.error(
                "The --container argument is required when --source is 'docker'"
            )
            sys.exit(1)

        if not webhook_url and monitor_cfg.get("alert_format") == "discord":
            logger.warning("No webhook URL configured. Alerts will not be sent.")

        logger.info(f"Starting LogWhisperer in '{source}' monitoring mode...")

        # Create monitor instance
        monitor = monitor_module.LogMonitor(
            source=source,
            file_path=file_path,
            container=container,
            webhook_url=webhook_url,
        )

        # Set up graceful shutdown
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            monitor.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        # Start monitoring
        monitor.start()

    except ConfigError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user")
        if "monitor" in locals():
            monitor.stop()
    except Exception as e:
        logger.exception(f"Unexpected error in monitor mode: {e}")
        sys.exit(1)


def run_summarize(args: argparse.Namespace) -> None:
    """
    Run the summarization mode (OSS feature, but enhanced in PRO).

    Args:
        args: Command line arguments
    """
    # Summarize is available in OSS, but check license for enhanced features
    has_license = check_license()
    
    # Import OSS module
    oss_module = import_with_fallback("logwhisperer_oss", package="")
    if not oss_module:
        logger.error("OSS module not found")
        sys.exit(1)

    try:
        # Build argument list for OSS module
        oss_args = []

        # Map arguments to OSS format
        arg_mapping = {
            "source": "--source",
            "logfile": "--logfile",
            "entries": "--entries",
            "priority": "--priority",
            "model": "--model",
            "container": "--container",
            "ollama_host": "--ollama-host",
            "timeout": "--timeout",
            "follow": "--follow",
            "interval": "--interval",
            "list_models": "--list-models",
            "config": "--config",
        }

        for arg_name, arg_flag in arg_mapping.items():
            value = getattr(args, arg_name, None)
            if value is not None:
                if isinstance(value, bool):
                    if value:
                        oss_args.append(arg_flag)
                else:
                    oss_args.extend([arg_flag, str(value)])

        # Save original argv and replace with new args
        original_argv = sys.argv
        sys.argv = [sys.argv[0]] + oss_args

        if not has_license:
            print("\n💡 Running in OSS mode with limited features.")
            print("   Upgrade to Pro for: real-time monitoring, Discord alerts, and more!")
            print("   Visit: https://gumroad.com/l/logwhisperer\n")

        logger.info("Running summarization mode")
        oss_module.main()

    except Exception as e:
        logger.exception(f"Error in summarize mode: {e}")
        sys.exit(1)
    finally:
        # Restore original argv
        if "original_argv" in locals():
            sys.argv = original_argv


def run_activate(args: argparse.Namespace) -> None:
    """
    Run license activation.
    
    Args:
        args: Command line arguments
    """
    try:
        from modules.license import validate_license, get_machine_id
        
        print("╔══════════════════════════════════════════╗")
        print("║     LogWhisperer License Activation      ║")
        print("╚══════════════════════════════════════════╝")
        print()
        
        # Check if already activated
        is_valid, message = validate_license()
        if is_valid:
            print(f"✅ License already activated: {message}")
            print(f"🖥️  Machine ID: {get_machine_id()}")
            return
        
        # Get license key
        license_key = args.license_key
        if not license_key:
            license_key = input("Enter your Gumroad license key: ").strip()
        
        if not license_key:
            print("❌ No license key provided")
            sys.exit(1)
        
        # Set environment variable temporarily
        os.environ['LOGWHISPERER_LICENSE_KEY'] = license_key
        
        # Validate
        print("\nValidating license...")
        is_valid, message = validate_license(online_check=True)
        
        if is_valid:
            print(f"✅ {message}")
            print(f"🖥️  Machine ID: {get_machine_id()}")
            
            # Offer to save
            if sys.stdout.isatty():  # Only in interactive mode
                save = input("\nSave license key to shell profile? (y/N) ").lower().strip()
                if save == 'y':
                    shell_rc = os.path.expanduser("~/.bashrc")
                    if os.path.exists(os.path.expanduser("~/.zshrc")):
                        shell_rc = os.path.expanduser("~/.zshrc")
                    
                    with open(shell_rc, 'a') as f:
                        f.write(f"\n# LogWhisperer License\n")
                        f.write(f"export LOGWHISPERER_LICENSE_KEY='{license_key}'\n")
                    
                    print(f"✅ License saved to {shell_rc}")
                    print(f"   Run: source {shell_rc}")
            
            print("\n🎉 LogWhisperer Pro is now activated!")
            print("\nYou can now use:")
            print("  logwhisperer monitor    - Real-time monitoring with alerts")
            print("  logwhisperer summarize  - Enhanced log summarization")
            
        else:
            print(f"❌ {message}")
            sys.exit(1)
            
    except ImportError:
        print("❌ License module not found. This appears to be the OSS version.")
        print("   Purchase LogWhisperer Pro at: https://gumroad.com/l/logwhisperer")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Activation error: {e}")
        sys.exit(1)


def run_check_license(args: argparse.Namespace) -> None:
    """Check license status."""
    try:
        from modules.license import check_license_cli
        sys.exit(check_license_cli())
    except ImportError:
        print("❌ License module not found. This appears to be the OSS version.")
        print("   Purchase LogWhisperer Pro at: https://gumroad.com/l/logwhisperer")
        sys.exit(1)


def run_test(args: argparse.Namespace) -> None:
    """
    Run diagnostic tests.

    Args:
        args: Command line arguments
    """
    logger.info("Running LogWhisperer diagnostics...")

    # Test 1: Check license status
    print("\n1️⃣  License Status:")
    if DEVELOPMENT_MODE and DEVELOPMENT_KEY:
        print("   ✅ Running in development mode")
    else:
        try:
            from modules.license import validate_license
            is_valid, message = validate_license(online_check=False)
            if is_valid:
                print(f"   ✅ {message}")
            else:
                print(f"   ❌ {message}")
        except ImportError:
            print("   ℹ️  Running OSS version (no license required)")

    # Test 2: Check Ollama connectivity
    try:
        import requests

        config = load_full_config(args.config)
        ollama_host = config.get("ollama_host", "http://localhost:11434")

        print(f"\n2️⃣  Testing Ollama connection at {ollama_host}...")
        response = requests.get(f"{ollama_host}/api/tags", timeout=5)
        if response.ok:
            models = response.json().get("models", [])
            print(f"   ✅ Ollama is accessible. Found {len(models)} models")
            for model in models[:5]:  # Show first 5 models
                print(f"      - {model.get('name')}")
        else:
            print(f"   ❌ Ollama returned status {response.status_code}")
    except Exception as e:
        print(f"   ❌ Cannot connect to Ollama: {e}")

    # Test 3: Check log source accessibility
    source = args.source or "journalctl"
    print(f"\n3️⃣  Testing log source: {source}")

    if source == "journalctl":
        import subprocess

        try:
            result = subprocess.run(
                ["journalctl", "-n", "1"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                print("   ✅ Journalctl is accessible")
            else:
                print(f"   ❌ Journalctl error: {result.stderr}")
        except Exception as e:
            print(f"   ❌ Cannot access journalctl: {e}")

    elif source == "file":
        file_path = args.file or "/var/log/syslog"
        file_path = Path(file_path)
        if file_path.exists() and file_path.is_file():
            if os.access(file_path, os.R_OK):
                print(f"   ✅ Log file {file_path} is readable")
            else:
                print(f"   ❌ Log file {file_path} exists but is not readable")
        else:
            print(f"   ❌ Log file {file_path} does not exist")

    elif source == "docker":
        import subprocess

        try:
            result = subprocess.run(
                ["docker", "version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                print("   ✅ Docker is accessible")
            else:
                print(f"   ❌ Docker error: {result.stderr}")
        except Exception as e:
            print(f"   ❌ Cannot access Docker: {e}")

    # Test 4: Check configuration
    print("\n4️⃣  Configuration:")
    try:
        config = load_full_config(args.config)
        print("   ✅ Configuration loaded successfully")
    except Exception as e:
        print(f"   ❌ Configuration error: {e}")

    # Test 5: Check Discord webhook (if configured)
    if args.webhook_only or not args.skip_webhook:
        monitor_config = config.get("monitor", {})
        webhook_url = monitor_config.get("webhook_url")
        
        if webhook_url:
            print("\n5️⃣  Testing Discord webhook...")
            if test_webhook_url(webhook_url):
                print("   ✅ Webhook is working")
            else:
                print("   ❌ Webhook test failed")
        elif not args.webhook_only:
            print("\n5️⃣  Discord webhook:")
            print("   ℹ️  No webhook URL configured")

    print("\n✅ Diagnostics complete")


def test_webhook(args: argparse.Namespace) -> None:
    """Test Discord webhook configuration."""
    config = load_full_config(args.config)
    monitor_config = config.get("monitor", {})
    webhook_url = args.webhook or monitor_config.get("webhook_url")
    
    if not webhook_url:
        logger.error("No webhook URL configured in config.yaml")
        logger.info("Please add webhook_url to the monitor section of your config")
        logger.info("Or provide a webhook URL with --webhook")
        sys.exit(1)
    
    logger.info("Testing Discord webhook...")
    
    if test_webhook_url(webhook_url):
        logger.info("✅ Webhook test successful! Check your Discord channel.")
        sys.exit(0)
    else:
        logger.error("❌ Webhook test failed. Please check your webhook URL.")
        sys.exit(1)


def test_webhook_url(webhook_url: str) -> bool:
    """
    Test a Discord webhook URL.
    
    Args:
        webhook_url: The webhook URL to test
        
    Returns:
        True if successful, False otherwise
    """
    # Try to import discord_alert module
    discord_module = import_with_fallback("discord_alert")
    
    if discord_module and hasattr(discord_module, 'test_webhook'):
        # Use the test_webhook function from discord_alert
        return discord_module.test_webhook(webhook_url)
    else:
        # Fallback to basic test
        try:
            import requests
            
            response = requests.post(
                webhook_url,
                json={
                    "content": "🧪 **LogWhisperer Test Message**\n\nYour webhook is configured correctly! 🎉",
                    "embeds": [{
                        "title": "LogWhisperer Installation Test",
                        "description": "If you see this message, your Discord integration is working!",
                        "color": 0x00ff00,
                        "fields": [
                            {"name": "Status", "value": "✅ Connected", "inline": True},
                            {"name": "Version", "value": __version__, "inline": True},
                            {"name": "Timestamp", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "inline": True}
                        ],
                        "footer": {
                            "text": "LogWhisperer Alert System"
                        }
                    }]
                },
                timeout=10
            )
            
            if response.status_code == 204:
                return True
            else:
                logger.error(f"Discord API returned: {response.status_code} - {response.text}")
                return False
                
        except requests.exceptions.Timeout:
            logger.error("Request timed out - webhook may be invalid or network is slow")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="LogWhisperer - AI-powered log intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Activate LogWhisperer Pro
  logwhisperer activate
  
  # Monitor system logs in real-time (PRO)
  logwhisperer monitor --source journalctl --webhook https://discord.com/api/webhooks/...
  
  # Summarize recent Docker logs
  logwhisperer summarize --source docker --container myapp
  
  # Monitor a specific log file (PRO)
  logwhisperer monitor --source file --file /var/log/nginx/error.log
  
  # Test Discord webhook
  logwhisperer test-webhook
  
  # Run diagnostics
  logwhisperer test
        """,
    )

    parser.add_argument(
        "--version", action="version", version=f"LogWhisperer v{__version__}"
    )

    parser.add_argument("--config", help="Path to configuration file", type=str)

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )

    subparsers = parser.add_subparsers(
        title="Commands", dest="command", help="Available commands"
    )

    # Activate command
    activate_parser = subparsers.add_parser(
        "activate", help="Activate LogWhisperer Pro license"
    )
    activate_parser.add_argument(
        "--license-key", help="License key (or enter interactively)"
    )
    activate_parser.set_defaults(func=run_activate)

    # Check license command
    check_parser = subparsers.add_parser(
        "check-license", help="Check license status"
    )
    check_parser.set_defaults(func=run_check_license)

    # Monitor command (PRO)
    monitor_parser = subparsers.add_parser(
        "monitor", help="Real-time monitoring with alerts (PRO feature)"
    )
    monitor_parser.add_argument(
        "--source",
        choices=["journalctl", "file", "docker"],
        help="Log source to monitor",
    )
    monitor_parser.add_argument("--file", help="Path to log file (when source=file)")
    monitor_parser.add_argument(
        "--container", help="Docker container name (when source=docker)"
    )
    monitor_parser.add_argument("--webhook", help="Discord webhook URL for alerts")
    monitor_parser.set_defaults(func=run_monitor)

    # Summarize command (OSS)
    summarize_parser = subparsers.add_parser(
        "summarize", help="One-time log summarization"
    )
    summarize_parser.add_argument(
        "--source", choices=["journalctl", "file", "docker"], help="Log source"
    )
    summarize_parser.add_argument("--logfile", help="Path to log file")
    summarize_parser.add_argument(
        "--entries", type=int, help="Number of log entries to process"
    )
    summarize_parser.add_argument(
        "--priority", help="Log priority filter (journalctl only)"
    )
    summarize_parser.add_argument("--model", help="LLM model to use")
    summarize_parser.add_argument("--container", help="Docker container name")
    summarize_parser.add_argument("--ollama-host", help="Ollama API host")
    summarize_parser.add_argument(
        "--timeout", type=int, help="Request timeout in seconds"
    )
    summarize_parser.add_argument(
        "--follow", action="store_true", help="Continuously monitor logs"
    )
    summarize_parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Interval between summaries in follow mode",
    )
    summarize_parser.add_argument(
        "--list-models", action="store_true", help="List available Ollama models"
    )
    summarize_parser.set_defaults(func=run_summarize)

    # Test command
    test_parser = subparsers.add_parser("test", help="Run diagnostic tests")
    test_parser.add_argument(
        "--source",
        choices=["journalctl", "file", "docker"],
        default="journalctl",
        help="Log source to test",
    )
    test_parser.add_argument("--file", help="Log file to test (when source=file)")
    test_parser.add_argument(
        "--webhook-only",
        action="store_true",
        help="Only test webhook connectivity"
    )
    test_parser.add_argument(
        "--skip-webhook",
        action="store_true",
        help="Skip webhook test"
    )
    test_parser.set_defaults(func=run_test)

    # Test webhook command
    webhook_parser = subparsers.add_parser(
        "test-webhook",
        help="Test Discord webhook configuration"
    )
    webhook_parser.add_argument(
        "--webhook",
        help="Discord webhook URL to test (overrides config)"
    )
    webhook_parser.set_defaults(func=test_webhook)

    # Parse arguments
    args = parser.parse_args()

    # Set up logging
    setup_logging(args.verbose)

    # Execute command
    if hasattr(args, "func"):
        try:
            args.func(args)
        except Exception as e:
            logger.exception(f"Fatal error: {e}")
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()