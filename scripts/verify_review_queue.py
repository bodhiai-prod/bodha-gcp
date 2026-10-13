"""Read-only preflight for queue indexes and the VM's Listen permission.

Does not claim jobs, write documents, or call generation providers. Run in the
worker environment with ADC, or use --gcloud-user for an operator's CLI login.
"""
import argparse
import subprocess
import threading
from datetime import datetime, timezone

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2.credentials import Credentials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--database", default="(default)")
    parser.add_argument("--gcloud-user", action="store_true")
    args = parser.parse_args()
    credentials = None
    if args.gcloud_user:
        token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
        credentials = Credentials(token)
    client = firestore.Client(project=args.project, database=args.database, credentials=credentials)
    collection = client.collection("paper_review_jobs")
    now = datetime.now(timezone.utc).isoformat()
    watch = None
    try:
        for kinds in (["paper_review", "paper_generation"], ["paper_diagram"]):
            for status, field, operator in (("leased", "lease_until", "<"), ("queued", "next_run_at", "<=")):
                query = (collection.where(filter=FieldFilter("status", "==", status))
                         .where(filter=FieldFilter("kind", "in", kinds))
                         .where(filter=FieldFilter(field, operator, now)).order_by(field).limit(8))
                count = len(list(query.stream(timeout=15)))
                print(f"OK indexed {','.join(kinds)} {status}: {count} eligible candidates (limit 8)")
        ready = threading.Event()
        watch = collection.where(filter=FieldFilter("status", "==", "queued")).on_snapshot(
            lambda documents, changes, read_time: ready.set()
        )
        if not ready.wait(timeout=20):
            raise RuntimeError("Queue listener did not receive its initial snapshot; inspect credentials/Listen permission")
        print("OK queue listener initial snapshot")
    finally:
        if watch is not None:
            watch.unsubscribe()
        client.close()


if __name__ == "__main__":
    main()
