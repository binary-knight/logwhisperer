#!/bin/bash
# LogWhisperer License Activation Script

set -euo pipefail

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Functions
success() { echo -e "${GREEN}✓${NC} $*"; }
error() { echo -e "${RED}✗${NC} $*" >&2; }
info() { echo -e "${BLUE}ℹ${NC} $*"; }
warning() { echo -e "${YELLOW}⚠${NC} $*"; }

echo "╔══════════════════════════════════════════╗"
echo "║     LogWhisperer License Activation      ║"
echo "╚══════════════════════════════════════════╝"
echo

# Check if LogWhisperer is installed
if ! command -v logwhisperer >/dev/null 2>&1; then
    error "LogWhisperer is not installed or not in PATH"
    echo "Please install LogWhisperer first using install.sh"
    exit 1
fi

# Check current license status
echo "Checking current license status..."
if logwhisperer --check-license >/dev/null 2>&1; then
    success "License is already activated!"
    logwhisperer --check-license
    exit 0
fi

# Prompt for license key
echo
echo "Enter your Gumroad license key:"
echo "(Get one at: https://gumroad.com/l/logwhisperer)"
echo
read -p "License key: " LICENSE_KEY

if [[ -z "$LICENSE_KEY" ]]; then
    error "No license key provided"
    exit 1
fi

# Validate the license
echo
echo "Validating license..."
export LOGWHISPERER_LICENSE_KEY="$LICENSE_KEY"

if logwhisperer --check-license; then
    success "License validated successfully!"
    
    # Ask about saving the license
    echo
    read -p "Save license key to your shell profile? (y/N) " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Detect shell
        SHELL_RC=""
        if [[ -n "${BASH_VERSION:-}" ]]; then
            SHELL_RC="$HOME/.bashrc"
        elif [[ -n "${ZSH_VERSION:-}" ]]; then
            SHELL_RC="$HOME/.zshrc"
        else
            SHELL_RC="$HOME/.profile"
        fi
        
        # Add to shell profile
        echo "" >> "$SHELL_RC"
        echo "# LogWhisperer License" >> "$SHELL_RC"
        echo "export LOGWHISPERER_LICENSE_KEY='$LICENSE_KEY'" >> "$SHELL_RC"
        
        success "License key saved to $SHELL_RC"
        info "Run 'source $SHELL_RC' or restart your terminal"
    fi
    
    # Create systemd service file if requested
    if [[ -d /etc/systemd/system ]] && [[ $EUID -eq 0 ]]; then
        read -p "Create systemd service with license? (y/N) " -n 1 -r
        echo
        
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            cat > /etc/systemd/system/logwhisperer.service << EOF
[Unit]
Description=LogWhisperer Log Monitor
After=network.target

[Service]
Type=simple
User=logwhisperer
Group=logwhisperer
Environment="LOGWHISPERER_LICENSE_KEY=$LICENSE_KEY"
ExecStart=/usr/local/bin/logwhisperer monitor
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
            
            systemctl daemon-reload
            success "Systemd service created"
            info "Start with: systemctl start logwhisperer"
        fi
    fi
    
    echo
    success "LogWhisperer is now activated!"
    echo
    echo "You can now use:"
    echo "  logwhisperer summarize     - Summarize recent logs"
    echo "  logwhisperer monitor       - Start real-time monitoring"
    echo "  logwhisperer --help        - See all options"
    
else
    error "License validation failed"
    exit 1
fi