# LogWhisperer 🔍

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

AI-powered log analysis and monitoring tool that uses local LLMs to provide intelligent insights from your system logs.

## ✨ Features

- 🤖 **AI-Powered Analysis** - Uses Ollama for local LLM processing
- 📊 **Multiple Log Sources** - Supports journalctl, files, and Docker containers  
- 🚨 **Real-time Alerts** - Discord notifications with @mentions
- 🔍 **Smart Summarization** - Intelligent pattern detection
- 🛡️ **Production Ready** - Rate limiting, deduplication, caching

## 🚀 Quick Start

```bash
# Clone and install
git clone https://github.com/binary-knight/logwhisperer.git
cd logwhisperer
pip install -r requirements.txt
python logwhisperer.py --help
```

## 📖 Basic Usage

### Summarize Logs
```bash
# Recent system errors
logwhisperer summarize --source journalctl --priority err

# Specific log file
logwhisperer summarize --source file --logfile /var/log/nginx/error.log

# Docker container
logwhisperer summarize --source docker --container myapp
```

### Real-time Monitoring (PRO)
```bash
# Start monitoring with Discord alerts
logwhisperer monitor

# Monitor specific file
logwhisperer monitor --source file --file /var/log/app.log
```

## ⚙️ Configuration

Create `/etc/logwhisperer/config.yaml`:

```yaml
# LLM Settings
model: mistral
ollama_host: http://localhost:11434

# Monitoring
monitor:
  webhook_url: https://discord.com/api/webhooks/YOUR_WEBHOOK
  escalation_level: ERROR
  
  # Discord @mentions (optional)
  discord_mentions:
    ERROR:
      - "123456789012345678"  # User ID
    CRITICAL:
      - "123456789012345678"  # User ID  
      - "&456789012345678901" # Role ID
```

## 🔨 Building from Source

### Development
```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
pytest

# Build binary
./build.sh
```

## 📚 Documentation

- [Full Documentation](docs/README.md)
- [Quick Reference](QUICK_REFERENCE.md)

## 🤝 Contributing

Contributions are welcome. Open an issue first for anything large.

```bash
# Setup dev environment
git clone https://github.com/binary-knight/logwhisperer.git
cd logwhisperer
pip install -r requirements-dev.txt
pre-commit install
```

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🙏 Acknowledgments

- [Ollama](https://ollama.ai/) for local LLM support
- [Nuitka](https://nuitka.net/) for Python compilation
- All our [contributors](https://github.com/binary-knight/logwhisperer/graphs/contributors)

## 🔗 Links

- [Releases](https://github.com/binary-knight/logwhisperer/releases)
- [Issues](https://github.com/binary-knight/logwhisperer/issues)

---

Made with ❤️ by the LogWhisperer Team