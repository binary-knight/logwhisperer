# LogWhisperer 🔍

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?logo=docker&logoColor=white)](https://hub.docker.com/r/yourusername/logwhisperer)
[![Discord](https://img.shields.io/discord/YOUR_SERVER_ID?logo=discord)](https://discord.gg/YOUR_INVITE)

AI-powered log analysis and monitoring tool that uses local LLMs to provide intelligent insights from your system logs.

![LogWhisperer Demo](docs/assets/demo.gif)

## ✨ Features

- 🤖 **AI-Powered Analysis** - Uses Ollama for local LLM processing
- 📊 **Multiple Log Sources** - Supports journalctl, files, and Docker containers  
- 🚨 **Real-time Alerts** - Discord notifications with @mentions
- 🔍 **Smart Summarization** - Intelligent pattern detection
- 🐳 **Docker Ready** - Full containerization support
- 🛡️ **Production Ready** - Rate limiting, deduplication, caching

## 🚀 Quick Start

### Option 1: Download Binary (Easiest)
```bash
# Download latest release
wget https://github.com/yourusername/logwhisperer/releases/latest/download/logwhisperer_linux_x86_64.zip
unzip logwhisperer_linux_x86_64.zip
sudo ./install.sh

# Test it
logwhisperer test
```

### Option 2: Docker (Recommended)
```bash
# Clone and run with docker-compose
git clone https://github.com/yourusername/logwhisperer.git
cd logwhisperer
docker-compose up -d
```

### Option 3: From Source
```bash
# Clone and install
git clone https://github.com/yourusername/logwhisperer.git
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

### CI/CD
```bash
# Tag a release
git tag v1.0.0
git push origin v1.0.0
# GitHub Actions handles the rest!
```

## 🐳 Docker

```bash
# Quick start
docker-compose up -d

# With external Ollama
docker-compose --profile external-ollama up -d

# Development mode
docker-compose --profile development up
```

## 📚 Documentation

- [Full Documentation](docs/README.md)
- [Configuration Guide](docs/configuration.md)
- [Docker Guide](docs/docker.md)
- [Development Guide](docs/development.md)

## 🤝 Contributing

Contributions are welcome! Please read our [Contributing Guide](CONTRIBUTING.md).

```bash
# Setup dev environment
git clone https://github.com/yourusername/logwhisperer.git
cd logwhisperer
pip install -r requirements-dev.txt
pre-commit install
```

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🙏 Acknowledgments

- [Ollama](https://ollama.ai/) for local LLM support
- [Nuitka](https://nuitka.net/) for Python compilation
- All our [contributors](https://github.com/yourusername/logwhisperer/graphs/contributors)

## 🔗 Links

- [Releases](https://github.com/yourusername/logwhisperer/releases)
- [Docker Hub](https://hub.docker.com/r/yourusername/logwhisperer)
- [Issues](https://github.com/yourusername/logwhisperer/issues)
- [Discussions](https://github.com/yourusername/logwhisperer/discussions)

---

Made with ❤️ by the LogWhisperer Team