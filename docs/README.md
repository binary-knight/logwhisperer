# LogWhisperer Documentation

## Table of Contents
1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Installation](#installation)
4. [Building from Source](#building-from-source)
5. [Configuration](#configuration)
6. [Usage Guide](#usage-guide)
7. [Docker Deployment](#docker-deployment)
8. [Development](#development)
9. [CI/CD](#cicd)
10. [Troubleshooting](#troubleshooting)

## Overview

LogWhisperer is an AI-powered log analysis and monitoring tool that uses local LLMs (via Ollama) to provide intelligent insights from your system logs. It supports real-time monitoring with Discord alerts, one-time summarization, and multiple log sources.

### Key Features
- 🤖 **AI-Powered Analysis**: Uses Ollama for local LLM processing
- 📊 **Multiple Log Sources**: Supports journalctl, files, and Docker containers
- 🚨 **Real-time Alerts**: Discord webhook integration with configurable mentions
- 🔍 **Smart Summarization**: Intelligent log pattern detection and analysis
- 🐳 **Docker Ready**: Full containerization support
- 🛡️ **Production Ready**: Rate limiting, deduplication, and error handling

## Quick Start

```bash
# Clone and install from source
git clone https://github.com/binary-knight/logwhisperer.git
cd logwhisperer
pip install -r requirements.txt

# Run a test
python logwhisperer.py test

# Summarize recent errors
python logwhisperer.py summarize --source journalctl --priority err
```

## Installation

### System Requirements
- **OS**: Linux (Ubuntu/Debian/RHEL/Arch), macOS
- **Python**: 3.8+ (for development)
- **RAM**: 4GB minimum (8GB recommended)
- **Disk**: 2GB for models + logs

### Method 1: Install Script (Recommended)
```bash
# Clone the repository
git clone https://github.com/binary-knight/logwhisperer.git
cd logwhisperer

# Run installer
sudo ./install.sh

# With options
sudo ./install.sh --model llama2 --with-service
```

### Method 2: Manual Installation
```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull default model
ollama pull mistral

# Copy files
sudo mkdir -p /opt/logwhisperer /etc/logwhisperer
sudo cp logwhisperer /opt/logwhisperer/
sudo cp config.yaml /etc/logwhisperer/
sudo chmod +x /opt/logwhisperer/logwhisperer

# Create symlink
sudo ln -s /opt/logwhisperer/logwhisperer /usr/local/bin/logwhisperer
```

### Method 3: From Source
```bash
# Clone repository
git clone https://github.com/binary-knight/logwhisperer.git
cd logwhisperer

# Install dependencies
pip install -r requirements.txt

# Run from source
python logwhisperer.py --help
```

## Building from Source

### Prerequisites
- Python 3.8+
- Git
- GCC/G++ compiler
- UPX (optional, for compression)

### Development Build
```bash
# Clone the repository
git clone https://github.com/binary-knight/logwhisperer.git
cd logwhisperer

# Install development dependencies
pip install -r requirements-dev.txt

# Run tests
pytest

# Run from source
python logwhisperer.py summarize --source journalctl
```

### Production Build (Local)
```bash
# Run the build script
./build.sh

# With options
./build.sh --debug              # Debug build
./build.sh --skip-tests         # Skip tests
./build.sh --no-compress        # No UPX compression
./build.sh --sign --gpg-key KEY # Sign release

# Output will be in dist/
ls -la dist/
# logwhisperer_v1.0.0_linux_x86_64.zip
# logwhisperer_v1.0.0_linux_x86_64.tar.gz
# logwhisperer_v1.0.0_linux_x86_64.zip.sha256
```

### GitHub Actions Build
```bash
# Tag a release to trigger CI/CD
git tag v1.0.0
git push origin v1.0.0

# CI/CD will:
# 1. Run tests on multiple platforms
# 2. Build binaries for Linux/macOS (Intel/ARM)
# 3. Create Docker images
# 4. Generate release with artifacts
```

## Configuration

### Basic Configuration
Edit `/etc/logwhisperer/config.yaml`:

```yaml
# Model settings
model: mistral              # LLM model to use
ollama_host: http://localhost:11434

# Log source
source: journalctl          # journalctl, file, or docker
log_file_path: /var/log/syslog  # For file source
docker_container: myapp     # For docker source

# Monitoring settings
monitor:
  enabled: true
  webhook_url: https://discord.com/api/webhooks/YOUR_WEBHOOK
  escalation_level: ERROR   # Minimum level for alerts
  
  # Discord mentions (optional)
  discord_mentions:
    ERROR:
      - "123456789012345678"     # User ID
    CRITICAL:
      - "123456789012345678"     # User ID
      - "&456789012345678901"    # Role ID (prefix with &)
```

### Environment Variables
```bash
export LOGWHISPERER_CONFIG=/path/to/config.yaml
export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
export OLLAMA_HOST=http://remote-ollama:11434
```

## Usage Guide

### Basic Commands

#### Test Installation
```bash
# Run diagnostics
logwhisperer test
```

#### One-time Summarization
```bash
# Summarize recent errors from journalctl
logwhisperer summarize --source journalctl --priority err

# Summarize a log file
logwhisperer summarize --source file --logfile /var/log/nginx/error.log

# Summarize Docker container logs
logwhisperer summarize --source docker --container myapp

# With custom settings
logwhisperer summarize \
  --entries 1000 \           # Number of log entries
  --model llama2 \           # Different model
  --timeout 120              # Timeout in seconds
```

#### Real-time Monitoring
```bash
# Start monitoring (PRO feature)
logwhisperer monitor

# Monitor specific source
logwhisperer monitor --source file --file /var/log/app.log

# With webhook override
logwhisperer monitor --webhook https://discord.com/api/webhooks/...
```

#### Follow Mode
```bash
# Continuously summarize logs every 60 seconds
logwhisperer summarize --follow --interval 60
```

### Advanced Usage

#### Custom Prompt Templates
In `config.yaml`:
```yaml
prompt: |
  You are a security analyst. Analyze these logs for security issues:
  
  {{LOGS}}
  
  Focus on:
  1. Authentication failures
  2. Suspicious patterns
  3. Potential breaches
```

#### Multiple Models
```bash
# List available models
logwhisperer summarize --list-models

# Use specific model
logwhisperer summarize --model codellama
```

## Docker Deployment

### Quick Start
```bash
# Create .env file
cat > .env << EOF
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
OLLAMA_MODEL=mistral
LOG_LEVEL=INFO
EOF

# Start with docker-compose
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Docker Compose Profiles

#### Default (Integrated Ollama)
```bash
docker-compose up -d
```

#### External Ollama
```bash
# Run with separate Ollama container
docker-compose --profile external-ollama up -d
```

#### Development Mode
```bash
# Mount source code for development
docker-compose --profile development up
```

### Custom Docker Run
```bash
# Build image
docker build -t logwhisperer:latest .

# Run with host log access
docker run -d \
  --name logwhisperer \
  -v /var/log:/host/logs:ro \
  -v $(pwd)/config.yaml:/etc/logwhisperer/config.yaml \
  -e DISCORD_WEBHOOK_URL="$DISCORD_WEBHOOK_URL" \
  logwhisperer:latest monitor
```

## Development

### Setting Up Development Environment
```bash
# Clone repository
git clone https://github.com/binary-knight/logwhisperer.git
cd logwhisperer

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install development dependencies
pip install -r requirements-dev.txt

# Install pre-commit hooks
pre-commit install
```

### Running Tests
```bash
# Run all tests
pytest

# With coverage
pytest --cov=. --cov-report=html

# Specific test file
pytest tests/test_monitor.py

# Watch mode
ptw  # pytest-watch
```

### Code Quality
```bash
# Format code
black .
isort .

# Lint
flake8 .
mypy .

# Security scan
bandit -r .
```

### Making Changes
1. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature
   ```

2. Make changes and test:
   ```bash
   pytest
   black .
   ```

3. Commit with conventional commits:
   ```bash
   git commit -m "feat: add new feature"
   git commit -m "fix: resolve issue #123"
   ```

4. Push and create PR:
   ```bash
   git push origin feature/your-feature
   ```

## CI/CD

### GitHub Actions Workflow

The CI/CD pipeline automatically:
1. Runs tests on multiple Python versions
2. Builds binaries for Linux and macOS
3. Creates Docker images
4. Publishes releases

### Triggering Builds

#### Automatic Triggers
- **Push to main**: Runs tests
- **Pull Request**: Runs tests and builds
- **Tag push (v*)**: Full release pipeline

#### Manual Trigger
```bash
# Via GitHub UI: Actions -> Build & Release -> Run workflow

# Via GitHub CLI
gh workflow run build-release.yml -f build_type=debug
```

### Release Process
1. Update version:
   ```bash
   # In logwhisperer.py
   __version__ = "1.1.0"
   ```

2. Commit and tag:
   ```bash
   git add .
   git commit -m "chore: bump version to 1.1.0"
   git tag v1.1.0
   git push origin main v1.1.0
   ```

3. CI/CD will create:
   - GitHub Release with binaries
   - Docker images on Docker Hub and GHCR
   - Updated documentation

## Troubleshooting

### Common Issues

#### Ollama Connection Failed
```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags

# Start Ollama
ollama serve

# Check logs
journalctl -u ollama -f
```

#### Model Not Found
```bash
# List models
ollama list

# Pull model
ollama pull mistral
```

#### Permission Denied
```bash
# Fix permissions
sudo chown -R $USER:$USER /opt/logwhisperer
sudo chmod +x /opt/logwhisperer/logwhisperer
```

#### Discord Webhook Not Working
```bash
# Test webhook
curl -H "Content-Type: application/json" \
  -d '{"content":"Test message"}' \
  YOUR_WEBHOOK_URL
```

### Debug Mode
```bash
# Run with verbose logging
LOG_LEVEL=DEBUG logwhisperer monitor

# Check logs
tail -f /var/log/logwhisperer/logwhisperer.log
```

### Getting Help
1. Check logs: `/var/log/logwhisperer/`
2. Run diagnostics: `logwhisperer test`
3. Enable debug logging
4. Check [GitHub Issues](https://github.com/binary-knight/logwhisperer/issues)
5. Join [Discord Community](https://discord.gg/your-invite)

## Performance Tuning

### Ollama Settings
```yaml
# Reduce model size for faster processing
model: phi  # Smaller model

# Adjust timeout for slow systems
timeout: 300
```

### Batch Processing
```yaml
monitor:
  batch_size: 100      # Smaller batches
  batch_timeout: 60    # Longer timeout
```

### Rate Limiting
```yaml
monitor:
  rate_limit_window: 300     # 5 minutes
  rate_limit_max_alerts: 5   # Max 5 alerts per window
```

## Security Considerations

1. **Webhook Security**: Keep webhook URLs private
2. **Log Access**: Use read-only mounts
3. **Container Security**: Run as non-root user
4. **Model Security**: Use trusted models only
5. **Network Security**: Use HTTPS for Ollama if remote

## License

LogWhisperer is released under the MIT License. See LICENSE file for details.

## Contributing

Contributions are welcome. Open an issue first for anything large.

## Support

- 📧 Email: support@logwhisperer.example.com
- 💬 Discord: [Join our community](https://discord.gg/your-invite)
- 🐛 Issues: [GitHub Issues](https://github.com/binary-knight/logwhisperer/issues)
- 📖 Wiki: [GitHub Wiki](https://github.com/binary-knight/logwhisperer/wiki)