#!/usr/bin/env bash

set -e

CONTAINER_TOOL=docker
IMAGE_NAME=disk-image-tools
[ -f "Dockerfile" ] && "$CONTAINER_TOOL" build -t "$IMAGE_NAME" .
"$CONTAINER_TOOL" run -it --rm -v "$(pwd)":/image:rw "$IMAGE_NAME"
