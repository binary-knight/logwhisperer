#!/bin/bash
set -e

# LogWhisperer Docker Entrypoint Script

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*"
}

error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $*"
}

# Function to wait for Ollama to be ready
wait_for_ollama() {
    local max_attempts=30
    local attempt=0
    
    log "Waiting for Ollama to start..."
    
    while [ $attempt -lt $max_attempts ]; do
        if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
            log "Ollama is ready"
            return 0
        fi
        
        attempt=$((attempt + 1))
        sleep 2
    done
    
    error "Ollama failed to start after $max_attempts attempts"
    return 1
}

# Function to pull required models
pull_models() {
    local model="${OLLAMA_MODEL:-mistral}"
    
    log "Checking for model: $model"
    
    if ! ollama list | grep -q "^$model"; then
        log "Pulling model: $model"
        ollama pull "$model" || {
            error "Failed to pull model: $model"
            return 1
        }
    else
        log "Model $model is already available"
    fi
    
    # Pull backup model if different
    if [ "$model" != "phi" ]; then
        if ! ollama list | grep -q "^phi"; then
            log "Pulling backup model: phi"
            ollama pull phi || warning "Failed to pull backup model phi"
        fi
    fi
}

# Setup configuration
setup_config() {
    # If no config exists, copy the example
    if [ ! -f "$LOGWHISPERER_CONFIG" ]; then
        if [ -f "/etc/logwhisperer/config.yaml.example" ]; then
            log "Creating config from example..."
            cp /etc/logwhisperer/config.yaml.example "$LOGWHISPERER_CONFIG"
        else
            error "No configuration file found"
            return 1
        fi
    fi
    
    # Update config with environment variables if set
    if [ -n "$DISCORD_WEBHOOK_URL" ]; then
        log "Setting Discord webhook URL from environment"
        # Use a temp file to avoid issues with sed -i in Docker
        sed "s|webhook_url:.*|webhook_url: $DISCORD_WEBHOOK_URL|g" "$LOGWHISPERER_CONFIG" > /tmp/config.yaml
        mv /tmp/config.yaml "$LOGWHISPERER_CONFIG"
    fi
    
    if [ -n "$OLLAMA_HOST" ] && [ "$OLLAMA_HOST" != "http://localhost:11434" ]; then
        log "Setting Ollama host: $OLLAMA_HOST"
        sed "s|ollama_host:.*|ollama_host: $OLLAMA_HOST|g" "$LOGWHISPERER_CONFIG" > /tmp/config.yaml
        mv /tmp/config.yaml "$LOGWHISPERER_CONFIG"
    fi
}

# Main entrypoint logic
main() {
    log "Starting LogWhisperer container..."
    
    # Setup configuration
    setup_config || exit 1
    
    # Check if we need to run Ollama locally
    if [ "$OLLAMA_HOST" = "http://localhost:11434" ] || [ -z "$OLLAMA_HOST" ]; then
        log "Starting local Ollama server..."
        
        # Start Ollama in the background
        ollama serve >/var/log/logwhisperer/ollama.log 2>&1 &
        OLLAMA_PID=$!
        
        # Wait for Ollama to be ready
        if wait_for_ollama; then
            # Pull required models
            pull_models || warning "Some models failed to download"
        else
            error "Ollama startup failed"
            exit 1
        fi
        
        # Ensure Ollama is stopped when container stops
        trap "kill $OLLAMA_PID 2>/dev/null" EXIT TERM INT
    else
        log "Using external Ollama server: $OLLAMA_HOST"
    fi
    
    # Handle special commands
    case "${1:-}" in
        bash|sh)
            exec "$@"
            ;;
        test)
            log "Running LogWhisperer tests..."
            exec logwhisperer test
            ;;
        *)
            # Run LogWhisperer with all arguments
            log "Starting LogWhisperer: $*"
            exec logwhisperer "$@"
            ;;
    esac
}

# Run main function
main "$@"