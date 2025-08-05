#!/bin/bash

# LogWhisperer Web Dashboard Installer
# Installs both API server and Web UI

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
INSTALL_DIR="/opt/logwhisperer"
VENV_DIR="$INSTALL_DIR/web-venv"
CONFIG_FILE="/etc/logwhisperer/config.yaml"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}This script must be run as root${NC}"
   exit 1
fi

echo -e "${BLUE}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║        LogWhisperer Web Dashboard Installer          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if LogWhisperer is installed
if [ ! -f "$INSTALL_DIR/logwhisperer" ]; then
    echo -e "${RED}Error: LogWhisperer not found at $INSTALL_DIR${NC}"
    echo "Please install LogWhisperer Pro first."
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}' | cut -d'.' -f1,2)
REQUIRED_VERSION="3.7"

if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 7) else 1)"; then
    echo -e "${RED}Error: Python 3.7 or higher is required${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Found Python $PYTHON_VERSION${NC}"

# Check if virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${BLUE}Creating Python virtual environment...${NC}"
    python3 -m venv $VENV_DIR
fi

# Activate virtual environment and install dependencies
echo -e "${BLUE}Installing Python dependencies...${NC}"
source $VENV_DIR/bin/activate

pip install --upgrade pip >/dev/null 2>&1
pip install flask flask-socketio flask-compress pyyaml requests >/dev/null 2>&1

echo -e "${GREEN}✓ Dependencies installed${NC}"

# Copy web dashboard files
echo -e "${BLUE}Installing web dashboard files...${NC}"

# Create web directory
mkdir -p $INSTALL_DIR/web

# Copy dashboard.py
if [ -f "web/dashboard.py" ]; then
    cp web/dashboard.py $INSTALL_DIR/web/
elif [ -f "dashboard.py" ]; then
    cp dashboard.py $INSTALL_DIR/web/
else
    echo -e "${RED}Error: dashboard.py not found${NC}"
    exit 1
fi

# Copy API server
if [ -f "api_server.py" ]; then
    cp api_server.py $INSTALL_DIR/
else
    echo -e "${YELLOW}Warning: api_server.py not found, you'll need to copy it manually${NC}"
fi

# Create wrapper scripts
echo -e "${BLUE}Creating launcher scripts...${NC}"

# API server wrapper
cat > $INSTALL_DIR/bin/logwhisperer-api << 'EOF'
#!/bin/bash
# LogWhisperer API Server wrapper

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LOGWHISPERER_CONFIG="/etc/logwhisperer/config.yaml"

# Check if virtual environment exists
if [ -d "$SCRIPT_DIR/web-venv" ]; then
    source "$SCRIPT_DIR/web-venv/bin/activate"
fi

exec python3 "$SCRIPT_DIR/api_server.py" "$@"
EOF

chmod +x $INSTALL_DIR/bin/logwhisperer-api

# Web dashboard wrapper
cat > $INSTALL_DIR/bin/logwhisperer-web << 'EOF'
#!/bin/bash
# LogWhisperer Web Dashboard wrapper

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LOGWHISPERER_CONFIG="/etc/logwhisperer/config.yaml"

# Check if virtual environment exists
if [ -d "$SCRIPT_DIR/web-venv" ]; then
    source "$SCRIPT_DIR/web-venv/bin/activate"
fi

cd "$SCRIPT_DIR/web"
exec python3 dashboard.py "$@"
EOF

chmod +x $INSTALL_DIR/bin/logwhisperer-web

# Create systemd service files
echo -e "${BLUE}Creating systemd services...${NC}"

# API service
cat > /etc/systemd/system/logwhisperer-api.service << EOF
[Unit]
Description=LogWhisperer API Server
After=network.target
Before=logwhisperer-web.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/bin/logwhisperer-api
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=logwhisperer-api

# Environment
Environment="PYTHONUNBUFFERED=1"

# Resource limits
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# Web service
cat > /etc/systemd/system/logwhisperer-web.service << EOF
[Unit]
Description=LogWhisperer Web Dashboard
After=network.target logwhisperer-api.service
Wants=logwhisperer-api.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$INSTALL_DIR/web
ExecStart=$INSTALL_DIR/bin/logwhisperer-web
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=logwhisperer-web

# Environment
Environment="PYTHONUNBUFFERED=1"

# Resource limits
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
systemctl daemon-reload

# Check configuration
echo -e "${BLUE}Checking configuration...${NC}"

if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${YELLOW}Warning: Configuration file not found at $CONFIG_FILE${NC}"
    echo "Please ensure LogWhisperer is properly configured."
fi

# Check if web config exists
if grep -q "^web:" "$CONFIG_FILE" 2>/dev/null; then
    echo -e "${GREEN}✓ Web configuration found${NC}"
else
    echo -e "${YELLOW}Adding web configuration to config.yaml...${NC}"
    cat >> $CONFIG_FILE << 'EOF'

# Web Dashboard Configuration
web:
  enabled: true
  host: "0.0.0.0"
  port: 5123
  users:
    admin: "changeme"  # CHANGE THIS PASSWORD!
EOF
    echo -e "${GREEN}✓ Web configuration added${NC}"
fi

# Enable services
echo -e "${BLUE}Enabling services...${NC}"
systemctl enable logwhisperer-api.service
systemctl enable logwhisperer-web.service

# Start services
echo -e "${BLUE}Starting services...${NC}"
systemctl start logwhisperer-api.service
sleep 2
systemctl start logwhisperer-web.service

# Check status
sleep 2
API_STATUS=$(systemctl is-active logwhisperer-api.service)
WEB_STATUS=$(systemctl is-active logwhisperer-web.service)

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}        LogWhisperer Web Dashboard Installed!           ${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo ""

if [ "$API_STATUS" = "active" ]; then
    echo -e "API Server: ${GREEN}✓ Running${NC} (port 5124)"
else
    echo -e "API Server: ${RED}✗ Not running${NC}"
    echo "  Check logs: journalctl -u logwhisperer-api -f"
fi

if [ "$WEB_STATUS" = "active" ]; then
    echo -e "Web Dashboard: ${GREEN}✓ Running${NC} (port 5123)"
else
    echo -e "Web Dashboard: ${RED}✗ Not running${NC}"
    echo "  Check logs: journalctl -u logwhisperer-web -f"
fi

echo ""
echo -e "${BLUE}Access the dashboard at:${NC}"
echo -e "  ${GREEN}http://$(hostname -I | awk '{print $1}'):5123${NC}"
echo ""
echo -e "${YELLOW}⚠️  IMPORTANT:${NC}"
echo -e "  1. Change the default admin password in $CONFIG_FILE"
echo -e "  2. Configure firewall rules if needed:"
echo -e "     ${BLUE}ufw allow 5123/tcp${NC}  # Web Dashboard"
echo -e "     ${BLUE}ufw allow 5124/tcp${NC}  # API Server (if remote access needed)"
echo ""
echo -e "${BLUE}Useful commands:${NC}"
echo -e "  systemctl status logwhisperer-api    # Check API status"
echo -e "  systemctl status logwhisperer-web    # Check web status"
echo -e "  journalctl -u logwhisperer-api -f    # View API logs"
echo -e "  journalctl -u logwhisperer-web -f    # View web logs"
echo -e "  systemctl restart logwhisperer-web   # Restart web dashboard"
echo ""

# Optional: Open in browser
if command -v xdg-open &> /dev/null && [ -n "$DISPLAY" ]; then
    read -p "Open dashboard in browser? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        xdg-open "http://localhost:5123" &
    fi
fi