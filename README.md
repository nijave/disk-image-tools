# disk-image-tools

Creates and modifies virtual machine golden disk images in userspace using libguestfs

## Usage
(requires Docker)
`./run.sh`

## Supported Operating Systems
<details>
  <summary>Ubuntu Cloud (https://cloud.ubuntu.com)</summary>

  Defaults to looking up the codename of the latest LTS release
  and using that. See https://github.com/nijave/disk-image-tools/commit/c29408ae27fef77c7aa94b15d6b12fc11dfa7d5d
  for reference
</details>
<details>
    <summary>CentOS Cloud (https://cloud.centos.org)</summary>

    Defaults to using the latest image for the hardcoded major version found in the `REL` constant of `configs/centos.py`
</details>


## Build Configuration
Both images are designed to be built similarly. The resulting image is expected to be able to read key-value pair data
from Hyper-V KVP service (Data Exchange integration). The images will set the hostname below network is brought up
to the name of the Hyper-V VM. Networking will be enabled with DHCP. They will then expect to find an EC2-like metadata
service to bootstrap from.

Images have the root password hardcoded to `password` for debugging
<details>
  <summary>Ubuntu Cloud</summary>

  Image configurations are applied in `configs.ubuntu.build(ubuntu_codename: str) -> str`
  which returns the name of the image it built.
</details>
<details>
  <summary>CentOS Cloud</summary>

  Image configurations are applied in `configs.centos.build() -> str`
  which returns the name of the image it built.
</details>
