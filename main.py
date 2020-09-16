import argparse
import ctypes
import logging
import os
import pathlib
import subprocess
import sys
import typing

import guestfs

import configs.ubuntu

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = pathlib.Path(__file__).parent.absolute()

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
guestfs_version = guestfs.GuestFS().version()
assert guestfs_version["major"] == 1
assert guestfs_version["minor"] >= 42


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


if __name__ == "__main__":
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
    parser.add_argument(
        "--work-dir",
        help="Set working directory (where image will be downloaded and final image will be created)",
    )
    parser.add_argument(
        "--ubuntu-suffix",
        default="-server-cloudimg-amd64-azure.vhd.zip",
        help="Suffix for downloading the Ubuntu Cloud image (i.e. {ubuntu_lts_codename}{image_suffix})",
    )

    args = parser.parse_args()

    working_dir = args.work_dir or os.environ.get("WORK_DIR")
    if working_dir:
        os.chdir(working_dir)

    # image = build_ubuntu(image_suffix="-server-cloudimg-amd64.img")
    ubuntu_codename = configs.ubuntu.get_lts_codename()
    image = configs.ubuntu.build(ubuntu_codename, image_suffix=args.ubuntu_suffix)

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
