import logging
import typing

import pandas as pd
import requests
from bs4 import BeautifulSoup

from configs.common import (
    download_file,
    prepare_image_copy,
    mount,
    save_file,
    set_root_password,
    setup_cloud_init,
    build_esp,
)

logger = logging.getLogger(__name__)

ARCH = "x86_64"
EFI_ARCH = "x64"
# Partition GUIDs https://systemd.io/DISCOVERABLE_PARTITIONS/
ROOTFS_GPT_ID = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"  # x86_64 rootfs
REL = "8"


def get_latest_url() -> typing.Tuple[str, str]:
    logger.info("Download image file list")
    page = requests.get(
        f"https://cloud.centos.org/centos/{REL}/{ARCH}/images/"
    ).content.decode()
    soup = BeautifulSoup(page, features="lxml")
    downloads = pd.read_html(str(soup.find_all("table")[0]))[0]
    latest = [
        item
        for item in downloads.sort_values("Last modified", ascending=False)[
            "Name"
        ].values
        if isinstance(item, str) and "-GenericCloud-" in item
    ][0]

    logger.info("Found latest image '%s'", latest)

    logger.info("Downloading checksums file")
    image_hash = [
        line.split(" = ")[1]
        for line in requests.get(
            f"https://cloud.centos.org/centos/{REL}/{ARCH}/images/CHECKSUM"
        )
        .content.decode()
        .splitlines()
        if line.startswith(f"SHA256 ({latest})")
    ][0]
    logger.info("Found image hash %s", image_hash)

    image_url = f"https://cloud.centos.org/centos/{REL}/{ARCH}/images/{latest}"
    return image_url, image_hash


def ensure_image_downloaded(image_url: str, image_hash: str) -> None:
    return download_file(image_url, image_hash)


def build() -> str:
    original_image = ensure_image_downloaded(*get_latest_url())[1]
    working_image = prepare_image_copy(original_image)

    logger.info("Rebuilding image with ESP")
    build_esp(working_image)

    g = mount(working_image)
    # Fix /etc/fstab after rebuilding partitions
    g.set_label("/dev/sda2", "ESP")
    g.write(
        "/etc/fstab",
        "\n".join(
            [
                "# Generated by disk-image-tools",
                "\t".join(
                    [
                        f"UUID={g.blkid('/dev/sda1')['UUID']}",
                        "/",
                        "xfs",
                        "defaults",
                        "0 0",
                    ]
                ),
                "\t".join(["LABEL=ESP", "/boot/efi", "vfat", "defaults", "0 0"]),
                "",
            ]
        ),
    )

    logger.warning("Setting root password to 'password'")
    # set_root_password(g, "password")
    g.command(["bash", "-c", "echo password | passwd --stdin root"])
    g.command(["passwd", "-u", "root"])

    g.command(["dnf", "install", "-y", "hypervkvpd", "patch"])

    # grub2 efi setup
    # https://fedoraproject.org/wiki/GRUB_2
    g.mount("/dev/sda2", "/boot/efi")
    g.part_set_gpt_type("/dev/sda", 1, ROOTFS_GPT_ID)
    g.command(
        [
            "dnf",
            "install",
            "-y",
            f"grub2-efi-{EFI_ARCH}",
            f"grub2-efi-{EFI_ARCH}-modules",
            f"shim",
        ]
    )
    # g.command(["grub2-mkconfig", "-o", "/boot/efi/EFI/centos/grub.cfg"])
    # grub isn't loading the full config since it only has access to the vfat partition
    # not sure what the correct way to do this is...
    g.write(
        "/boot/efi/EFI/centos/grub.cfg",
        """
    insmod xfs
    configfile (hd0,gpt1)/boot/grub2/grub.cfg
    """,
    )
    g.find("/boot/efi")

    # unsupported systemd-boot setup https://paste.centos.org/view/d1ce7921
    # https://www.freedesktop.org/wiki/Software/systemd/systemd-boot/
    # g.write_append("/etc/fstab", "\t".join(["LABEL=UEFI", "/efi", "vfat", "defaults", "0 0"]) + "\n")
    # g.command("dnf", "--disablerepo=*", "-y", "remove", "grubby", "grub2*", "shim*", "memtest86*"])
    # g.command(["systemd-machine-id-setup"])
    # g.command(["bootctl", "--path=/boot/efi", "install"])
    # https://www.freedesktop.org/software/systemd/man/kernel-install.html
    # generate boot entries by reinstalling kernel?
    # rm -rf /boot/grub2 /boot/loader
    # kernel-install add $(uname -r) /lib/modules/$(uname -r)/vmlinuz
    # dnf reinstall kernel-core -y

    # TODO setup network/dhcp?

    setup_cloud_init(g)

    g.close()

    return working_image
