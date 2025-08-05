#!/bin/bash

set -euo pipefail

# Script metadata
readonly SCRIPT_VERSION="1.1.0"
readonly SCRIPT_NAME="LogWhisperer Installer"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Color codes for output
readonly GREEN='\033[0;32m'
readonly RED='\033[0;31m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Installation defaults
readonly DEFAULT_MODEL="mistral"
readonly DEFAULT_INSTALL_DIR="/opt/logwhisperer"
readonly DEFAULT_CONFIG_DIR="/etc/logwhisperer"
readonly DEFAULT_LOG_DIR="/var/log/logwhisperer"
readonly DEFAULT_BACKUP_DIR="/var/backups/logwhisperer"
readonly SUPPORTED_MODELS=("mistral" "llama2" "codellama" "phi" "gemma" "tinyllama" "dolphin-mixtral")
readonly REQUIRED_COMMANDS=("curl" "systemctl" "grep" "awk" "sed")
readonly PYTHON_REQUIRED="3.7"

# Installation options
INSTALL_MODEL="$DEFAULT_MODEL"
INSTALL_DIR="$DEFAULT_INSTALL_DIR"
CONFIG_DIR="$DEFAULT_CONFIG_DIR"
LOG_DIR="$DEFAULT_LOG_DIR"
INSTALL_SERVICE=false
INSTALL_WEB=false
SKIP_OLLAMA=false
UNINSTALL=false
UPGRADE=false
DRY_RUN=false
VERBOSE=false
FORCE=false
DEV_MODE=false
DEV_KEY=""

# Logging setup
LOGFILE="${SCRIPT_DIR}/install_$(date +%Y%m%d_%H%M%S).log"
exec 3>&1 4>&2
trap 'exec 2>&4 1>&3' 0 1 2 3
exec 1> >(tee -a "$LOGFILE")
exec 2>&1

# Utility functions
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*"
}

error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $*"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

verbose() {
    if [[ "$VERBOSE" == true ]]; then
        echo -e "${BLUE}[VERBOSE]${NC} $*"
    fi
}

die() {
    error "$@"
    exit 1
}

confirm() {
    local prompt="${1:-Continue?}"
    local response
    
    if [[ "$FORCE" == true ]]; then
        return 0
    fi
    
    while true; do
        read -p "$prompt (y/N): " response
        case "${response,,}" in
            y|yes) return 0 ;;
            n|no|"") return 1 ;;
            *) echo "Please answer yes or no." ;;
        esac
    done
}

# Help function
show_help() {
    cat << EOF
$SCRIPT_NAME v$SCRIPT_VERSION

Usage: $0 [OPTIONS]

OPTIONS:
    -h, --help              Show this help message
    -v, --version           Show version information
    -V, --verbose           Enable verbose output
    -f, --force             Skip confirmation prompts
    -n, --dry-run           Show what would be done without making changes
    
    --model MODEL           LLM model to install (default: $DEFAULT_MODEL)
    --install-dir DIR       Installation directory (default: $DEFAULT_INSTALL_DIR)
    --config-dir DIR        Configuration directory (default: $DEFAULT_CONFIG_DIR)
    --log-dir DIR           Log directory (default: $DEFAULT_LOG_DIR)
    
    --with-service          Install and enable systemd service
    --with-web              Install web dashboard components
    --skip-ollama           Skip Ollama installation
    --upgrade               Upgrade existing installation
    --uninstall             Uninstall LogWhisperer
    
    --dev-mode              Enable development mode (bypass license)
    --dev-key KEY           Development key for dev mode
    
SUPPORTED MODELS:
    ${SUPPORTED_MODELS[*]}
    
EXAMPLES:
    # Basic installation
    $0
    
    # Install with specific model, service, and web dashboard
    $0 --model llama2 --with-service --with-web
    
    # Upgrade existing installation
    $0 --upgrade
    
    # Uninstall
    $0 --uninstall

EOF
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_help
                exit 0
                ;;
            -v|--version)
                echo "$SCRIPT_NAME v$SCRIPT_VERSION"
                exit 0
                ;;
            -V|--verbose)
                VERBOSE=true
                shift
                ;;
            -f|--force)
                FORCE=true
                shift
                ;;
            -n|--dry-run)
                DRY_RUN=true
                shift
                ;;
            --model)
                INSTALL_MODEL="$2"
                shift 2
                ;;
            --install-dir)
                INSTALL_DIR="$2"
                shift 2
                ;;
            --config-dir)
                CONFIG_DIR="$2"
                shift 2
                ;;
            --log-dir)
                LOG_DIR="$2"
                shift 2
                ;;
            --with-service)
                INSTALL_SERVICE=true
                shift
                ;;
            --with-web)
                INSTALL_WEB=true
                shift
                ;;
            --skip-ollama)
                SKIP_OLLAMA=true
                shift
                ;;
            --upgrade)
                UPGRADE=true
                shift
                ;;
            --uninstall)
                UNINSTALL=true
                shift
                ;;
            --dev-mode)
                DEV_MODE=true
                shift
                ;;
            --dev-key)
                DEV_KEY="$2"
                shift 2
                ;;
            *)
                error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

# Check system requirements
check_requirements() {
    info "Checking system requirements..."
    
    # Check OS
    if [[ "$OSTYPE" != "linux-gnu"* ]]; then
        die "This installer only supports Linux systems"
    fi
    
    # Check if running as root when needed
    if [[ "$INSTALL_SERVICE" == true || "$INSTALL_WEB" == true ]] && [[ "$EUID" -ne 0 ]]; then
        die "Service/Web installation requires root privileges. Please run with sudo."
    fi
    
    # Check required commands
    local missing_commands=()
    for cmd in "${REQUIRED_COMMANDS[@]}"; do
        if ! command -v "$cmd" &>/dev/null; then
            missing_commands+=("$cmd")
        fi
    done
    
    if [[ ${#missing_commands[@]} -gt 0 ]]; then
        die "Missing required commands: ${missing_commands[*]}"
    fi
    
    # Check Python if web dashboard is requested
    if [[ "$INSTALL_WEB" == true ]]; then
        if ! command -v python3 &>/dev/null; then
            die "Python 3 is required for web dashboard"
        fi
        
        # Check Python version
        local python_version=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        local required_major=$(echo $PYTHON_REQUIRED | cut -d. -f1)
        local required_minor=$(echo $PYTHON_REQUIRED | cut -d. -f2)
        local actual_major=$(echo $python_version | cut -d. -f1)
        local actual_minor=$(echo $python_version | cut -d. -f2)
        
        if [[ $actual_major -lt $required_major ]] || \
           [[ $actual_major -eq $required_major && $actual_minor -lt $required_minor ]]; then
            die "Python $PYTHON_REQUIRED or higher is required (found $python_version)"
        fi
        
        # Check pip
        if ! command -v pip3 &>/dev/null; then
            die "pip3 is required for web dashboard installation"
        fi
    fi
    
    # Check disk space
    local check_dir="$INSTALL_DIR"
    if [[ ! -d "$check_dir" ]]; then
        check_dir="$(dirname "$INSTALL_DIR")"
        while [[ ! -d "$check_dir" ]] && [[ "$check_dir" != "/" ]]; do
            check_dir="$(dirname "$check_dir")"
        done
    fi
    
    verbose "Checking disk space on: $check_dir"
    local free_space
    free_space=$(df -BG "$check_dir" 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//')
    if [[ -n "$free_space" ]] && [[ "$free_space" -lt 1 ]]; then
        warning "Less than 1GB free space available in $check_dir"
    fi
    
    # Check if model is supported
    if [[ ! " ${SUPPORTED_MODELS[*]} " =~ " $INSTALL_MODEL " ]]; then
        die "Unsupported model: $INSTALL_MODEL. Supported models: ${SUPPORTED_MODELS[*]}"
    fi
    
    verbose "System requirements check passed"
}

# Backup existing installation
backup_existing() {
    if [[ -d "$INSTALL_DIR" ]]; then
        info "Backing up existing installation..."
        
        local backup_name="logwhisperer_backup_$(date +%Y%m%d_%H%M%S)"
        local backup_path="${DEFAULT_BACKUP_DIR}/${backup_name}"
        
        if [[ "$DRY_RUN" == true ]]; then
            info "[DRY RUN] Would create backup at $backup_path"
        else
            mkdir -p "$DEFAULT_BACKUP_DIR"
            cp -r "$INSTALL_DIR" "$backup_path"
            info "Backup created at $backup_path"
        fi
    fi
}

# Install Ollama
install_ollama() {
    if [[ "$SKIP_OLLAMA" == true ]]; then
        info "Skipping Ollama installation (--skip-ollama specified)"
        return 0
    fi
    
    if command -v ollama &>/dev/null; then
        info "Ollama is already installed"
        return 0
    fi
    
    info "Installing Ollama..."
    
    if [[ "$DRY_RUN" == true ]]; then
        info "[DRY RUN] Would install Ollama"
        return 0
    fi
    
    # Download and verify Ollama installer
    local ollama_installer="/tmp/ollama_install.sh"
    curl -fsSL https://ollama.com/install.sh -o "$ollama_installer" || die "Failed to download Ollama installer"
    
    # Run installer
    bash "$ollama_installer" || die "Failed to install Ollama"
    rm -f "$ollama_installer"
    
    # Wait for Ollama to start
    info "Waiting for Ollama to start..."
    local max_attempts=30
    local attempt=0
    
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -s http://localhost:11434 >/dev/null 2>&1; then
            info "Ollama is running"
            break
        fi
        sleep 1
        ((attempt++))
    done
    
    if [[ $attempt -eq $max_attempts ]]; then
        die "Ollama failed to start within 30 seconds"
    fi
}

# Pull Ollama models
pull_models() {
    if [[ "$SKIP_OLLAMA" == true ]]; then
        return 0
    fi
    
    info "Pulling Ollama models..."
    
    if [[ "$DRY_RUN" == true ]]; then
        info "[DRY RUN] Would pull models: $INSTALL_MODEL, phi"
        return 0
    fi
    
    # Pull requested model
    info "Pulling model: $INSTALL_MODEL"
    ollama pull "$INSTALL_MODEL" || warning "Failed to pull model: $INSTALL_MODEL"
    
    # Always pull phi as a lightweight backup model
    if [[ "$INSTALL_MODEL" != "phi" ]]; then
        info "Pulling backup model: phi"
        ollama pull phi || warning "Failed to pull backup model: phi"
    fi
}

# Create directory structure
create_directories() {
    info "Creating directory structure..."
    
    local dirs=(
        "$INSTALL_DIR"
        "$INSTALL_DIR/bin"
        "$INSTALL_DIR/modules"
        "$INSTALL_DIR/reports"
        "$CONFIG_DIR"
        "$LOG_DIR"
    )
    
    # Add web directories if needed
    if [[ "$INSTALL_WEB" == true ]]; then
        dirs+=(
            "$INSTALL_DIR/web"
            "$INSTALL_DIR/web/templates"
            "$INSTALL_DIR/web/static"
        )
    fi
    
    for dir in "${dirs[@]}"; do
        if [[ "$DRY_RUN" == true ]]; then
            info "[DRY RUN] Would create directory: $dir"
        else
            mkdir -p "$dir"
            verbose "Created directory: $dir"
        fi
    done
}

# Install Python virtual environment for web
install_web_venv() {
    if [[ "$INSTALL_WEB" != true ]] || [[ "$DRY_RUN" == true ]]; then
        return 0
    fi
    
    info "Creating Python virtual environment for web dashboard..."
    
    local venv_dir="$INSTALL_DIR/web-venv"
    
    # Create virtual environment
    python3 -m venv "$venv_dir" || die "Failed to create virtual environment"
    
    # Activate and install dependencies
    source "$venv_dir/bin/activate"
    
    # Upgrade pip
    pip install --upgrade pip >/dev/null 2>&1
    
    # Install web dependencies
    local web_deps=(
        "flask>=2.0.0"
        "flask-socketio>=5.0.0"
        "flask-compress>=1.10.0"
        "flask-cors>=3.0.0"
        "python-socketio[client]>=5.0.0"
        "werkzeug>=2.0.0"
        "pyyaml>=5.4.0"
        "requests>=2.25.0"
        "gunicorn>=20.1.0"
        "eventlet>=0.30.0"
    )
    
    for dep in "${web_deps[@]}"; do
        pip install "$dep" || warning "Failed to install $dep"
    done
    
    deactivate
    
    info "Web dependencies installed successfully"
}

# Install files
install_files() {
    info "Installing LogWhisperer files..."
    
    # Check if required files exist
    if [[ ! -f "$SCRIPT_DIR/bin/logwhisperer" ]]; then
        die "Required file not found: bin/logwhisperer"
    fi
    
    if [[ ! -x "$SCRIPT_DIR/bin/logwhisperer" ]]; then
        die "logwhisperer binary is not executable"
    fi
    
    # Check for config
    local config_source=""
    if [[ -f "$SCRIPT_DIR/config/config.yaml.example" ]]; then
        config_source="$SCRIPT_DIR/config/config.yaml.example"
    elif [[ -f "$SCRIPT_DIR/config/config.yaml" ]]; then
        config_source="$SCRIPT_DIR/config/config.yaml"
    else
        die "Required file not found: config/config.yaml or config/config.yaml.example"
    fi
    
    if [[ "$DRY_RUN" == true ]]; then
        info "[DRY RUN] Would install files to $INSTALL_DIR"
        return 0
    fi
    
    # Install binary
    cp "$SCRIPT_DIR/bin/logwhisperer" "$INSTALL_DIR/bin/"
    chmod +x "$INSTALL_DIR/bin/logwhisperer"
    info "Installed: logwhisperer binary"
    
    # Install modules directory
    if [[ -d "$SCRIPT_DIR/modules" ]]; then
        cp -r "$SCRIPT_DIR/modules" "$INSTALL_DIR/"
        info "Installed: modules directory"
    fi
    
    # Install web components if requested
    if [[ "$INSTALL_WEB" == true ]]; then
        # API server
        if [[ -f "$SCRIPT_DIR/web/api_server.py" ]]; then
            cp "$SCRIPT_DIR/web/api_server.py" "$INSTALL_DIR/web/"
        elif [[ -f "$SCRIPT_DIR/api_server.py" ]]; then
            cp "$SCRIPT_DIR/api_server.py" "$INSTALL_DIR/web/"
        else
            warning "api_server.py not found, web dashboard may not function"
        fi
        
        # WSGI entry point
        if [[ -f "$SCRIPT_DIR/web/wsgi.py" ]]; then
            cp "$SCRIPT_DIR/web/wsgi.py" "$INSTALL_DIR/web/"
        elif [[ -f "$SCRIPT_DIR/wsgi.py" ]]; then
            cp "$SCRIPT_DIR/wsgi.py" "$INSTALL_DIR/web/"
        else
            # Create wsgi.py if it doesn't exist
            cat > "$INSTALL_DIR/web/wsgi.py" << 'EOF'
#!/usr/bin/env python3
"""WSGI entry point for LogWhisperer API Server"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from api_server import create_app
config_path = os.environ.get('LOGWHISPERER_CONFIG', '/etc/logwhisperer/config.yaml')
app, socketio = create_app(config_path)
application = app
EOF
            chmod 644 "$INSTALL_DIR/web/wsgi.py"
            info "Created wsgi.py entry point"
        fi
        
        # Dashboard
        if [[ -f "$SCRIPT_DIR/web/dashboard.py" ]]; then
            cp "$SCRIPT_DIR/web/dashboard.py" "$INSTALL_DIR/web/"
        elif [[ -f "$SCRIPT_DIR/dashboard.py" ]]; then
            cp "$SCRIPT_DIR/dashboard.py" "$INSTALL_DIR/web/"
        else
            warning "dashboard.py not found, web dashboard may not function"
        fi
        
        # Templates
        if [[ -d "$SCRIPT_DIR/web/templates" ]]; then
            cp -r "$SCRIPT_DIR/web/templates/"* "$INSTALL_DIR/web/templates/"
        elif [[ -d "$SCRIPT_DIR/templates" ]]; then
            cp -r "$SCRIPT_DIR/templates/"* "$INSTALL_DIR/web/templates/"
        fi
        
        info "Installed: web dashboard components"
    fi
    
    # Install config
    if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
        cp "$config_source" "$CONFIG_DIR/config.yaml"
        chmod 644 "$CONFIG_DIR/config.yaml"
        info "Installed default configuration"
        
        # Update config with web settings if web is installed
        if [[ "$INSTALL_WEB" == true ]]; then
            if ! grep -q "^web:" "$CONFIG_DIR/config.yaml"; then
                cat >> "$CONFIG_DIR/config.yaml" << 'EOF'

# Web Dashboard Configuration
web:
  enabled: true
  host: "0.0.0.0"
  port: 5123
  api_url: "http://localhost:5124"
  users:
    admin: "changeme"  # CHANGE THIS PASSWORD!
  session_timeout: 3600
  max_log_entries: 1000
  update_interval: 1.0
EOF
                info "Added web configuration to config.yaml"
            fi
        fi
        
        warning "Please edit $CONFIG_DIR/config.yaml to configure LogWhisperer"
    else
        info "Configuration already exists, not overwriting"
    fi
}

# Create web templates
create_web_templates() {
    if [[ "$INSTALL_WEB" != true ]] || [[ "$DRY_RUN" == true ]]; then
        return 0
    fi
    
    # Check if templates already exist and have content
    if [[ -f "$INSTALL_DIR/web/templates/base.html" ]] && [[ -s "$INSTALL_DIR/web/templates/base.html" ]]; then
        verbose "Templates already exist with content"
        return 0
    fi
    
    info "Creating web dashboard templates..."
    
    cd "$INSTALL_DIR/web"
    source "$INSTALL_DIR/web-venv/bin/activate"
    
    # Create templates using dashboard.py
    python3 dashboard.py --create-templates || {
        warning "Failed to create templates automatically"
        deactivate
        return 1
    }
    
    deactivate
    
    # Verify templates were created
    if [[ -f "$INSTALL_DIR/web/templates/base.html" ]] && [[ -s "$INSTALL_DIR/web/templates/base.html" ]]; then
        info "Web dashboard templates created successfully"
    else
        warning "Templates may not have been created properly"
    fi
}

# Create wrapper scripts
create_wrappers() {
    info "Creating wrapper scripts..."
    
    if [[ "$DRY_RUN" == true ]]; then
        info "[DRY RUN] Would create wrapper scripts"
        return 0
    fi
    
    # Main wrapper
    local wrapper_path="/usr/local/bin/logwhisperer"
    cat > "$wrapper_path" << EOF
#!/bin/bash
# LogWhisperer wrapper script
# Generated by installer v$SCRIPT_VERSION

export LOGWHISPERER_CONFIG="${CONFIG_DIR}/config.yaml"
EOF

    # Add dev mode environment variables if enabled
    if [[ "$DEV_MODE" == true ]]; then
        cat >> "$wrapper_path" << EOF
# Development mode enabled
export LOGWHISPERER_DEV_MODE="true"
export LOGWHISPERER_DEV_KEY="${DEV_KEY}"
EOF
        info "Development mode enabled in wrapper"
    fi

    cat >> "$wrapper_path" << EOF
exec "$INSTALL_DIR/bin/logwhisperer" "\$@"
EOF
    chmod +x "$wrapper_path"
    verbose "Created main wrapper at $wrapper_path"
    
    # Web dashboard wrappers if installed
    if [[ "$INSTALL_WEB" == true ]]; then
        # API server wrapper
        mkdir -p "$INSTALL_DIR/bin"
        cat > "$INSTALL_DIR/bin/logwhisperer-api" << EOF
#!/bin/bash
# LogWhisperer API Server wrapper - Production

cd "$INSTALL_DIR/web"
source "$INSTALL_DIR/web-venv/bin/activate"
export LOGWHISPERER_CONFIG="${CONFIG_DIR}/config.yaml"
EOF

        # Add dev mode to API wrapper too
        if [[ "$DEV_MODE" == true ]]; then
            cat >> "$INSTALL_DIR/bin/logwhisperer-api" << EOF
export LOGWHISPERER_DEV_MODE="true"
export LOGWHISPERER_DEV_KEY="${DEV_KEY}"
EOF
        fi

        # Use gunicorn with eventlet for production (removed --keepalive option)
        cat >> "$INSTALL_DIR/bin/logwhisperer-api" << 'EOF'

# Production server using gunicorn with eventlet workers
exec gunicorn --worker-class eventlet --workers 1 --bind 0.0.0.0:5124 --timeout 120 --log-level info --access-logfile - --error-logfile - --worker-connections 1000 wsgi:application
EOF
        chmod +x "$INSTALL_DIR/bin/logwhisperer-api"
        
        # Web dashboard wrapper
        cat > "$INSTALL_DIR/bin/logwhisperer-web" << EOF
#!/bin/bash
# LogWhisperer Web Dashboard wrapper - Production

cd "$INSTALL_DIR/web"
source "$INSTALL_DIR/web-venv/bin/activate"
export LOGWHISPERER_CONFIG="${CONFIG_DIR}/config.yaml"
EOF

        # Add dev mode to web wrapper too
        if [[ "$DEV_MODE" == true ]]; then
            cat >> "$INSTALL_DIR/bin/logwhisperer-web" << EOF
export LOGWHISPERER_DEV_MODE="true"
export LOGWHISPERER_DEV_KEY="${DEV_KEY}"
EOF
        fi

        # Use gunicorn for production web server (removed --keepalive option)
        cat >> "$INSTALL_DIR/bin/logwhisperer-web" << 'EOF'

# Production server using gunicorn
exec gunicorn --workers 2 --bind 0.0.0.0:5123 --timeout 120 --log-level info --access-logfile - --error-logfile - dashboard:app
EOF
        chmod +x "$INSTALL_DIR/bin/logwhisperer-web"
        
        verbose "Created web component wrappers"
    fi
}

# Install systemd services
install_services() {
    if [[ "$INSTALL_SERVICE" != true ]] && [[ "$INSTALL_WEB" != true ]]; then
        return 0
    fi
    
    if [[ "$DRY_RUN" == true ]]; then
        info "[DRY RUN] Would install systemd services"
        return 0
    fi
    
    # Main service
    if [[ "$INSTALL_SERVICE" == true ]]; then
        info "Installing LogWhisperer monitoring service..."
        
        cat > "/etc/systemd/system/logwhisperer.service" << EOF
[Unit]
Description=LogWhisperer Monitoring Agent
Documentation=https://github.com/yourusername/logwhisperer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/logwhisperer monitor
WorkingDirectory=$INSTALL_DIR
Restart=always
RestartSec=10
StandardOutput=append:${LOG_DIR}/logwhisperer.log
StandardError=append:${LOG_DIR}/logwhisperer.error.log

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$LOG_DIR $INSTALL_DIR/reports

# Resource limits
LimitNOFILE=65536
MemoryLimit=1G

# Environment
Environment="PYTHONUNBUFFERED=1"
Environment="LOGWHISPERER_CONFIG=$CONFIG_DIR/config.yaml"
EOF

        # Add dev mode environment variables to service if enabled
        if [[ "$DEV_MODE" == true ]]; then
            sed -i '/Environment="LOGWHISPERER_CONFIG=/a Environment="LOGWHISPERER_DEV_MODE=true"\nEnvironment="LOGWHISPERER_DEV_KEY='$DEV_KEY'"' "/etc/systemd/system/logwhisperer.service"
        fi

        cat >> "/etc/systemd/system/logwhisperer.service" << EOF

[Install]
WantedBy=multi-user.target
EOF
        
        systemctl daemon-reload
        systemctl enable logwhisperer.service
        info "Main monitoring service installed and enabled"
    fi
    
    # Web services
    if [[ "$INSTALL_WEB" == true ]]; then
        info "Installing web dashboard services..."
        
        # API service
        cat > "/etc/systemd/system/logwhisperer-api.service" << EOF
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
Environment="PYTHONUNBUFFERED=1"
EOF

        # Add dev mode to API service
        if [[ "$DEV_MODE" == true ]]; then
            cat >> "/etc/systemd/system/logwhisperer-api.service" << EOF
Environment="LOGWHISPERER_DEV_MODE=true"
Environment="LOGWHISPERER_DEV_KEY=$DEV_KEY"
EOF
        fi

        cat >> "/etc/systemd/system/logwhisperer-api.service" << EOF

[Install]
WantedBy=multi-user.target
EOF
        
        # Web service
        cat > "/etc/systemd/system/logwhisperer-web.service" << EOF
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
Environment="PYTHONUNBUFFERED=1"
EOF

        # Add dev mode to web service
        if [[ "$DEV_MODE" == true ]]; then
            cat >> "/etc/systemd/system/logwhisperer-web.service" << EOF
Environment="LOGWHISPERER_DEV_MODE=true"
Environment="LOGWHISPERER_DEV_KEY=$DEV_KEY"
EOF
        fi

        cat >> "/etc/systemd/system/logwhisperer-web.service" << EOF

[Install]
WantedBy=multi-user.target
EOF
        
        systemctl daemon-reload
        systemctl enable logwhisperer-api.service
        systemctl enable logwhisperer-web.service
        info "Web dashboard services installed and enabled"
    fi
}

# Start services
start_services() {
    if [[ "$DRY_RUN" == true ]]; then
        return 0
    fi
    
    local services_to_start=()
    
    if [[ "$INSTALL_SERVICE" == true ]]; then
        services_to_start+=("logwhisperer")
    fi
    
    if [[ "$INSTALL_WEB" == true ]]; then
        services_to_start+=("logwhisperer-api" "logwhisperer-web")
    fi
    
    if [[ ${#services_to_start[@]} -eq 0 ]]; then
        return 0
    fi
    
    if confirm "Start services now?"; then
        for service in "${services_to_start[@]}"; do
            info "Starting $service..."
            systemctl start "${service}.service"
            sleep 2
            
            if systemctl is-active --quiet "${service}.service"; then
                info "$service is running"
            else
                warning "$service failed to start. Check: journalctl -u $service -n 50"
            fi
        done
    fi
}

# Setup log rotation
setup_log_rotation() {
    info "Setting up log rotation..."
    
    if [[ "$DRY_RUN" == true ]]; then
        info "[DRY RUN] Would create logrotate config"
        return 0
    fi
    
    cat > "/etc/logrotate.d/logwhisperer" << EOF
$LOG_DIR/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
    sharedscripts
    postrotate
        systemctl reload logwhisperer.service > /dev/null 2>&1 || true
    endscript
}
EOF
    
    verbose "Created logrotate configuration"
}

# Verify installation
verify_installation() {
    info "Verifying installation..."
    
    local errors=0
    
    # Check main executable
    if [[ ! -x "/usr/local/bin/logwhisperer" ]]; then
        error "LogWhisperer executable not found"
        ((errors++))
    fi
    
    # Check config
    if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
        error "Configuration file not found"
        ((errors++))
    fi
    
    # Test basic functionality
    if ! /usr/local/bin/logwhisperer --version &>/dev/null; then
        error "LogWhisperer failed to run"
        ((errors++))
    fi
    
    # Check web components if installed
    if [[ "$INSTALL_WEB" == true ]]; then
        if [[ ! -f "$INSTALL_DIR/web/api_server.py" ]]; then
            warning "API server not found"
        fi
        
        if [[ ! -f "$INSTALL_DIR/web/dashboard.py" ]]; then
            warning "Dashboard not found"
        fi
        
        if [[ ! -d "$INSTALL_DIR/web-venv" ]]; then
            warning "Python virtual environment not found"
        fi
    fi
    
    # Check Ollama
    if [[ "$SKIP_OLLAMA" != true ]]; then
        if ! curl -s http://localhost:11434 >/dev/null 2>&1; then
            warning "Ollama is not accessible"
        fi
    fi
    
    if [[ $errors -eq 0 ]]; then
        info "Installation verified successfully"
        return 0
    else
        error "Installation verification failed with $errors errors"
        return 1
    fi
}

# Uninstall function
uninstall() {
    info "Uninstalling LogWhisperer..."
    
    if ! confirm "Are you sure you want to uninstall LogWhisperer?"; then
        info "Uninstall cancelled"
        exit 0
    fi
    
    # Stop and disable services
    local services=("logwhisperer" "logwhisperer-api" "logwhisperer-web")
    for service in "${services[@]}"; do
        if systemctl is-active --quiet "${service}.service" 2>/dev/null; then
            info "Stopping $service..."
            systemctl stop "${service}.service"
            systemctl disable "${service}.service"
        fi
    done
    
    # Remove files
    local items_to_remove=(
        "/usr/local/bin/logwhisperer"
        "/etc/systemd/system/logwhisperer.service"
        "/etc/systemd/system/logwhisperer-api.service"
        "/etc/systemd/system/logwhisperer-web.service"
        "/etc/logrotate.d/logwhisperer"
        "$INSTALL_DIR"
    )
    
    for item in "${items_to_remove[@]}"; do
        if [[ -e "$item" ]]; then
            rm -rf "$item"
            verbose "Removed: $item"
        fi
    done
    
    # Optionally remove config and logs
    if confirm "Remove configuration and logs?"; then
        rm -rf "$CONFIG_DIR" "$LOG_DIR"
        verbose "Removed configuration and logs"
    fi
    
    systemctl daemon-reload
    
    info "LogWhisperer has been uninstalled"
}

# Print summary
print_summary() {
    echo
    log "Installation Summary:"
    echo "  Install directory: $INSTALL_DIR"
    echo "  Config directory:  $CONFIG_DIR"
    echo "  Log directory:     $LOG_DIR"
    echo "  Ollama model:      $INSTALL_MODEL"
    
    if [[ "$DEV_MODE" == true ]]; then
        echo -e "  ${YELLOW}Development mode: ENABLED${NC}"
        echo -e "  ${YELLOW}Dev key:          ${DEV_KEY:0:10}...${NC}"
    fi
    
    if [[ "$INSTALL_SERVICE" == true ]]; then
        echo "  Monitor service:   Installed and enabled"
    fi
    
    if [[ "$INSTALL_WEB" == true ]]; then
        echo "  Web dashboard:     Installed and enabled"
        echo "  Web UI URL:        http://$(hostname -I | awk '{print $1}'):5123"
        echo "  API URL:           http://$(hostname -I | awk '{print $1}'):5124"
    fi
    
    echo
    log "Next steps:"
    
    if [[ "$DEV_MODE" == true ]]; then
        echo -e "  ${YELLOW}⚠️  Development mode is active - license checks bypassed${NC}"
    else
        echo "  1. Activate license:     logwhisperer activate"
    fi
    
    echo "  2. Review configuration: $CONFIG_DIR/config.yaml"
    
    if [[ "$INSTALL_WEB" == true ]]; then
        echo "     - Set Discord webhook URL"
        echo "     - Change web dashboard password (current: admin/changeme)"
    fi
    
    echo "  3. Test installation:    logwhisperer test"
    echo "  4. Run summarization:    logwhisperer summarize"
    
    if [[ "$INSTALL_SERVICE" == true ]]; then
        echo "  5. Check monitor:        systemctl status logwhisperer"
    else
        echo "  5. Start monitoring:     logwhisperer monitor"
    fi
    
    if [[ "$INSTALL_WEB" == true ]]; then
        echo "  6. Access dashboard:     http://$(hostname -I | awk '{print $1}'):5123"
        echo "     Default login:        admin / changeme"
    fi
    
    echo
    log "Installation completed successfully!"
    echo "Log file: $LOGFILE"
    
    if [[ "$INSTALL_WEB" == true ]]; then
        echo
        warning "IMPORTANT: Configure firewall for web access:"
        echo "  sudo ufw allow 5123/tcp  # Web Dashboard"
        echo "  sudo ufw allow 5124/tcp  # API Server (if remote access needed)"
    fi
}

# Main function
main() {
    log "$SCRIPT_NAME v$SCRIPT_VERSION"
    
    parse_args "$@"
    
    # Validate dev mode configuration
    if [[ "$DEV_MODE" == true ]] && [[ -z "$DEV_KEY" ]]; then
        die "Development mode requires --dev-key to be specified"
    fi
    
    if [[ "$DEV_MODE" == true ]]; then
        warning "Development mode enabled - this bypasses license checks"
        info "This should only be used for development and testing"
    fi
    
    if [[ "$UNINSTALL" == true ]]; then
        uninstall
        exit 0
    fi
    
    check_requirements
    
    if [[ "$UPGRADE" == true ]]; then
        backup_existing
    fi
    
    install_ollama
    pull_models
    create_directories
    install_files
    
    if [[ "$INSTALL_WEB" == true ]]; then
        install_web_venv
        create_web_templates  # Create templates after installing web files
    fi
    
    create_wrappers
    
    # If running as root and services not explicitly set, ask user
    if [[ "$EUID" -eq 0 ]] && [[ "$DRY_RUN" != true ]]; then
        if [[ "$INSTALL_SERVICE" != true ]] && confirm "Install LogWhisperer monitoring as a systemd service?"; then
            INSTALL_SERVICE=true
        fi
        
        if [[ "$INSTALL_WEB" != true ]] && confirm "Install web dashboard (requires Python)?"; then
            INSTALL_WEB=true
            # Need to install venv after user confirms
            install_web_venv
            create_web_templates  # Create templates after installing venv
        fi
    fi
    
    if [[ "$EUID" -eq 0 ]]; then
        install_services
        setup_log_rotation
        start_services
    elif [[ "$INSTALL_SERVICE" == true || "$INSTALL_WEB" == true ]] && [[ "$EUID" -ne 0 ]]; then
        warning "Skipping service installation (requires root)"
    fi
    
    if [[ "$DRY_RUN" != true ]]; then
        verify_installation
        print_summary
    else
        info "[DRY RUN] Installation steps completed (no changes made)"
    fi
}

# Run main function
main "$@"