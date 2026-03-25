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

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== POLYORCHESTRA INSTALLER ===${NC}"
echo "Select your language / Choisissez votre langue :"
echo "1) English"
echo "2) Français"
read -p "[1/2] (Default: 1): " LANG_CHOICE

if [ "$LANG_CHOICE" = "2" ]; then
    L_Q_PRINTER="Quelle imprimante installez-vous ? (1, 2, 3, 4)"
    L_CHOICE="Votre choix"
    L_DL="Téléchargement dans"
else
    LANG_CHOICE="1"
    L_Q_PRINTER="Which printer are you installing? (1, 2, 3, 4)"
    L_CHOICE="Your choice"
    L_DL="Downloading to"
fi

echo -e "\n${YELLOW}$L_Q_PRINTER${NC}"
read -p "$L_CHOICE [1]: " PRINTER_CHOICE

INSTANCE=${PRINTER_CHOICE:-1}
DIR_NAME="polyorchestra-bridge-$INSTANCE"

echo -e "\n${GREEN}$L_DL /mnt/UDISK/$DIR_NAME...${NC}"
cd /mnt/UDISK
rm -rf $DIR_NAME
git clone -q https://github.com/aq-bros/polyorchestra-klipper-bridge.git $DIR_NAME
cd $DIR_NAME

sh install_sonicpad.sh "$INSTANCE" "$LANG_CHOICE"