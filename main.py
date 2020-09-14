"""
dnf install -y python3-pip libguestfs
pip install /vendor/*.whl
"""
import argparse
import crypt
import ctypes
import datetime
import hashlib
import io
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import typing
import zipfile
import zlib

"""
1.40.x that ships with Fedora 31 has a bug in the Python bindings used for
set_event_callback causing segfaults in the Python interpreter

wget https://../libguestfs-1.42.0.tar.gz
tar xf libguestfs-1.42.0.tar.gz
cd libguestfs-1.42.0
./configure CFLAGS=-fPIC --enable-python
make -j16
cd python
make sdist
sed -i 's/from distutils.core/from setuptools/g' setup.py
python setup.py bdist_wheel

LIBGUESTFS_PATH="/home/nick/.local/src/libguestfs-1.42.0/appliance"
LD_LIBRARY_PATH="$LIBGUESTFS_PATH/../lib/.libs"

# -or-
# Fedora 31
sudo dnf install --allowerasing \
    https://download-ib01.fedoraproject.org/pub/fedora/linux/releases/32/Everything/x86_64/os/Packages/l/libguestfs-1.42.0-2.fc32.x86_64.rpm \
    https://download-ib01.fedoraproject.org/pub/fedora/linux/releases/32/Everything/x86_64/os/Packages/l/libguestfs-devel-1.42.0-2.fc32.x86_64.rpm \
    https://download-ib01.fedoraproject.org/pub/fedora/linux/releases/32/Everything/x86_64/os/Packages/l/libguestfs-tools-1.42.0-2.fc32.noarch.rpm \
    https://download-ib01.fedoraproject.org/pub/fedora/linux/releases/32/Everything/x86_64/os/Packages/p/perl-Sys-Guestfs-1.42.0-2.fc32.x86_64.rpm \
    https://download-ib01.fedoraproject.org/pub/fedora/linux/releases/32/Everything/x86_64/os/Packages/l/libguestfs-tools-c-1.42.0-2.fc32.x86_64.rpm \
    https://download-ib01.fedoraproject.org/pub/fedora/linux/releases/32/Everything/x86_64/os/Packages/l/libguestfs-xfs-1.42.0-2.fc32.x86_64.rpm
"""


import guestfs
import pandas as pd
import requests
import ruamel.yaml
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = pathlib.Path(__file__).parent.absolute()


def get_ubuntu_lts_codename() -> str:
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

    def download_file(uri, path):
        logger.info("Downloading %s", uri)
        with requests.get(uri, stream=True) as response:
            with open(path, "wb") as f:
                shutil.copyfileobj(response.raw, f)

    def check_file_hash(path, _hash):
        sha256_hash = hashlib.sha256()
        with open(path, "rb") as f:
            # Read and update hash string value in blocks of 4K
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        file_hash = sha256_hash.hexdigest()
        logger.info("Checking file %s matches %s", file_hash, _hash)
        assert sha256_hash.hexdigest() == _hash

    def check_file_crc32(path, _crc):
        with open(path, "rb") as f:
            crc = 0
            for byte_block in iter(lambda: f.read(4096), b""):
                crc = zlib.crc32(byte_block, crc)
        logger.info("Checking file %s matches %s", crc, _crc)
        assert crc == _crc

    if not pathlib.Path(latest_image).is_file():
        logger.info("Image file missing. Image will be downloaded")
        download_file(latest_image_url, latest_image)
        check_file_hash(latest_image, target_hash)
    else:
        try:
            check_file_hash(latest_image, target_hash)
        except AssertionError:
            logger.warning("File hash didn't match. Attempting to download a new copy")
            download_file(latest_image_url, latest_image)
            check_file_hash(latest_image, target_hash)

    if image_file_name.endswith(".zip"):
        logger.info("Unzipping image file")
        with zipfile.ZipFile(latest_image) as zf:
            file_details = sorted(zf.filelist, key=lambda i: i.file_size, reverse=True)[
                0
            ]
            logger.info(
                "Assuming largest file '%s' is the disk image", file_details.filename
            )
            latest_image = re.sub(r"\.zip$", "", image_file_name)
            try:
                logger.info("Checking to see if file already exists")
                check_file_crc32(latest_image, file_details.CRC)
                logger.info("Skipping extraction")
            except (AssertionError, FileNotFoundError):
                logger.info("Extracting %s", file_details.filename)
                start = time.time()
                zf.extract(file_details.filename, ".")
                os.rename(file_details.filename, latest_image)
                end = time.time()
                logger.info(
                    "Extracted %i bytes in %d seconds (%d MB/s)",
                    file_details.file_size,
                    end - start,
                    file_details.file_size / 1024 / 1024 / (end - start),
                )

    logger.info("Image successfully downloaded")
    return True, latest_image


def get_python_interpreter_arguments() -> typing.Iterable[str]:
    """
    Grab arguments passed to the Python interpreter (instead of passed to the script)
    https://stackoverflow.com/a/57914236
    :return: typing.Iterable[str]
    """
    argc = ctypes.c_int()
    argv = ctypes.POINTER(
        ctypes.c_wchar_p if sys.version_info >= (3,) else ctypes.c_char_p
    )()
    ctypes.pythonapi.Py_GetArgcArgv(ctypes.byref(argc), ctypes.byref(argv))

    # Ctypes are weird. They can't be used in list comprehensions, you can't use `in` with them, and you can't
    # use a for-each loop on them. We have to do an old-school for-i loop.
    arguments = list()
    for i in range(argc.value - len(sys.argv) + 1):
        arguments.append(argv[i])

    return arguments


def guestfs_event_logger(event, event_handle, message, arr):
    logger.info(
        f"guestfs: %s %s",
        guestfs.event_to_string(event),
        message.encode("unicode_escape").decode("ascii")
        if isinstance(message, str)
        else message,
    )


def guess_image_format(image: str) -> str:
    """
    Tries to guess a disk image format
    :param image: filename
    :return: format for use with guestfs/qemu
    """
    ext = image.split(".")[-1]
    return {"img": "qcow2", "vhd": "vpc"}.get(ext, ext)


def mount(working_image: str) -> guestfs.GuestFS:
    g = guestfs.GuestFS(python_return_dict=True)
    g.add_drive_opts(
        working_image, format=guess_image_format(working_image), readonly=False,
    )
    g.set_event_callback(guestfs_event_logger, event_bitmask=guestfs.EVENT_ALL)
    g.set_trace(True)
    g.set_autosync(True)
    g.set_backend("direct")

    g.launch()
    g.inspect_os()

    roots = g.inspect_get_roots()
    logger.info("Found roots: %s", roots)
    assert len(roots) == 1
    root = roots[0]

    logger.info(f"Root filesystem is {g.list_filesystems()[root]}")
    logger.info(f"Product: {g.inspect_get_product_name(root)}")
    logger.info(
        f"Version: {g.inspect_get_major_version(root)}.{g.inspect_get_minor_version(root)}"
    )
    logger.info(f"Type: {g.inspect_get_type(root)}")
    logger.info(f"Distro: {g.inspect_get_distro(root)}")

    g.mount(root, "/")

    return g


def build_ubuntu(image_suffix: str = "-server-cloudimg-amd64-azure.vhd.zip") -> str:
    datestamp = datetime.datetime.now().strftime("%Y%m%d")
    ubuntu_codename = get_ubuntu_lts_codename()
    original_image = ensure_image_downloaded(ubuntu_codename, image_suffix)[1]
    working_image = f"{datestamp}_{original_image}"
    logger.info("Creating copy of disk image %s to %s", original_image, working_image)
    shutil.copy2(original_image, working_image)
    logger.info("Making disk image copy writeable")
    os.chmod(working_image, 0o600)
    g = mount(working_image)

    # release_detail_files = [f["name"] for f in g.readdir("/etc") if "release" in f["name"]]
    # for f in release_detail_files:
    #     logger.info(f"Reading /etc/{f}")
    #     logger.info(g.read_file(f"/etc/{f}").decode().strip())

    # Add root account password
    shadow = g.read_file("/etc/shadow").decode()
    root_password = "password"
    logger.warning("Setting root password to '%s'", root_password)
    passwd = crypt.crypt(root_password, crypt.mksalt())
    shadow = shadow.replace("root:*", f"root:{passwd}")
    g.write("/etc/shadow", shadow)

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

    # Configure link-local on-link route on startup
    cloud_init_override_path = (
        "/etc/systemd/system/cloud-init.service.d/01-add-route.conf"
    )
    g.mkdir_p("/".join(cloud_init_override_path.split("/")[:-1]))
    g.write(
        cloud_init_override_path,
        """
    [Service]
    ExecStartPre=/bin/bash -c 'ip route add 169.254.169.0/24 dev "$(ls /sys/class/net | grep -v lo | head -n 1)"'
    """.strip(),
    )
    g.chown(0, 0, cloud_init_override_path)  # root=0, root=0
    g.chmod(0o644, cloud_init_override_path)

    # Configure cloud-init datasource
    cloud_init_config_path = "/etc/cloud/cloud.cfg.d/99-ec2-datasource.cfg"
    datasource_config = io.StringIO()
    ruamel.yaml.YAML().dump(
        {"datasource": {"Ec2": {"strict_id": False},}}, datasource_config,
    )
    datasource_config.seek(0)
    g.write(
        cloud_init_config_path, datasource_config.read(),
    )

    # Override some parts of the Ec2LocalDataSource to instead pull
    # information from Hyper-V KVP service (Data Exchange)
    g.copy_in(
        str(SCRIPT_DIR / "0001-cloudinit.patch"), "/usr/lib/python3/dist-packages"
    )
    g.command(
        [
            "patch",
            "-p1",
            "-d",
            "/usr/lib/python3/dist-packages",
            "/usr/lib/python3/dist-packages/cloudinit/sources/DataSourceEc2.py",
            "/usr/lib/python3/dist-packages/0001-cloudinit.patch",
        ]
    )

    # Don't close the image if interpreter will drop to interactive mode
    if "-i" not in get_python_interpreter_arguments():
        g.close()

    return working_image


if __name__ == "__main__":
    if os.environ.get("DISK_IMAGE_DIRECTORY"):
        os.chdir(os.environ["DISK_IMAGE_DIRECTORY"])

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--convert",
        nargs="?",
        const="vhd",
        type=str,
        help="Converts the image to a different format with qemu-image convert (default is vhd)",
    )
    parser.add_argument(
        "--resize", help="Resize the disk image using qemu-img resize (i.e. +18G)"
    )

    args = parser.parse_args()

    image = build_ubuntu(image_suffix="-server-cloudimg-amd64.img")

    if args.resize:
        logger.info("Resizing image %s by %s", image, args.resize)
        subprocess.check_output(["qemu-img", "resize", image, args.resize])
        logger.info("Resize complete")

    if args.convert:
        fmt = args.convert if args.convert != "vhd" else "vpc"
        logger.info("Converting image to format %s", fmt)
        new_image = ".".join(image.split(".")[:-1] + [args.convert])
        subprocess.check_output(
            [
                "qemu-img",
                "convert",
                "-f",
                guess_image_format(image),
                "-O",
                fmt,
                image,
                new_image,
            ]
        )
        image = new_image

    print(image, end="")
