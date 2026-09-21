#!/usr/bin/env bash
set -euo pipefail
if [ ! -s /models/model_weights.h ]; then
  echo 'Treine pelo painel primeiro: models/model_weights.h ainda não existe.' >&2
  exit 1
fi
mkdir -p /tmp/ferris-firmware
cd /tmp/ferris-firmware
# Copy only sources: host builds and generated configuration are not portable.
tar -C /source --exclude=build --exclude=managed_components --exclude=data \
  --exclude=sdkconfig --exclude=sdkconfig.old --exclude=model_weights.h -cf - . | tar -xf -
cp /models/model_weights.h main/model_weights.h
# Environment-provided ESP32 setup (SSID/senha/bridge/token), so credentials
# never end up in a committed file. Created only when at least one is provided.
vars=''
for pair in FERRIS_WIFI_SSID FERRIS_WIFI_PASSWORD FERRIS_BRIDGE_URL FERRIS_DEVICE_TOKEN; do
  [ -n "${!pair:-}" ] && vars="$vars $pair"
done
if [ -n "$vars" ]; then
  for pair in $vars; do
    printf 'CONFIG_%s="%s"\n' "$pair" "${!pair}" >> sdkconfig
  done
fi
case "${1:-flash}" in
  build) idf.py build ;;
  flash) idf.py -p "${FLASH_PORT}" -b 460800 flash ;;
  *) echo 'Use build ou flash.' >&2; exit 1 ;;
esac
