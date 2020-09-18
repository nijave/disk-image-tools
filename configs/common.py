import crypt
import datetime
import hashlib
import io
import logging
import os
import pathlib
import re
import shutil

import guestfs
import requests
import ruamel.yaml

logger = logging.getLogger(__name__)


def save_file(uri, path):
    logger.info("Downloading %s", uri)
    with requests.get(uri, stream=True) as response:
        with open(path, "wb") as f:
            shutil.copyfileobj(response.raw, f)


def check_file_hash(path, _hash):
    sha256_hash = hashlib.sha256()
    with open(path, "rb") as f:
        # Read and update hash string value in blocks of 1M
        for byte_block in iter(lambda: f.read(1024 ** 2), b""):
            sha256_hash.update(byte_block)
    file_hash = sha256_hash.hexdigest()
    logger.info("Checking file %s matches %s", file_hash, _hash)
    assert sha256_hash.hexdigest() == _hash


def download_file(latest_image_url, target_hash):
    image_file_name = latest_image_url.split("/")[-1]
    if not pathlib.Path(image_file_name).is_file():
        logger.info("Image file missing. Image will be downloaded")
        save_file(latest_image_url, image_file_name)
        check_file_hash(image_file_name, target_hash)
    else:
        try:
            check_file_hash(image_file_name, target_hash)
        except AssertionError:
            logger.warning("File hash didn't match. Attempting to download a new copy")
            save_file(latest_image_url, image_file_name)
            check_file_hash(image_file_name, target_hash)

    logger.info("Image successfully downloaded")
    return True, image_file_name


def prepare_image_copy(original_image):
    datestamp = datetime.datetime.now().strftime("%Y%m%d")
    working_image = f"{datestamp}_{original_image}"
    logger.info("Creating copy of disk image %s to %s", original_image, working_image)
    shutil.copy2(original_image, working_image)
    logger.info("Making disk image copy writeable")
    os.chmod(working_image, 0o600)

    return working_image


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


SCRIPT_DIR = pathlib.Path(__file__).parent.absolute()


def guess_image_format(image: str) -> str:
    """
    Tries to guess a disk image format
    :param image: filename
    :return: format for use with guestfs/qemu
    """
    ext = image.split(".")[-1]
    return {"img": "qcow2", "vhd": "vpc"}.get(ext, ext)


def guestfs_event_logger(event, event_handle, message, arr):
    logger.info(
        f"guestfs: %s %s",
        guestfs.event_to_string(event),
        message.encode("unicode_escape").decode("ascii")
        if isinstance(message, str)
        else message,
    )


def set_root_password(g, pwd):
    # Add root account password
    shadow = g.read_file("/etc/shadow").decode()
    logger.warning("Setting root password to '%s'", pwd)
    passwd = crypt.crypt(pwd, crypt.mksalt())
    shadow = shadow.replace("root:*", f"root:{passwd}")
    g.write("/etc/shadow", shadow)


def setup_cloud_init(g):
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

    python_search_base = "/usr/lib"
    ec2_ds_path = "/cloudinit/sources/DataSourceEc2.py"
    logger.info(
        "Looking for cloudinit Python module starting in %s", python_search_base
    )
    locations = [
        pathlib.Path(python_search_base) / f.lstrip("/")
        for f in g.find(python_search_base)
        if f.endswith(ec2_ds_path)
    ]

    assert len(locations) == 1
    python_package_path = re.sub(re.escape(ec2_ds_path) + "$", "", str(locations[0]))
    logger.info("Found Python packages at %s", python_package_path)

    # Override some parts of the Ec2LocalDataSource to instead pull
    # information from Hyper-V KVP service (Data Exchange)
    g.copy_in(str(SCRIPT_DIR / "0001-cloudinit.patch"), python_package_path)
    g.command(
        [
            "/usr/bin/patch",
            "-p1",
            "-d",
            python_package_path,
            f"{python_package_path}/cloudinit/sources/DataSourceEc2.py",
            f"{python_package_path}/0001-cloudinit.patch",
        ]
    )
