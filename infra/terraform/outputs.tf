output "vm_name" { value = google_compute_instance.app.name }
output "vm_external_ip" { value = google_compute_address.app.address }
output "bucket_name" { value = google_storage_bucket.app.name }
output "artifact_repository" { value = google_artifact_registry_repository.app.id }
output "service_account" { value = google_service_account.app.email }
output "build_service_account" { value = google_service_account.build.email }
output "dns_name_servers" {
  value = var.manage_dns ? google_dns_managed_zone.app[0].name_servers : []
}
