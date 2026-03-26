#!/bin/sh

#
# PolyOrchestra™ Klipper Bridge
# Copyright (c) 2025-2026 AQ Bros. All rights reserved.
#
# "PolyOrchestra" is a registered trademark of AQ Bros.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== POLYORCHESTRA INSTALLER / UPDATER ===${NC}"
echo "Select your language / Choisissez votre langue :"
echo "1) English"
echo "2) Français"
read -p "[1/2] (Default: 1): " LANG_CHOICE </dev/tty

if [ "$LANG_CHOICE" = "2" ]; then
    L_Q_PRINTER="Quel port USB utilisez-vous / voulez-vous mettre à jour ? (Par défaut: 1)"
    L_CHOICE="Port USB"
    L_DL="Téléchargement dans"
else
    LANG_CHOICE="1"
    L_Q_PRINTER="Which USB port are you using / updating? (Default: 1)"
    L_CHOICE="USB Port"
    L_DL="Downloading to"
fi

echo -e "\n${YELLOW}$L_Q_PRINTER${NC}"
read -p "$L_CHOICE [1]: " PRINTER_CHOICE </dev/tty

INSTANCE=${PRINTER_CHOICE:-1}
DIR_NAME="polyorchestra-bridge-$INSTANCE"
CONFIG_PATH="/mnt/UDISK/$DIR_NAME/config.json"
BACKUP_PATH="/tmp/poly_config_backup_${INSTANCE}.json"

if [ -f "$CONFIG_PATH" ]; then
    echo -e "${GREEN}=== MISE A JOUR DETECTEE POUR LE PORT $INSTANCE ===${NC}"
    echo -e "${YELLOW}Sauvegarde de votre configuration...${NC}"
    cp "$CONFIG_PATH" "$BACKUP_PATH"
else
    echo -e "${GREEN}=== NOUVELLE INSTALLATION POUR LE PORT $INSTANCE ===${NC}"
fi

echo -e "\n${GREEN}$L_DL /mnt/UDISK/$DIR_NAME...${NC}"
cd /mnt/UDISK
rm -rf $DIR_NAME
git clone -q https://github.com/aq-bros/polyorchestra-klipper-bridge.git $DIR_NAME
cd $DIR_NAME

if [ -f "$BACKUP_PATH" ]; then
    mv "$BACKUP_PATH" config.json
    rm -f "$BACKUP_PATH"
    echo -e "${GREEN}Configuration restaurée avec succès !${NC}"
fi

sh install_sonicpad.sh "$INSTANCE" "$LANG_CHOICE"