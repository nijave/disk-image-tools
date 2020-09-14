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

## Build Configuration
<details>
  <summary>Ubuntu Cloud</summary>

  The script is currently monolithic and image configurations are applied in
  `build_ubuntu(ubuntu_codename: str, image_suffix: str = "-server-cloudimg-amd64-azure.vhd.zip") -> str`
  which returns the name of the image it built. The current script creates an **insecure** debug image that
  will bootstrap with cloud-init designed to run on Microsoft Hyper-V with Data Exchange integration enabled.
  The image is designed to work with an EC2-compatible meta-data service and will set the machine's hostname
  by getting the VM name from Hyper-V through Data Exchange.
</details>
