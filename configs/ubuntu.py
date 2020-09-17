import io
import logging
import typing

import pandas as pd
import requests
import ruamel.yaml
from bs4 import BeautifulSoup

from configs.common import (
    download_file,
    prepare_image_copy,
    mount,
    set_root_password,
    setup_cloud_init,
)

logger = logging.getLogger(__name__)


def get_lts_codename() -> str:
    logger.info("Retrieving Ubuntu Releases wiki page")
    releases = requests.get("https://wiki.ubuntu.com/Releases").content.decode()
    logger.info("Parsing page contents")
    soup = BeautifulSoup(releases, features="lxml")

    def contains_elem(elem, tag, text):
        return any(e.text == text for e in elem.find_all(tag))

    logger.info("Looking for releases table")
    release_table = next(
        table
        for table in soup.find_all("table")
        if (
            contains_elem(table, "p", "Code name")
            and contains_elem(table, "p", "Release")
        )
    )

    logger.info("Parsing releases table with pandas")
    versions = pd.read_html(str(release_table), header=0)[0]
    code_name = versions[versions["Version"].str.lower().str.contains("lts")].iloc[0][
        "Code name"
    ]
    logger.info("Found latest release codename %s", code_name)
    short_code_name = code_name.split(" ")[0].lower()
    logger.info("Using release short codename %s", short_code_name)
    assert short_code_name.isalpha()

    return short_code_name


def ensure_image_downloaded(
    codename: str, image_suffix: str
) -> typing.Tuple[bool, str]:
    # image_file_name = f"{codename}-server-cloudimg-amd64.img"
    image_file_name = f"{codename}{image_suffix}"

    logger.info("Getting sha256 list for %s", codename)
    image_hashes = requests.get(
        f"https://cloud-images.ubuntu.com/{codename}/current/SHA256SUMS"
    ).text
    latest_image_url = (
        f"https://cloud-images.ubuntu.com/{codename}/current/{image_file_name}"
    )
    latest_image = latest_image_url.split("/")[-1]

    logger.info("Looking for sha256 of %s", latest_image)
    target_hash = [
        line for line in image_hashes.splitlines() if line.endswith(f"*{latest_image}")
    ][0].split(" ")[0]
    logger.info("Found hash %s for %s", target_hash, latest_image)

    return download_file(latest_image_url, target_hash)


def build(ubuntu_codename: str, image_suffix: str) -> str:
    original_image = ensure_image_downloaded(ubuntu_codename, image_suffix)[1]
    working_image = prepare_image_copy(original_image)
    g = mount(working_image)

    # release_detail_files = [f["name"] for f in g.readdir("/etc") if "release" in f["name"]]
    # for f in release_detail_files:
    #     logger.info(f"Reading /etc/{f}")
    #     logger.info(g.read_file(f"/etc/{f}").decode().strip())

    set_root_password(g, "password")

    netplan_config = io.StringIO()
    ruamel.yaml.YAML().dump(
        {
            "network": {
                "version": 2,
                "renderer": "networkd",  # or NetworkManager
                "ethernets": {
                    "enp0s2": {"dhcp4": True, "dhcp6": True,}
                },  # is interface name stable?
            }
        },
        netplan_config,
    )
    netplan_config.seek(0)

    # Configure netplan
    g.write("/etc/netplan/default.yaml", netplan_config.read())

    setup_cloud_init(g)

    g.close()

    return working_image
