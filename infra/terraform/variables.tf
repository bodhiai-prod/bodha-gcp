variable "project_id" {
  description = "Existing GCP project with billing enabled."
  type        = string
}

variable "region" {
  type    = string
  default = "asia-south1"
}

variable "zone" {
  type    = string
  default = "asia-south1-a"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "bucket_name" {
  description = "Globally unique GCS bucket name."
  type        = string
}

variable "app_domain" {
  description = "Public hostname used by Caddy, without a scheme."
  type        = string
}

variable "cors_origins" {
  description = "Comma-separated origins accepted by FastAPI."
  type        = string
}

variable "admin_emails" {
  description = "Comma-separated Google-account emails granted the admin role on first sign-in."
  type        = string
  default     = ""
}

variable "machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "data_disk_gb" {
  type    = number
  default = 100
}

variable "storage_prefix" {
  type    = string
  default = "bodhiai"
}

variable "manage_dns" {
  description = "Create a Cloud DNS zone and A record. Delegate it only at cutover."
  type        = bool
  default     = false
}

variable "dns_zone_name" {
  type    = string
  default = "bodha-prod"
}

variable "dns_name" {
  description = "DNS zone suffix, including trailing dot (for example example.com.)."
  type        = string
  default     = ""
}

variable "alert_email" {
  description = "Email for uptime alerts; empty disables notification resources."
  type        = string
  default     = ""
}

variable "billing_account_id" {
  description = "Billing account ID for the budget; empty disables the budget."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  type    = number
  default = 200
}
