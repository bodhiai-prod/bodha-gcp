# Deploy these additive indexes and wait for READY before releasing the
# event-driven worker. Existing documents and their IDs are unchanged.
resource "google_firestore_index" "review_queue_due" {
  for_each   = toset(["next_run_at", "lease_until"])
  project    = var.project_id
  database   = "(default)"
  collection = "paper_review_jobs"

  fields {
    field_path = "kind"
    order      = "ASCENDING"
  }
  fields {
    field_path = "status"
    order      = "ASCENDING"
  }
  fields {
    field_path = each.value
    order      = "ASCENDING"
  }
}
