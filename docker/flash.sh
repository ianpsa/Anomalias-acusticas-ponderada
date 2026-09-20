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
case "${1:-flash}" in
  build) idf.py build ;;
  flash) idf.py -p "${FLASH_PORT}" -b 460800 flash ;;
  *) echo 'Use build ou flash.' >&2; exit 1 ;;
esac
