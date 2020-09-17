import logging

import requests
import pandas as pd
import typing
from bs4 import BeautifulSoup

from configs.common import (
    download_file,
    prepare_image_copy,
    mount,
    save_file,
    set_root_password,
)

logger = logging.getLogger(__name__)

ARCH = "x86_64"
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

    g = mount(working_image)

    set_root_password(g, "password")

    infra = g.read_file("/etc/dnf/vars/infra").decode().strip()
    # Get the mirrorlist
    mirrorlist = (
        requests.get(
            f"http://mirrorlist.centos.org/?release={REL}&arch={ARCH}&repo=AppStream&infra={infra}"
        )
        .content.decode()
        .splitlines()
    )

    # Manually download packages
    package_listing = BeautifulSoup(
        requests.get(f"{mirrorlist[0]}Packages").content.decode(), features="lxml",
    )

    required_packages = ("hypervkvpd", "hyperv-daemons-license")
    package_links = [
        f"{mirrorlist[0]}Packages/{p['href']}"
        for p in filter(
            lambda elem: any(
                elem["href"].startswith(f"{r}-") for r in required_packages
            ),
            package_listing.find_all("a"),
        )
    ]

    for link in package_links:
        save_file(link, link.split("/")[-1])

    for p in [url.split("/")[-1] for url in package_links]:
        g.copy_in(p, "/tmp")

    g.command(["rpm", "-i", "/tmp/*.rpm"])

    return working_image
    # return g
