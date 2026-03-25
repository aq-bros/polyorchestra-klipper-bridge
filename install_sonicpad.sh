#!/bin/sh

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

PROJECT_DIR="$( cd "$( dirname "$0" )" && pwd )"
SERVICE_NAME="polyorchestra"
PYTHON_EXEC="/usr/bin/python3"

DATA_DIR="/usr/data/printer_data"
CONFIG_DIR="$DATA_DIR/config"
LOG_DIR="$DATA_DIR/logs"
LOG_FILE="$LOG_DIR/polyorchestra.log"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}=== POLYORCHESTRA™ SONIC PAD INSTALLATION ===${NC}"

echo -e "${GREEN}[1/5] Installing dependencies via pip...${NC}"
pip3 install requests websocket-client --disable-pip-version-check 2>/dev/null || true

echo -e "${GREEN}[2/5] Generating polyorchestra.cfg file...${NC}"
if [ -d "$CONFIG_DIR" ]; then
    POLY_CFG_PATH="$CONFIG_DIR/polyorchestra.cfg"

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
    echo -e "${YELLOW}File created at: $POLY_CFG_PATH${NC}"
else
    echo -e "${YELLOW}Warning: Config directory not found at $CONFIG_DIR${NC}"
fi

echo -e "${GREEN}[3/5] Adding to Moonraker Update Manager...${NC}"
MOONRAKER_CONF="$CONFIG_DIR/moonraker.conf"

if [ -f "$MOONRAKER_CONF" ]; then
    if ! grep -q "\[update_manager polyorchestra\]" "$MOONRAKER_CONF"; then
        cat >> "$MOONRAKER_CONF" << EOF

[update_manager polyorchestra]
type: git_repo
path: $PROJECT_DIR
origin: https://github.com/aq-bros/polyorchestra-klipper-bridge.git
primary_branch: main
install_script: install_sonicpad.sh
is_system_service: False
EOF
        echo -e "${YELLOW}Moonraker updated. Restarting Moonraker...${NC}"
        /etc/init.d/S56moonraker restart 2>/dev/null || true
    fi
fi

echo -e "${GREEN}[4/5] Creating init.d background service...${NC}"
INIT_FILE="/etc/init.d/$SERVICE_NAME"

cat > "$INIT_FILE" << EOF
#!/bin/sh /etc/rc.common

START=99
STOP=10

start() {
    echo "Starting Polyorchestra Bridge..."
    if [ ! -f "$LOG_FILE" ]; then
        touch "$LOG_FILE"
    fi
    # Lancement en arrière-plan
    nohup $PYTHON_EXEC -u $PROJECT_DIR/main.py >> $LOG_FILE 2>&1 &
    echo \$! > /var/run/${SERVICE_NAME}.pid
}

stop() {
    echo "Stopping Polyorchestra Bridge..."
    if [ -f /var/run/${SERVICE_NAME}.pid ]; then
        kill \$(cat /var/run/${SERVICE_NAME}.pid) 2>/dev/null
        rm /var/run/${SERVICE_NAME}.pid
    else
        killall -9 main.py 2>/dev/null
    fi
}

restart() {
    stop
    sleep 2
    start
}
EOF

chmod +x "$INIT_FILE"

echo -e "${GREEN}[5/5] Starting service...${NC}"
/etc/init.d/$SERVICE_NAME enable 2>/dev/null || true
/etc/init.d/$SERVICE_NAME restart

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}POLYORCHESTRA™ SONIC PAD INSTALLATION COMPLETE !${NC}"
echo -e "${BLUE}====================================================${NC}"