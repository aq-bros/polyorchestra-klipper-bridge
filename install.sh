#!/bin/bash

#
# Polyorchestra™ Klipper Bridge
# Copyright (c) 2025-2026 AQ Bros. All rights reserved.
#
# "Polyorchestra" is a registered trademark of AQ Bros.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SERVICE_NAME="polyorchestra"
PYTHON_EXEC="/usr/bin/python3"

REAL_USER=${SUDO_USER:-$USER}
USER_HOME=$(eval echo ~$REAL_USER)

LOG_DIR="$USER_HOME/printer_data/logs"
LOG_FILE="$LOG_DIR/polyorchestra.log"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}=== POLYORCHESTRA™ AUTOMATIC INSTALLATION ===${NC}"

echo -e "${GREEN}[1/5] Installing dependencies...${NC}"
sudo apt-get update -q
sudo apt-get install -y python3-pip python3-requests python3-websocket
pip3 install requests websocket-client --break-system-packages 2>/dev/null || pip3 install requests websocket-client

echo -e "${GREEN}[2/5] Generating polyorchestra.cfg file...${NC}"
KLIPPER_CONF_DIR=""
if [ -d "$USER_HOME/printer_data/config" ]; then
    KLIPPER_CONF_DIR="$USER_HOME/printer_data/config"
elif [ -d "$USER_HOME/klipper_config" ]; then
    KLIPPER_CONF_DIR="$USER_HOME/klipper_config"
fi

if [ ! -z "$KLIPPER_CONF_DIR" ]; then
    POLY_CFG_PATH="$KLIPPER_CONF_DIR/polyorchestra.cfg"

    cat > "$POLY_CFG_PATH" << 'EOF'
# [gcode_macro G28]
# rename_existing: G0028
# gcode:
#     SET_DISPLAY_TEXT MSG="G28"
#     G0028 {rawparams}
#     SET_DISPLAY_TEXT

# [gcode_macro BED_MESH_CALIBRATE]
# rename_existing: BED_MESH_CALIBRATE_BASE
# gcode:
#     SET_DISPLAY_TEXT MSG="BED_MESH_CALIBRATE"
#     BED_MESH_CALIBRATE_BASE {rawparams}
#     SET_DISPLAY_TEXT

# [gcode_macro QUAD_GANTRY_LEVEL]
# rename_existing: QUAD_GANTRY_LEVEL_BASE
# gcode:
#     SET_DISPLAY_TEXT MSG="QUAD_GANTRY_LEVEL"
#     QUAD_GANTRY_LEVEL_BASE {rawparams}
#     SET_DISPLAY_TEXT

# [gcode_macro Z_TILT_ADJUST]
# rename_existing: Z_TILT_ADJUST_BASE
# gcode:
#     SET_DISPLAY_TEXT MSG="Z_TILT_ADJUST"
#     Z_TILT_ADJUST_BASE {rawparams}
#     SET_DISPLAY_TEXT

# [gcode_macro LOAD_FILAMENT]
# gcode:
#     SET_DISPLAY_TEXT MSG="LOAD_FILAMENT"
#     {% set speed = params.SPEED|default(300) %}
#     {% set max_velocity = printer.configfile.settings['extruder'].max_extrude_only_velocity | default(50) %}
#     SAVE_GCODE_STATE NAME=load_state
#     M300
#     G91
#     G92 E0
#     G1 E50 F{max_velocity}
#     G1 E25 F{speed}
#     M300
#     RESTORE_GCODE_STATE NAME=load_state
#     SET_DISPLAY_TEXT

# [gcode_macro UNLOAD_FILAMENT]
# gcode:
#     SET_DISPLAY_TEXT MSG="UNLOAD_FILAMENT"
#     {% set speed = params.SPEED|default(300) %}
#     {% set max_velocity = printer.configfile.settings['extruder'].max_extrude_only_velocity | default(50) %}
#     SAVE_GCODE_STATE NAME=unload_state
#     G91
#     M300
#     G92 E0
#     G1 E25 F{speed}
#     G1 E-75 F{max_velocity}
#     M300
#     RESTORE_GCODE_STATE NAME=unload_state
#     SET_DISPLAY_TEXT

# [gcode_macro M600]
# gcode:
#     SET_DISPLAY_TEXT MSG="M600"
#     PAUSE
EOF

    chown $REAL_USER:$REAL_USER "$POLY_CFG_PATH"
fi

echo -e "${GREEN}[3/5] Adding to Moonraker Update Manager...${NC}"
MOONRAKER_CONF="$KLIPPER_CONF_DIR/moonraker.conf"

if [ -f "$MOONRAKER_CONF" ]; then
    if ! grep -q "\[update_manager polyorchestra\]" "$MOONRAKER_CONF"; then
        cat >> "$MOONRAKER_CONF" << EOF

[update_manager polyorchestra]
type: git_repo
path: $PROJECT_DIR
origin: https://github.com/aq-bros/polyorchestra-klipper-bridge.git
primary_branch: main
install_script: install.sh
is_system_service: True
managed_services: polyorchestra
EOF
        echo -e "${YELLOW}Moonraker updated. Restarting...${NC}"
        sudo systemctl restart moonraker
    fi
fi

echo -e "${GREEN}[4/5] Configuring Log Rotation...${NC}"
LOGROTATE_FILE="/etc/logrotate.d/${SERVICE_NAME}"
sudo bash -c "cat > $LOGROTATE_FILE" << EOF
$LOG_FILE {
    daily
    rotate 7
    maxsize 10M
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
    create 644 $REAL_USER $REAL_USER
}
EOF

echo -e "${GREEN}Authorizing service in Moonraker...${NC}"
ASVC_FILE="$USER_HOME/printer_data/moonraker.asvc"
if [ -f "$ASVC_FILE" ]; then
    if ! grep -q "^polyorchestra$" "$ASVC_FILE"; then
        echo "polyorchestra" >> "$ASVC_FILE"
    fi
fi

echo -e "${GREEN}[5/5] Starting service...${NC}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOG_CONFIG=""
if [ -d "$LOG_DIR" ]; then
    [ ! -f "$LOG_FILE" ] && echo "" > "$LOG_FILE" && chown $REAL_USER:$REAL_USER "$LOG_FILE"
    LOG_CONFIG="StandardOutput=append:$LOG_FILE"
fi

sudo bash -c "cat > $SERVICE_FILE" << EOF
[Unit]
Description=Polyorchestra™ Bridge
After=network-online.target moonraker.service

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_EXEC -u $PROJECT_DIR/main.py
Restart=always
RestartSec=10
$LOG_CONFIG
StandardError=inherit

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME
sudo systemctl restart $SERVICE_NAME

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}POLYORCHESTRA™ BRIDGE INSTALLATION COMPLETE !${NC}"
echo -e "${BLUE}====================================================${NC}"
