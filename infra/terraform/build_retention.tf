variable "artifact_cleanup_dry_run" {
  description = "Leave true until the read-only retention verifier and targeted plan are reviewed."
  type        = bool
  default     = true
}

variable "artifact_protected_build_ids" {
  description = "Exact latest-20 snapshot plus live/rollback build IDs. Retained irrespective of age."
  type        = list(string)
  validation {
    condition = length(distinct(var.artifact_protected_build_ids)) >= 20 && alltrue([
      for id in var.artifact_protected_build_ids : can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", id))
    ])
    error_message = "At least 20 distinct, full UUID build IDs must be protected."
  }
}

variable "build_source_created_before" {
  description = "Conservative UTC date before every protected source upload. Never advance without re-verifying the latest 20 builds."
  type        = string
  validation {
    condition     = can(formatdate("YYYY-MM-DD", "${var.build_source_created_before}T00:00:00Z"))
    error_message = "Supply a valid YYYY-MM-DD source-retention cutoff."
  }
}

# This is the existing Cloud Build staging bucket, imported before applying.
# Never apply source-archive retention to google_storage_bucket.app.
resource "google_storage_bucket" "build_sources" {
  name                        = "${var.project_id}_cloudbuild"
  project                     = var.project_id
  location                    = "US"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  force_destroy               = false

  soft_delete_policy { retention_duration_seconds = 604800 }
  lifecycle_rule {
    action { type = "Delete" }
    condition {
      age            = 30
      created_before = var.build_source_created_before
      matches_prefix = ["source/"]
      matches_suffix = [".tgz"]
      with_state     = "LIVE"
    }
  }
  lifecycle { prevent_destroy = true }
}
