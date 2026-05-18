#!/bin/bash
# VPS Auto-Setup Script for Coin Screener Pro
# Usage: curl -fsSL https://raw.githubusercontent.com/Unknows05/Coin-screener-1.0/main/scripts/setup-vps.sh | bash

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
REPO_URL="https://github.com/Unknows05/Coin-screener-1.0.git"
INSTALL_DIR="/opt/coin-screener"
SERVICE_USER="screener"
PORT=8000

print_status() {
    echo -e "${BLUE}[*]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

# Check if root
check_root() {
    if [ "$EUID" -ne 0 ]; then 
        print_error "Please run as root (use sudo)"
        exit 1
    fi
}

# Detect OS
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VERSION=$VERSION_ID
    else
        print_error "Cannot detect OS"
        exit 1
    fi
    
    print_status "Detected OS: $OS $VERSION"
}

# Install dependencies
install_deps() {
    print_status "Installing dependencies..."
    
    case $OS in
        ubuntu|debian)
            apt-get update -qq
            apt-get install -y -qq python3 python3-pip python3-venv git curl sqlite3 nginx ufw fail2ban
            ;;
        centos|rhel|fedora)
            yum update -y -q
            yum install -y -q python3 python3-pip git curl sqlite3 nginx firewalld
            # For CentOS 8+/RHEL 8+
            if command -v dnf &> /dev/null; then
                dnf install -y -q python3 python3-pip git curl sqlite3 nginx firewalld
            fi
            ;;
        *)
            print_error "Unsupported OS: $OS"
            exit 1
            ;;
    esac
    
    print_success "Dependencies installed"
}

# Create service user
create_user() {
    print_status "Creating service user: $SERVICE_USER"
    
    if id "$SERVICE_USER" &>/dev/null; then
        print_warning "User $SERVICE_USER already exists"
    else
        useradd -m -s /bin/bash -d "$INSTALL_DIR" "$SERVICE_USER"
        print_success "User $SERVICE_USER created"
    fi
}

# Setup application
setup_app() {
    print_status "Setting up application..."
    
    # Clone repository
    if [ -d "$INSTALL_DIR/.git" ]; then
        print_warning "Repository already exists, pulling updates..."
        su - "$SERVICE_USER" -c "cd $INSTALL_DIR && git pull origin main"
    else
        rm -rf "$INSTALL_DIR"
        su - "$SERVICE_USER" -c "git clone $REPO_URL $INSTALL_DIR"
    fi
    
    # Setup virtual environment
    print_status "Setting up Python virtual environment..."
    su - "$SERVICE_USER" -c "cd $INSTALL_DIR && python3 -m venv venv"
    su - "$SERVICE_USER" -c "cd $INSTALL_DIR && source venv/bin/activate && pip install -q --upgrade pip"
    su - "$SERVICE_USER" -c "cd $INSTALL_DIR && source venv/bin/activate && pip install -q -r requirements.txt"
    
    # Create data directory
    su - "$SERVICE_USER" -c "mkdir -p $INSTALL_DIR/data"
    
    print_success "Application setup complete"
}

# Setup systemd service
setup_systemd() {
    print_status "Setting up systemd service..."
    
    cat > /etc/systemd/system/coin-screener.service << EOF
[Unit]
Description=Coin Screener Pro API
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/venv/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=$INSTALL_DIR/venv/bin/python -u api.py
Restart=always
RestartSec=10
StandardOutput=append:$INSTALL_DIR/data/api.log
StandardError=append:$INSTALL_DIR/data/api.log

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable coin-screener
    
    print_success "Systemd service created"
}

# Setup firewall
setup_firewall() {
    print_status "Configuring firewall..."
    
    case $OS in
        ubuntu|debian)
            ufw default deny incoming
            ufw default allow outgoing
            ufw allow 22/tcp comment 'SSH'
            ufw allow $PORT/tcp comment 'Coin Screener'
            ufw allow 80/tcp comment 'HTTP'
            ufw allow 443/tcp comment 'HTTPS'
            
            # Enable UFW non-interactively
            echo "y" | ufw enable
            ;;
        centos|rhel|fedora)
            systemctl start firewalld
            systemctl enable firewalld
            firewall-cmd --permanent --add-port=22/tcp
            firewall-cmd --permanent --add-port=$PORT/tcp
            firewall-cmd --permanent --add-port=80/tcp
            firewall-cmd --permanent --add-port=443/tcp
            firewall-cmd --reload
            ;;
    esac
    
    print_success "Firewall configured"
}

# Setup fail2ban
setup_fail2ban() {
    print_status "Setting up fail2ban..."
    
    cat > /etc/fail2ban/jail.local << EOF
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
EOF

    systemctl restart fail2ban
    systemctl enable fail2ban
    
    print_success "Fail2ban configured"
}

# Create swap (for low-memory VPS)
setup_swap() {
    local total_mem=$(free -m | awk '/^Mem:/{print $2}')
    
    if [ "$total_mem" -lt 2048 ]; then
        print_status "Creating swap file (2GB) for low-memory VPS..."
        
        if [ ! -f /swapfile ]; then
            fallocate -l 2G /swapfile
            chmod 600 /swapfile
            mkswap /swapfile
            swapon /swapfile
            echo '/swapfile none swap sw 0 0' >> /etc/fstab
            print_success "Swap file created"
        else
            print_warning "Swap file already exists"
        fi
    else
        print_status "Sufficient memory (${total_mem}MB), skipping swap creation"
    fi
}

# Setup auto-updates
setup_autoupdate() {
    print_status "Setting up automatic security updates..."
    
    case $OS in
        ubuntu|debian)
            apt-get install -y -qq unattended-upgrades
            dpkg-reconfigure -plow unattended-upgrades
            ;;
        centos|rhel|fedora)
            # Create cron job for yum updates
            echo "0 3 * * * root yum update -y -q" > /etc/cron.d/auto-update
            ;;
    esac
    
    print_success "Auto-updates configured"
}

# Start service
start_service() {
    print_status "Starting Coin Screener service..."
    
    systemctl start coin-screener
    sleep 3
    
    # Check if running
    if systemctl is-active --quiet coin-screener; then
        print_success "Service started successfully!"
        
        # Health check
        sleep 3
        if curl -s http://localhost:$PORT/health > /dev/null 2>&1; then
            print_success "Health check passed!"
        else
            print_warning "Service started but health check failed (may need more time)"
        fi
    else
        print_error "Failed to start service"
        print_status "Check logs: journalctl -u coin-screener -n 50"
        exit 1
    fi
}

# Print final info
print_info() {
    local ip=$(curl -s ifconfig.me 2>/dev/null || echo "YOUR_VPS_IP")
    
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║       🎉 Coin Screener Pro - Setup Complete!               ║"
    echo "╠════════════════════════════════════════════════════════════╣"
    echo "║                                                            ║"
    echo "║  📊 Dashboard:  http://$ip:$PORT                    ║"
    echo "║  📚 API Docs:   http://$ip:$PORT/docs             ║"
    echo "║                                                            ║"
    echo "╠════════════════════════════════════════════════════════════╣"
    echo "║  Management Commands:                                      ║"
    echo "║    sudo systemctl status coin-screener                     ║"
    echo "║    sudo systemctl restart coin-screener                  ║"
    echo "║    sudo journalctl -u coin-screener -f                   ║"
    echo "║                                                            ║"
    echo "╠════════════════════════════════════════════════════════════╣"
    echo "║  View Logs:                                                ║"
    echo "║    sudo tail -f $INSTALL_DIR/data/api.log ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""
}

# Main
main() {
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║      Coin Screener Pro - VPS Auto-Setup                    ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""
    
    check_root
    detect_os
    install_deps
    create_user
    setup_app
    setup_systemd
    setup_firewall
    setup_fail2ban
    setup_swap
    setup_autoupdate
    start_service
    print_info
    
    print_status "Setup complete! Your Coin Screener is running."
    print_status "Visit http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_VPS_IP'):$PORT"
}

# Run main
main "$@"
