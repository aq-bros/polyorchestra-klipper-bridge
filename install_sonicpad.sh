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
PYTHON_EXEC="/usr/bin/python3"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

INSTANCE=$1
LANG_CHOICE=$2

SUFFIX=$INSTANCE
[ "$INSTANCE" = "1" ] && SUFFIX=""

DATA_DIR="/mnt/UDISK"
CONFIG_DIR="$DATA_DIR/printer_config${SUFFIX}"
LOG_DIR="$DATA_DIR/printer_log${SUFFIX}"
LOG_FILE="$LOG_DIR/polyorchestra.log"
MOONRAKER_CONF="$CONFIG_DIR/moonraker.conf"

if [ "$LANG_CHOICE" = "2" ]; then
    T_TITLE="=== INSTALLATION POLYORCHESTRA™ SONIC PAD ==="
    T_STEP1="[1/5] Installation des dépendances via pip..."
    T_STEP2="[2/5] Génération du fichier polyorchestra.cfg..."
    T_STEP3="[3/5] Ajout au gestionnaire de mise à jour Moonraker..."
    T_STEP4="[4/5] Détection automatique du port..."
    T_STEP5="[5/5] Démarrage du service"
    T_DONE="INSTALLATION TERMINÉE POUR L'INSTANCE : $INSTANCE"
else
    T_TITLE="=== POLYORCHESTRA™ SONIC PAD INSTALLATION ==="
    T_STEP1="[1/5] Installing dependencies via pip..."
    T_STEP2="[2/5] Generating polyorchestra.cfg file..."
    T_STEP3="[3/5] Adding to Moonraker Update Manager..."
    T_STEP4="[4/5] Automatic port detection..."
    T_STEP5="[5/5] Starting service"
    T_DONE="INSTALLATION COMPLETE FOR INSTANCE: $INSTANCE"
fi

echo -e "${BLUE}$T_TITLE${NC}"

echo -e "\n${GREEN}$T_STEP1${NC}"
pip3 install requests websocket-client --disable-pip-version-check 2>/dev/null || true

echo -e "${GREEN}$T_STEP2${NC}"
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
fi

echo -e "${GREEN}$T_STEP3${NC}"
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
        /etc/init.d/S56moonraker restart 2>/dev/null || true
    fi
fi

echo -e "${GREEN}$T_STEP4${NC}"
PORT="7125"
if [ -f "$MOONRAKER_CONF" ]; then
    DETECTED_PORT=$(grep -E "^port\s*:" "$MOONRAKER_CONF" | awk -F ':' '{print $2}' | tr -d '[:space:]')
    [ ! -z "$DETECTED_PORT" ] && PORT=$DETECTED_PORT
fi
echo -e "${YELLOW}Port: $PORT${NC}"

CONFIG_JSON="$PROJECT_DIR/config.json"
cat > "$CONFIG_JSON" << EOF
{
    "moonraker_host": "127.0.0.1",
    "moonraker_port": $PORT
}
EOF

SERVICE_NAME="polyorchestra${SUFFIX}"
INIT_FILE="/etc/init.d/$SERVICE_NAME"
mkdir -p "$LOG_DIR" 2>/dev/null

cat > "$INIT_FILE" << EOF
#!/bin/sh /etc/rc.common
START=99
STOP=10
start() {
    [ ! -f "$LOG_FILE" ] && touch "$LOG_FILE"
    nohup $PYTHON_EXEC -u $PROJECT_DIR/main.py >> $LOG_FILE 2>&1 &
    echo \$! > /var/run/${SERVICE_NAME}.pid
}
stop() {
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

echo -e "${GREEN}$T_STEP5 ($SERVICE_NAME)...${NC}"
/etc/init.d/$SERVICE_NAME enable 2>/dev/null || true
/etc/init.d/$SERVICE_NAME restart

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}$T_DONE${NC}"
echo -e "${BLUE}====================================================${NC}"