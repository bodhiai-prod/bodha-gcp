data "google_project" "current" {
  project_id = var.project_id
}

locals {
  name          = "bodha-${var.environment}"
  cloudbuild_sa = "serviceAccount:${google_service_account.build.email}"
  required_apis = toset([
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "dns.googleapis.com",
    "firestore.googleapis.com",
    "firebase.googleapis.com",
    "firebasehosting.googleapis.com",
    "iam.googleapis.com",
    "identitytoolkit.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "secretmanager.googleapis.com",
    "servicemanagement.googleapis.com",
    "serviceusage.googleapis.com",
    "storagetransfer.googleapis.com",
    "storage.googleapis.com",
    "vision.googleapis.com",
  ])
}

resource "google_project_service" "required" {
  for_each           = local.required_apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "main" {
  name                    = "${local.name}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.required]
}

resource "google_compute_subnetwork" "main" {
  name                     = "${local.name}-subnet"
  region                   = var.region
  network                  = google_compute_network.main.id
  ip_cidr_range            = "10.40.0.0/24"
  private_ip_google_access = true
}

resource "google_compute_firewall" "web" {
  name          = "${local.name}-web"
  network       = google_compute_network.main.name
  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["bodha-web"]
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
  allow {
    protocol = "udp"
    ports    = ["443"]
  }
}

resource "google_compute_firewall" "iap_ssh" {
  name          = "${local.name}-iap-ssh"
  network       = google_compute_network.main.name
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["bodha-web"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_address" "app" {
  name   = "${local.name}-ip"
  region = var.region

  depends_on = [google_project_service.required]
}

resource "google_service_account" "app" {
  account_id   = "${local.name}-app"
  display_name = "Bodha ${var.environment} application VM"
}

resource "google_service_account" "build" {
  account_id   = "${local.name}-build"
  display_name = "Bodha ${var.environment} Cloud Build deployer"
}

resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = "bodha"
  format        = "DOCKER"
  description   = "Immutable Bodha backend and frontend images"
  depends_on    = [google_project_service.required]
}

resource "google_storage_bucket" "app" {
  name                        = var.bucket_name
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning { enabled = true }
  soft_delete_policy { retention_duration_seconds = 604800 }
  lifecycle_rule {
    condition { num_newer_versions = 3 }
    action { type = "Delete" }
  }
  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "app_objects" {
  bucket = google_storage_bucket.app.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.app.email}"
}

# The application performs a bucket-metadata read during its readiness check.
# Object Admin covers object operations, while this grants only bucket-level
# read access to the application's own bucket.
resource "google_storage_bucket_iam_member" "app_bucket_metadata" {
  bucket = google_storage_bucket.app.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.app.email}"
}

# `gcloud builds submit` stages the source in this Google-managed bucket, then
# the project's default Compute Engine service account reads that archive to
# start the build.  Grant only the read permission required for that hand-off.
resource "google_storage_bucket_iam_member" "cloudbuild_source_reader" {
  bucket = "${var.project_id}_cloudbuild"
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "app_roles" {
  for_each = toset([
    "roles/artifactregistry.reader",
    "roles/aiplatform.user",
    "roles/firebaseauth.admin",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/datastore.user",
    "roles/secretmanager.secretAccessor",
    "roles/serviceusage.serviceUsageConsumer",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.app.email}"
}

resource "google_project_iam_member" "cloudbuild_roles" {
  for_each = toset([
    "roles/artifactregistry.writer",
    "roles/cloudbuild.builds.builder",
    "roles/compute.instanceAdmin.v1",
    "roles/compute.osAdminLogin",
    "roles/iap.tunnelResourceAccessor",
    "roles/logging.logWriter",
    "roles/serviceusage.serviceUsageConsumer",
  ])
  project = var.project_id
  role    = each.value
  member  = local.cloudbuild_sa
}

resource "google_service_account_iam_member" "cloudbuild_uses_vm_sa" {
  service_account_id = google_service_account.app.name
  role               = "roles/iam.serviceAccountUser"
  member             = local.cloudbuild_sa
}

resource "google_secret_manager_secret" "runtime" {
  for_each  = toset(["bodha-mongo-url", "bodha-jwt-secret", "bodha-openrouter-api-key", "bodha-google-oauth-client-secret"])
  secret_id = each.value
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_compute_disk" "data" {
  name                      = "${local.name}-data"
  type                      = "pd-balanced"
  zone                      = var.zone
  size                      = var.data_disk_gb
  physical_block_size_bytes = 4096

  depends_on = [google_project_service.required]
}

resource "google_compute_resource_policy" "snapshots" {
  name   = "${local.name}-daily-snapshots"
  region = var.region
  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "20:00"
      }
    }
    retention_policy {
      max_retention_days    = 14
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
    snapshot_properties {
      storage_locations = [var.region]
      guest_flush       = true
      labels            = { app = "bodha", environment = var.environment }
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_compute_disk_resource_policy_attachment" "snapshots" {
  name = google_compute_resource_policy.snapshots.name
  disk = google_compute_disk.data.name
  zone = var.zone
}

resource "google_storage_bucket_object" "compose" {
  name   = "_deployment/docker-compose.gcp.yml"
  bucket = google_storage_bucket.app.name
  source = "${path.module}/../../deploy/docker-compose.gcp.yml"
}

resource "google_storage_bucket_object" "deploy" {
  name   = "_deployment/bodha-deploy"
  bucket = google_storage_bucket.app.name
  source = "${path.module}/../../deploy/vm/bodha-deploy"
}

data "google_compute_image" "ubuntu" {
  family  = "ubuntu-2404-lts-amd64"
  project = "ubuntu-os-cloud"
}

resource "google_compute_instance" "app" {
  name         = local.name
  zone         = var.zone
  machine_type = var.machine_type
  tags         = ["bodha-web"]

  boot_disk {
    initialize_params {
      image = data.google_compute_image.ubuntu.self_link
      size  = 30
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.data.id
    device_name = "bodha-data"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.main.id
    access_config { nat_ip = google_compute_address.app.address }
  }

  service_account {
    email  = google_service_account.app.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
    startup-script = templatefile("${path.module}/startup.sh.tftpl", {
      project_id     = var.project_id
      region         = var.region
      bucket_name    = var.bucket_name
      storage_prefix = var.storage_prefix
      app_domain     = var.app_domain
      cors_origins   = var.cors_origins
      admin_emails   = var.admin_emails
    })
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  allow_stopping_for_update = true
  deletion_protection       = true

  depends_on = [
    google_project_iam_member.app_roles,
    google_storage_bucket_object.compose,
    google_storage_bucket_object.deploy,
  ]
}

resource "google_dns_managed_zone" "app" {
  count       = var.manage_dns ? 1 : 0
  name        = var.dns_zone_name
  dns_name    = var.dns_name
  description = "Bodha production DNS; delegate only during cutover"
  depends_on  = [google_project_service.required]
}

resource "google_dns_record_set" "app" {
  count        = var.manage_dns ? 1 : 0
  managed_zone = google_dns_managed_zone.app[0].name
  name         = "${var.app_domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.app.address]
}

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  display_name = "Bodha operations email"
  type         = "email"
  labels       = { email_address = var.alert_email }
  depends_on   = [google_project_service.required]
}

resource "google_monitoring_uptime_check_config" "app" {
  count        = var.app_domain == "" ? 0 : 1
  display_name = "${local.name} live endpoint"
  timeout      = "10s"
  period       = "60s"
  monitored_resource {
    type   = "uptime_url"
    labels = { project_id = var.project_id, host = var.app_domain }
  }
  http_check {
    path         = "/health/live"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  count        = var.app_domain == "" ? 0 : 1
  display_name = "${local.name} unavailable"
  combiner     = "OR"
  conditions {
    display_name = "Uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\""
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "120s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_FRACTION_TRUE"
      }
    }
  }
  notification_channels = var.alert_email == "" ? [] : [google_monitoring_notification_channel.email[0].name]
}

resource "google_billing_budget" "monthly" {
  count           = var.billing_account_id == "" ? 0 : 1
  billing_account = var.billing_account_id
  display_name    = "Bodha ${var.environment} monthly budget"
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }
  budget_filter { projects = ["projects/${data.google_project.current.number}"] }
  threshold_rules { threshold_percent = 0.5 }
  threshold_rules { threshold_percent = 0.8 }
  threshold_rules { threshold_percent = 1.0 }

  depends_on = [google_project_service.required]
}
