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

    # Manually download packages
    # Dependency resolving requires the fairly heavy libdnf + hawkey Python wrapper so manual resolution is required
    # XML repo data parsing vs html parsing is a slightly more reliable but more complicated option
    infra = g.read_file("/etc/dnf/vars/infra").decode().strip()
    required_packages = {
        "BaseOS": ["patch"],
        "AppStream": ["hypervkvpd", "hyperv-daemons-license"],
    }
    for repo, package_list in required_packages.items():
        # Get the mirrorlist
        logger.info("Getting mirrorlist for %s", repo)
        # TODO try additional mirrors if the first doesn't respond?
        mirrorlist = (
            requests.get(
                f"http://mirrorlist.centos.org/?release={REL}&arch={ARCH}&repo={repo}&infra={infra}"
            )
            .content.decode()
            .splitlines()
        )

        logger.info("Getting html package list for %s", repo)
        package_listing = BeautifulSoup(
            requests.get(f"{mirrorlist[0]}Packages").content.decode(), features="lxml",
        )

        logger.info("Searching for packages %s in listing", package_list)
        package_links = [
            f"{mirrorlist[0]}Packages/{p['href']}"
            for p in filter(
                lambda elem: any(
                    elem["href"].startswith(f"{r}-") for r in package_list
                ),
                package_listing.find_all("a"),
            )
        ]

        logger.info("Found links %s", package_links)

        for link in package_links:
            save_file(link, link.split("/")[-1])

        for p in [url.split("/")[-1] for url in package_links]:
            logger.info("Copying package %s into image", p)
            g.copy_in(p, "/tmp")

    logger.info(
        "Installing all rpms (%s) in /tmp",
        [f for f in g.find("/tmp") if f.endswith(".rpm")],
    )
    g.command(["rpm", "-i", "/tmp/*.rpm"])

    # TODO setup network/dhcp?

    setup_cloud_init(g)

    g.close()

    return working_image
    # return g
