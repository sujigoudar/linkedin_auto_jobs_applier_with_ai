########################################################################
# OCI Always Free A1 warm standby -- INACTIVE by design.
#
# This provisions compute only. It does NOT install broker credentials,
# does NOT enable the trading systemd unit, and does NOT touch the
# active site. Review every value in terraform.tfvars.example before
# `terraform plan`; nothing here has been applied.
#
# Current Always Free A1 allowance (verify against your own tenancy
# before applying -- Oracle can change or already have this consumed):
#   2 OCPUs, 12 GB RAM equivalent, 200 GB combined boot+block storage,
#   per home region, subject to capacity and idle-instance reclamation.
########################################################################

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 5.0"
    }
  }
}

provider "oci" {
  # Auth via OCI CLI config (~/.oci/config) or environment variables --
  # never hardcode keys here. See docs/02_CLOUD_SELECTION.md's account
  # workflow: discover the tenancy/compartment with an authenticated
  # read-only CLI call first, and paste the *existing* OCIDs below.
  region = var.region
}

variable "region" {
  description = "Your tenancy's fixed home region (Always Free A1 capacity is only in the home region)."
  type        = string
}

variable "compartment_id" {
  description = "OCID of a DEDICATED compartment for this standby -- never the tenancy root, so narrow IAM policy and cost tracking are possible."
  type        = string
}

variable "subnet_id" {
  description = "OCID of an existing subnet (create a minimal VCN separately -- not templated here to avoid silently provisioning networking you didn't review)."
  type        = string
}

variable "ssh_public_key" {
  description = "Your own SSH public key -- password auth is disabled."
  type        = string
}

variable "ocpus" {
  description = "A1 flex OCPU count. Verify remaining free allowance before raising above the default."
  type        = number
  default     = 2
}

variable "memory_in_gbs" {
  description = "A1 flex memory (GB). Verify remaining free allowance before raising above the default."
  type        = number
  default     = 12
}

variable "boot_volume_size_in_gbs" {
  description = "Boot volume size. Default leaves headroom under the 200GB combined Always Free block-storage allowance for Litestream restore testing."
  type        = number
  default     = 50
}

variable "availability_domain_index" {
  description = "0-based index into this region's availability domain list. Verify in the OCI console which AD your tenancy's Always Free A1 capacity actually sits in before changing this -- most single-AD regions only have index 0."
  type        = number
  default     = 0
}

# oci_core_instance requires a real availability_domain string -- it cannot
# be null (this previously failed `terraform validate` with exactly that
# error). Discovered from the tenancy/region actually being applied to,
# never hardcoded, since AD names are tenancy-specific
# (e.g. "AbCd:US-ASHBURN-AD-1").
data "oci_identity_availability_domains" "ads" {
  compartment_id = var.compartment_id
}

data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_id
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "22.04"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order                = "DESC"
}

resource "oci_core_instance" "standby" {
  compartment_id      = var.compartment_id
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[var.availability_domain_index].name
  display_name        = "signal-copier-standby"
  shape                = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_in_gbs
  }

  create_vnic_details {
    subnet_id        = var.subnet_id
    assign_public_ip = true
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs  = var.boot_volume_size_in_gbs
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data            = base64encode(file("${path.module}/../../cloud-init/standby-init.yaml"))
  }

  freeform_tags = {
    "role"        = "signal-copier-standby"
    "environment" = "standby-inactive"
    "managed_by"  = "terraform"
  }
}

output "standby_public_ip" {
  value       = oci_core_instance.standby.public_ip
  description = "Public IP of the standby -- point Cloudflare monitoring at this, and restrict it with OCI security-list/NSG rules to only the ports the status page and SSH need."
}
