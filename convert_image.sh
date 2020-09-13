#!/usr/bin/env bash

set -x

IMAGE_NAME=$(ls | grep -E "^[0-9]{8}.*?\.img" | tail -n 1)
NEW_IMAGE_NAME=$(basename -s .img "$IMAGE_NAME").vhd
qemu-img resize "$IMAGE_NAME" +18G
qemu-img convert -f qcow2 -O vpc "$IMAGE_NAME" "$NEW_IMAGE_NAME"
