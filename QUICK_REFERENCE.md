# LogWhisperer Quick Reference

## 🚀 Development Commands

### Setup
```bash
git clone https://github.com/binary-knight/logwhisperer.git
cd logwhisperer
python -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
```

### Testing
```bash
pytest                      # Run all tests
pytest -v                   # Verbose
pytest --cov               # With coverage
pytest -k test_monitor     # Specific test
ptw                        # Watch mode
```

### Code Quality
```bash
black .                    # Format code
isort .                    # Sort imports
flake8 .                   # Lint
mypy .                     # Type check
bandit -r .                # Security scan
```

### Building
```bash
./build.sh                 # Production build
./build.sh --debug         # Debug build
./build.sh --skip-tests    # Skip tests
./build.sh --no-compress   # No UPX
```

## 📦 Docker Commands

### Basic
```bash
docker-compose up -d       # Start
docker-compose logs -f     # View logs
docker-compose down        # Stop
docker-compose restart     # Restart
```

### Profiles
```bash
docker-compose --profile external-ollama up -d    # External Ollama
docker-compose --profile development up           # Dev mode
```

### Build
```bash
docker build -t logwhisperer:latest .             # Build image
docker run --rm logwhisperer:latest --version    # Test image
```

## 🔧 Common Operations

### Release Process
```bash
# 1. Update version in logwhisperer.py
# 2. Commit and tag
git add .
git commit -m "chore: bump version to 1.1.0"
git tag v1.1.0
git push origin main v1.1.0
```

### Debug Mode
```bash
# Run with debug logging
LOG_LEVEL=DEBUG python logwhisperer.py monitor

# Check logs
tail -f /var/log/logwhisperer/logwhisperer.log
```

### Test Webhook
```bash
# Test Discord webhook
curl -H "Content-Type: application/json" \
  -d '{"content":"Test from LogWhisperer"}' \
  https://discord.com/api/webhooks/YOUR_WEBHOOK
```

## 📋 Configuration Examples

### Minimal Config
```yaml
model: mistral
monitor:
  webhook_url: https://discord.com/api/webhooks/...
```

### Full Config
```yaml
model: llama2
source: journalctl
priority: err
timeout: 120
ollama_host: http://localhost:11434

monitor:
  enabled: true
  webhook_url: https://discord.com/api/webhooks/...
  escalation_level: ERROR
  batch_size: 100
  
  discord_mentions:
    ERROR: ["123456789"]
    CRITICAL: ["123456789", "&987654321"]
```

## 🎯 Common Use Cases

### Monitor System Errors
```bash
logwhisperer monitor --source journalctl --priority err
```

### Analyze Application Logs
```bash
logwhisperer summarize --source file --logfile /var/log/app.log
```

### Docker Container Monitoring
```bash
logwhisperer monitor --source docker --container nginx
```

### Custom Analysis
```yaml
# In config.yaml
prompt: |
  Analyze for security issues:
  {{LOGS}}
```

## 🐛 Troubleshooting

### Ollama Issues
```bash
# Check status
curl http://localhost:11434/api/tags

# Pull model
ollama pull mistral

# List models
ollama list
```

### Permission Issues
```bash
sudo chown -R $USER:$USER /opt/logwhisperer
chmod +x /opt/logwhisperer/logwhisperer
```

### Test Installation
```bash
logwhisperer test
```

## 📁 Project Structure
```
logwhisperer/
├── .github/workflows/      # CI/CD
├── docker/                 # Docker files
│   └── entrypoint.sh
├── modules/                # Core modules
│   ├── monitor.py
│   ├── discord_alert.py
│   ├── summarizer.py
│   └── spinner.py
├── tests/                  # Test files
├── logwhisperer.py         # Main entry
├── logwhisperer_oss.py     # OSS features
├── config.yaml             # Default config
├── requirements*.txt       # Dependencies
├── Dockerfile             # Production image
├── docker-compose.yml     # Compose config
└── build.sh              # Build script
```

## 🔗 Useful Links

- [Full Docs](docs/README.md)
- [GitHub Issues](https://github.com/binary-knight/logwhisperer/issues)
- [Discord Community](https://discord.gg/your-invite)
- [Ollama Docs](https://github.com/jmorganca/ollama)