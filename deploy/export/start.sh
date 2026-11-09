#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
sha256sum --strict --check SHA256SUMS >/dev/null
exec ./native/pslc-agent --config "$PWD/config.json" "$@"
