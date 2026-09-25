#!/bin/bash

set -euo pipefail

# Script metadata
readonly SCRIPT_NAME="LogWhisperer Build Script"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Color codes
readonly GREEN='\033[0;32m'
readonly RED='\033[0;31m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

# Build configuration
readonly BUILD_DIR="${SCRIPT_DIR}/build"
readonly RELEASE_DIR="${SCRIPT_DIR}/release"
readonly DIST_DIR="${SCRIPT_DIR}/dist"
readonly ARTIFACTS_DIR="${SCRIPT_DIR}/artifacts"

# Build options (can be overridden by environment)
PYTHON_CMD="${PYTHON_CMD:-python3}"
BUILD_TYPE="${BUILD_TYPE:-release}"  # release or debug
SKIP_TESTS="${SKIP_TESTS:-false}"
SKIP_CLEANUP="${SKIP_CLEANUP:-false}"
COMPRESS_BINARY="${COMPRESS_BINARY:-true}"
CREATE_CHECKSUM="${CREATE_CHECKSUM:-true}"
SIGN_RELEASE="${SIGN_RELEASE:-false}"
GPG_KEY="${GPG_KEY:-}"

# Platform detection
PLATFORM="unknown"
ARCH="unknown"
case "$(uname -s)" in
    Linux*)
        PLATFORM="linux"
        ARCH=$(uname -m)
        ;;
    Darwin*)
        PLATFORM="macos"
        ARCH=$(uname -m)
        ;;
    *)
        die "Unsupported platform: $(uname -s)"
        ;;
esac

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

die() {
    error "$@"
    exit 1
}

# Version detection with multiple fallbacks
detect_version() {
    local version=""
    
    # Try git describe first
    if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
        version=$(git describe --tags --abbrev=0 2>/dev/null || true)
        
        # If no tags, try to create version from commit
        if [[ -z "$version" ]]; then
            local commit_count=$(git rev-list --count HEAD 2>/dev/null || echo "0")
            local commit_hash=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
            version="0.0.0-dev.${commit_count}+${commit_hash}"
        fi
    fi
    
    # Try reading from version file
    if [[ -z "$version" ]] && [[ -f "${SCRIPT_DIR}/VERSION" ]]; then
        version=$(cat "${SCRIPT_DIR}/VERSION" | tr -d '[:space:]')
    fi
    
    # Try extracting from Python code
    if [[ -z "$version" ]] && [[ -f "${SCRIPT_DIR}/logwhisperer.py" ]]; then
        version=$(grep -E '__version__\s*=\s*"[^"]*"' "${SCRIPT_DIR}/logwhisperer.py" | sed 's/.*"\([^"]*\)".*/\1/' | head -1 || true)
    fi
    
    # Default version
    if [[ -z "$version" ]]; then
        version="1.0.0"
        warning "Could not detect version, using default: $version"
    fi
    
    echo "$version"
}

# Check prerequisites
check_requirements() {
    log "Checking build requirements..."
    
    local missing_deps=()
    
    # Check Python
    if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
        missing_deps+=("Python 3")
    else
        local python_version=$("$PYTHON_CMD" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        info "Python version: $python_version"
        
        # Check minimum Python version
        if [[ $(echo "$python_version" | cut -d. -f1) -lt 3 ]] || \
           [[ $(echo "$python_version" | cut -d. -f1) -eq 3 && $(echo "$python_version" | cut -d. -f2) -lt 7 ]]; then
            missing_deps+=("Python 3.7+")
        fi
    fi
    
    # Check Nuitka
    if ! "$PYTHON_CMD" -m pip show nuitka >/dev/null 2>&1; then
        missing_deps+=("Nuitka")
    else
        local nuitka_version=$("$PYTHON_CMD" -m nuitka --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        info "Nuitka version: $nuitka_version"
    fi
    
    # Check other required tools
    local required_cmds=("zip")
    if [[ "$CREATE_CHECKSUM" == "true" ]]; then
        # Platform-specific checksum commands
        if [[ "$PLATFORM" == "macos" ]]; then
            # macOS uses different commands
            if ! command -v shasum >/dev/null 2>&1; then
                missing_deps+=("shasum")
            fi
            if ! command -v md5 >/dev/null 2>&1; then
                missing_deps+=("md5")
            fi
        else
            # Linux uses these
            if ! command -v sha256sum >/dev/null 2>&1; then
                missing_deps+=("sha256sum")
            fi
            if ! command -v md5sum >/dev/null 2>&1; then
                missing_deps+=("md5sum")
            fi
        fi
    fi
    if [[ "$SIGN_RELEASE" == "true" ]]; then
        required_cmds+=("gpg")
    fi
    
    for cmd in "${required_cmds[@]}"; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing_deps+=("$cmd")
        fi
    done
    
    # Check required Python packages
    local required_packages=("yaml" "requests" "cryptography")
    for pkg in "${required_packages[@]}"; do
        if ! "$PYTHON_CMD" -c "import $pkg" 2>/dev/null; then
            missing_deps+=("Python package: $pkg")
        fi
    done
    
    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        error "Missing dependencies:"
        for dep in "${missing_deps[@]}"; do
            echo "  - $dep"
        done
        die "Please install missing dependencies"
    fi
    
    log "All requirements satisfied"
}

# Run tests
run_tests() {
    if [[ "$SKIP_TESTS" == "true" ]]; then
        warning "Skipping tests (SKIP_TESTS=true)"
        return 0
    fi
    
    log "Running tests..."
    
    # Check if test files exist
    if [[ -d "${SCRIPT_DIR}/tests" ]]; then
        if "$PYTHON_CMD" -m pytest tests/ -v; then
            log "Tests passed"
        else
            die "Tests failed"
        fi
    else
        warning "No tests directory found, skipping tests"
    fi
}

# Clean build artifacts
clean_artifacts() {
    log "Cleaning old build artifacts..."
    
    local dirs_to_clean=(
        "$BUILD_DIR"
        "$RELEASE_DIR"
        "$DIST_DIR"
        "${SCRIPT_DIR}/logwhisperer.dist"
        "${SCRIPT_DIR}/logwhisperer.build"
        "${SCRIPT_DIR}/logwhisperer.onefile-build"
        "${SCRIPT_DIR}/__pycache__"
        "${SCRIPT_DIR}/modules/__pycache__"
    )
    
    for dir in "${dirs_to_clean[@]}"; do
        if [[ -d "$dir" ]]; then
            rm -rf "$dir"
            info "Removed: $dir"
        fi
    done
    
    # Clean old build files
    find "$SCRIPT_DIR" -name "*.pyc" -delete
    find "$SCRIPT_DIR" -name "*.pyo" -delete
    find "$SCRIPT_DIR" -name "*.bin" -delete
    find "$SCRIPT_DIR" -name "*.spec" -delete
    
    # Clean old releases
    rm -f "${SCRIPT_DIR}"/logwhisperer_*.zip
    rm -f "${SCRIPT_DIR}"/logwhisperer_*.tar.gz
    rm -f "${SCRIPT_DIR}"/logwhisperer_*.sha256
    rm -f "${SCRIPT_DIR}"/logwhisperer_*.sig
}

# Build binary with Nuitka
build_binary() {
    log "Building binary with Nuitka..."
    
    mkdir -p "$BUILD_DIR"
    cd "$BUILD_DIR"
    
    # Fix permissions for certifi cacert.pem before building (macOS only)
    if [[ "$PLATFORM" == "macos" ]]; then
        log "Fixing permissions for macOS build..."
        local certifi_path=$("$PYTHON_CMD" -c "import certifi; import os; print(os.path.dirname(certifi.__file__))" 2>/dev/null || echo "")
        if [[ -n "$certifi_path" ]] && [[ -f "$certifi_path/cacert.pem" ]]; then
            chmod 644 "$certifi_path/cacert.pem" || true
            info "Fixed permissions for certifi cacert.pem"
        fi
    fi
        
    # Prepare Nuitka arguments
    local nuitka_args=(
        --standalone
        --onefile
        --assume-yes-for-downloads
        --output-dir="$BUILD_DIR"
        --include-package=yaml
        --include-package=requests
        --include-package=cryptography
        --include-package=modules
        --include-module=modules.monitor
        --include-module=modules.discord_alert
        --include-module=modules.summarizer
        --include-module=modules.spinner
        --include-module=modules.license
        --include-module=logwhisperer_oss
        --include-module=cryptography.hazmat
        --include-module=cryptography.hazmat.backends
        --include-module=cryptography.hazmat.backends.openssl
        --include-data-files="${SCRIPT_DIR}/modules/*.py=modules/"
        --include-data-files="${SCRIPT_DIR}/logwhisperer_oss.py=."
        --include-data-dir="${SCRIPT_DIR}/modules=modules"
        --follow-imports
        --include-package-data=modules
        --include-package-data=cryptography
        --enable-plugin=no-qt
        --show-progress
        --show-memory
    )
    
    # Add platform-specific options
    if [[ "$PLATFORM" == "linux" ]]; then
        # Check if icon exists before adding it
        if [[ -f "${SCRIPT_DIR}/assets/icon.png" ]]; then
            nuitka_args+=(
                --linux-onefile-icon="${SCRIPT_DIR}/assets/icon.png"
            )
        else
            warning "Icon not found at ${SCRIPT_DIR}/assets/icon.png, building without icon"
        fi
    fi
    
    # Add build type specific options
    if [[ "$BUILD_TYPE" == "release" ]]; then
        nuitka_args+=(
            --python-flag=no_site
            --python-flag=no_warnings
            --remove-output
        )
    else
        # Debug build - don't optimize, keep debug info
        nuitka_args+=(
            --python-flag=no_site
            # Don't use optimize flag for debug builds
            # Keep debug symbols
            --debug
            --trace-execution
        )
    fi
    
    # Add compression if requested
    if [[ "$COMPRESS_BINARY" == "true" ]]; then
        if command -v upx >/dev/null 2>&1; then
            nuitka_args+=(--enable-plugin=upx)
            info "UPX compression enabled"
        else
            warning "UPX not found, skipping compression"
        fi
    fi
    
    # Run Nuitka
    "$PYTHON_CMD" -m nuitka "${nuitka_args[@]}" "${SCRIPT_DIR}/logwhisperer.py"
    
    # Check if binary was created
    local binary_name="logwhisperer.bin"
    if [[ ! -f "$binary_name" ]]; then
        die "Binary not created"
    fi
    
    # Make binary executable
    chmod +x "$binary_name"
    
    # Verify binary
    if ./"$binary_name" --version >/dev/null 2>&1; then
        log "Binary verification passed"
    else
        die "Binary verification failed"
    fi
    
    cd "$SCRIPT_DIR"
}

sign_macos_binary() {
    if [[ "$PLATFORM" != "macos" ]]; then
        return 0
    fi
    
    # Check if we have signing credentials
    if [[ -z "${MACOS_IDENTITY:-}" ]]; then
        warning "MACOS_IDENTITY not set, skipping code signing"
        return 0
    fi
    
    log "Signing macOS binary..."
    
    local binary_path="$BUILD_DIR/logwhisperer.bin"
    
    if [[ ! -f "$binary_path" ]]; then
        error "Binary not found for signing: $binary_path"
        return 1
    fi
    
    # Sign the binary
    if codesign --deep --force --verify --verbose \
        --sign "$MACOS_IDENTITY" \
        --options runtime \
        --timestamp \
        "$binary_path"; then
        log "Binary signed successfully"
        
        # Verify signature
        if codesign --verify --verbose "$binary_path"; then
            log "Signature verification passed"
        else
            error "Signature verification failed"
            return 1
        fi
    else
        error "Failed to sign binary"
        return 1
    fi
    
    return 0
}

# Create default configuration file
create_default_config() {
    cat > "$1" << 'EOF'
# LogWhisperer Configuration
# AI-powered log analysis and monitoring

# Model configuration
model: "mistral"
ollama_host: "http://localhost:11434"

# Default log source
source: "journalctl"
log_file_path: "/var/log/syslog"
priority: "err"
entries: 500

# Processing options
lines_per_prompt: 50
timeout: 60

# Monitor configuration (Pro only)
monitor:
  enabled: true
  escalation_level: "ERROR"
  batch_size: 200
  sleep_interval: 1.0
  batch_timeout: 30.0
  webhook_url: ""  # Discord webhook URL
  send_full_summary: false
  discord_mentions: []  # List of user/role IDs to mention

# Web dashboard configuration (Pro only)
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

# Custom prompt (optional)
# Use {{LOGS}} as placeholder for log entries
# prompt: |
#   Analyze these logs and provide a concise summary:
#   {{LOGS}}

# Report directory
report_dir: "reports"
EOF
}

# Prepare web components
prepare_web_components() {
    log "Preparing web components..."
    
    local web_dir="$RELEASE_DIR/web"
    mkdir -p "$web_dir"
    
    # Copy API server
    if [[ -f "${SCRIPT_DIR}/api_server.py" ]]; then
        cp "${SCRIPT_DIR}/api_server.py" "$web_dir/"
        info "Copied API server"
    else
        warning "api_server.py not found"
    fi
    
    # Copy web dashboard
    if [[ -f "${SCRIPT_DIR}/web/dashboard.py" ]]; then
        cp "${SCRIPT_DIR}/web/dashboard.py" "$web_dir/"
    elif [[ -f "${SCRIPT_DIR}/dashboard.py" ]]; then
        cp "${SCRIPT_DIR}/dashboard.py" "$web_dir/"
    else
        warning "dashboard.py not found"
    fi
    
    # Copy templates
    if [[ -d "${SCRIPT_DIR}/templates" ]]; then
        cp -r "${SCRIPT_DIR}/templates" "$web_dir/"
        info "Copied templates directory"
    fi
    
    # Copy static files
    if [[ -d "${SCRIPT_DIR}/static" ]]; then
        cp -r "${SCRIPT_DIR}/static" "$web_dir/"
        info "Copied static directory"
    fi
    
    # Create web requirements file
    cat > "$web_dir/requirements-web.txt" << 'EOF'
flask>=2.0.0
flask-socketio>=5.0.0
flask-compress>=1.10.0
python-socketio[client]>=5.0.0
werkzeug>=2.0.0
pyyaml>=5.4.0
requests>=2.25.0
EOF
    
    # Copy systemd service files
    mkdir -p "$RELEASE_DIR/systemd"
    
    # Main service
    cat > "$RELEASE_DIR/systemd/logwhisperer.service" << 'EOF'
[Unit]
Description=LogWhisperer - AI-powered log intelligence
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/logwhisperer
ExecStart=/opt/logwhisperer/logwhisperer monitor
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=logwhisperer

[Install]
WantedBy=multi-user.target
EOF

    # API service
    cat > "$RELEASE_DIR/systemd/logwhisperer-api.service" << 'EOF'
[Unit]
Description=LogWhisperer API Server
After=network.target
Before=logwhisperer-web.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/logwhisperer
ExecStart=/opt/logwhisperer/bin/logwhisperer-api
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=logwhisperer-api
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
EOF

    # Web service
    cat > "$RELEASE_DIR/systemd/logwhisperer-web.service" << 'EOF'
[Unit]
Description=LogWhisperer Web Dashboard
After=network.target logwhisperer-api.service
Wants=logwhisperer-api.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/logwhisperer/web
ExecStart=/opt/logwhisperer/bin/logwhisperer-web
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=logwhisperer-web
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
EOF

    info "Created systemd service files"
}

# Prepare release package
prepare_release() {
    local version="$1"
    
    log "Preparing release package..."
    
    # Create release directory structure
    mkdir -p "$RELEASE_DIR"/{bin,config,docs,modules}
    
    # Copy binary
    cp "$BUILD_DIR/logwhisperer.bin" "$RELEASE_DIR/bin/logwhisperer"
    chmod +x "$RELEASE_DIR/bin/logwhisperer"
    
    # Handle configuration file
    if [[ -f "${SCRIPT_DIR}/config.yaml.example" ]]; then
        cp "${SCRIPT_DIR}/config.yaml.example" "$RELEASE_DIR/config/config.yaml.example"
        info "Copied existing config.yaml.example"
    else
        warning "config.yaml.example not found, creating default configuration"
        create_default_config "$RELEASE_DIR/config/config.yaml.example"
    fi
    
    # Copy installer if it exists
    if [[ -f "${SCRIPT_DIR}/install.sh" ]]; then
        cp "${SCRIPT_DIR}/install.sh" "$RELEASE_DIR/"
        chmod +x "$RELEASE_DIR/install.sh"
    else
        warning "install.sh not found, creating basic installer"
        cat > "$RELEASE_DIR/install.sh" << 'EOF'
#!/bin/bash
# LogWhisperer Installer

set -euo pipefail

# See full install.sh for complete installer
echo "Please use the full installer from the LogWhisperer repository"
EOF
        chmod +x "$RELEASE_DIR/install.sh"
    fi
    
    # Copy web installer
    if [[ -f "${SCRIPT_DIR}/install-web.sh" ]]; then
        cp "${SCRIPT_DIR}/install-web.sh" "$RELEASE_DIR/"
        chmod +x "$RELEASE_DIR/install-web.sh"
        info "Copied web installer"
    fi
    
    # Copy Python modules directory (needed for web components)
    if [[ -d "${SCRIPT_DIR}/modules" ]]; then
        cp -r "${SCRIPT_DIR}/modules" "$RELEASE_DIR/"
        info "Copied modules directory for web components"
    fi
    
    # Prepare web components
    prepare_web_components
    
    # Copy activate script if it exists
    if [[ -f "${SCRIPT_DIR}/activate.sh" ]]; then
        cp "${SCRIPT_DIR}/activate.sh" "$RELEASE_DIR/"
        chmod +x "$RELEASE_DIR/activate.sh"
        info "Included activate.sh script"
    fi
    
    # Create comprehensive README
    cat > "$RELEASE_DIR/README.md" << EOF
# LogWhisperer Pro v${version}

AI-powered log analysis and monitoring tool with web dashboard.

## Installation

### Basic Installation
\`\`\`bash
sudo ./install.sh
\`\`\`

### Web Dashboard Installation
\`\`\`bash
sudo ./install-web.sh
\`\`\`

## Activation

Activate your license:
\`\`\`bash
logwhisperer activate
\`\`\`

## Platform

- Platform: ${PLATFORM}
- Architecture: ${ARCH}
- Build Date: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
- Build Type: ${BUILD_TYPE}

## Quick Start

1. Activate your license:
   \`\`\`bash
   logwhisperer activate
   \`\`\`

2. Edit the configuration:
   \`\`\`bash
   sudo nano /etc/logwhisperer/config.yaml
   \`\`\`

3. Test the installation:
   \`\`\`bash
   logwhisperer test
   \`\`\`

4. Run LogWhisperer:
   \`\`\`bash
   logwhisperer summarize  # One-time summary
   logwhisperer monitor    # Real-time monitoring (Pro)
   \`\`\`

5. Access Web Dashboard (optional):
   - Install: \`sudo ./install-web.sh\`
   - Access: http://your-server:5123
   - Default login: admin / changeme

## Features

### Core Features
- AI-powered log summarization
- Multiple log sources (journalctl, file, docker)
- Configurable Ollama models

### Pro Features
- Real-time monitoring with alerts
- Discord webhook integration
- Web dashboard with analytics
- Advanced pattern detection
- Performance optimization

## Service Management

\`\`\`bash
# Main service
sudo systemctl status logwhisperer
sudo journalctl -u logwhisperer -f

# API service (for web dashboard)
sudo systemctl status logwhisperer-api

# Web service
sudo systemctl status logwhisperer-web
\`\`\`

For support, visit: https://github.com/binary-knight/logwhisperer
EOF
    
    # Create version file
    echo "$version" > "$RELEASE_DIR/VERSION"
    
    # Create manifest
    cat > "$RELEASE_DIR/MANIFEST.txt" << EOF
LogWhisperer Release Manifest
Version: ${version}
Platform: ${PLATFORM}
Architecture: ${ARCH}
Build Date: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
Build Type: ${BUILD_TYPE}

Files:
$(cd "$RELEASE_DIR" && find . -type f | sort)

Checksums:
$(cd "$RELEASE_DIR" && find . -type f -exec sha256sum {} \; | sort)
EOF
}

# Create distribution archives
create_archives() {
    local version="$1"
    local base_name="logwhisperer_${version}_${PLATFORM}_${ARCH}"
    
    log "Creating distribution archives..."
    
    mkdir -p "$DIST_DIR"
    cd "$RELEASE_DIR"
    
    # Create ZIP archive
    local zip_file="${DIST_DIR}/${base_name}.zip"
    zip -r "$zip_file" ./* -x "*.pyc" -x "__pycache__/*"
    info "Created: $zip_file"
    
    # Create tar.gz archive
    local tar_file="${DIST_DIR}/${base_name}.tar.gz"
    tar czf "$tar_file" --exclude="*.pyc" --exclude="__pycache__" ./*
    info "Created: $tar_file"
    
    cd "$SCRIPT_DIR"
    
    # Create checksums
    if [[ "$CREATE_CHECKSUM" == "true" ]]; then
        log "Creating checksums..."
        cd "$DIST_DIR"
        
        for file in *.{zip,tar.gz}; do
            if [[ -f "$file" ]]; then
                # Use platform-specific commands
                if [[ "$PLATFORM" == "macos" ]]; then
                    shasum -a 256 "$file" > "${file}.sha256"
                    md5 -r "$file" > "${file}.md5"
                else
                    sha256sum "$file" > "${file}.sha256"
                    md5sum "$file" > "${file}.md5"
                fi
                info "Created checksums for: $file"
            fi
        done
        
        cd "$SCRIPT_DIR"
    fi
    
    # Sign releases
    if [[ "$SIGN_RELEASE" == "true" ]] && [[ -n "$GPG_KEY" ]]; then
        log "Signing releases..."
        cd "$DIST_DIR"
        
        for file in *.{zip,tar.gz}; do
            if [[ -f "$file" ]]; then
                gpg --armor --detach-sign --default-key "$GPG_KEY" "$file"
                info "Signed: $file"
            fi
        done
        
        cd "$SCRIPT_DIR"
    fi
}

# Print build summary
print_summary() {
    local version="$1"
    local end_time=$(date +%s)
    local duration=$((end_time - START_TIME))
    
    echo
    log "Build completed successfully!"
    echo
    echo "Summary:"
    echo "  Version:      ${version}"
    echo "  Platform:     ${PLATFORM}"
    echo "  Architecture: ${ARCH}"
    echo "  Build Type:   ${BUILD_TYPE}"
    echo "  Duration:     ${duration} seconds"
    echo
    echo "Components included:"
    echo "  ✓ Main binary (compiled with Nuitka)"
    echo "  ✓ Web dashboard components"
    echo "  ✓ API server"
    echo "  ✓ Systemd service files"
    echo "  ✓ Installation scripts"
    echo
    echo "Artifacts:"
    ls -la "$DIST_DIR"
    echo
    
    # Show next steps
    echo "Next steps:"
    echo "  1. Test the binary: ${RELEASE_DIR}/bin/logwhisperer --version"
    echo "  2. Install locally: sudo ${RELEASE_DIR}/install.sh"
    echo "  3. Install web dashboard: sudo ${RELEASE_DIR}/install-web.sh"
    echo "  4. Upload release: ${DIST_DIR}/logwhisperer_${version}_${PLATFORM}_${ARCH}.zip"
}

# Main build process
main() {
    local START_TIME=$(date +%s)
    
    log "$SCRIPT_NAME"
    log "Starting build process..."
    
    # Detect version
    VERSION=$(detect_version)
    log "Building LogWhisperer ${VERSION}"
    
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --debug)
                BUILD_TYPE="debug"
                shift
                ;;
            --skip-tests)
                SKIP_TESTS="true"
                shift
                ;;
            --skip-cleanup)
                SKIP_CLEANUP="true"
                shift
                ;;
            --no-compress)
                COMPRESS_BINARY="false"
                shift
                ;;
            --sign)
                SIGN_RELEASE="true"
                shift
                ;;
            --gpg-key)
                GPG_KEY="$2"
                shift 2
                ;;
            --help)
                cat << EOF
Usage: $0 [OPTIONS]

Options:
  --debug           Build debug version
  --skip-tests      Skip running tests
  --skip-cleanup    Skip cleaning build artifacts
  --no-compress     Disable binary compression
  --sign           Sign release files
  --gpg-key KEY    GPG key for signing
  --help           Show this help message

Environment variables:
  PYTHON_CMD       Python command (default: python3)
  BUILD_TYPE       Build type: release or debug (default: release)
  SKIP_TESTS       Skip tests: true or false (default: false)
  COMPRESS_BINARY  Compress binary: true or false (default: true)
EOF
                exit 0
                ;;
            *)
                die "Unknown option: $1"
                ;;
        esac
    done
    
    # Build steps
    check_requirements
    
    if [[ "$SKIP_CLEANUP" != "true" ]]; then
        clean_artifacts
    fi
    
    run_tests
    build_binary

    if [[ "$PLATFORM" == "macos" ]] && [[ -n "${MACOS_IDENTITY:-}" ]]; then
        sign_macos_binary || warning "Binary signing failed, continuing anyway"
    fi

    prepare_release "$VERSION"
    create_archives "$VERSION"
    
    if [[ "$SKIP_CLEANUP" != "true" ]]; then
        log "Cleaning temporary build files..."
        rm -rf "$BUILD_DIR"
        rm -rf "${SCRIPT_DIR}/logwhisperer.dist"
        rm -rf "${SCRIPT_DIR}/logwhisperer.build"
        rm -rf "${SCRIPT_DIR}/logwhisperer.onefile-build"
    fi
    
    print_summary "$VERSION"
}

# Run main function
main "$@"