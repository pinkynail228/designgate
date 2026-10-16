#!/bin/sh
# Start the Yandex Responses adapter for DeepSeek workers on 127.0.0.1:${ADAPTER_PORT:-8798}.
# Secrets come from an env file outside the repo (default: the owner's Yandex env file); nothing is printed.
set -eu
env_file=${YANDEX_ENV_FILE:-$HOME/.config/usergate-survivors/yandex.env}
set -a; . "$env_file"; set +a
: "${YANDEX_API_KEY:?missing in the env file}"; : "${YANDEX_FOLDER_ID:?missing in the env file}"
export ADAPTER_PORT=${ADAPTER_PORT:-8798} ADAPTER_LOG=${ADAPTER_LOG:-/tmp/zs-designgate/yandex-adapter.log}
mkdir -p /tmp/zs-designgate
exec python3 "$(dirname "$0")/yandex_adapter.py"
