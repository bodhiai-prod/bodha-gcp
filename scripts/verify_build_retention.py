#!/usr/bin/env python3
"""Read-only retention preview. Prints proposed tfvars; never writes/deletes.

Run with --settings infra/terraform/build_retention.auto.tfvars.json before
applying or advancing the cutoff. Requires authenticated operator gcloud.
"""
import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = "bodha-ai-prod"
REPOSITORY = f"asia-south1-docker.pkg.dev/{PROJECT}/bodha"
BUCKET = f"{PROJECT}_cloudbuild"


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def preview(builds, images, objects, live_ids, settings=None, now=None):
    """Fail closed if protected sources/images are absent or settings unsafe."""
    now = now or datetime.now(timezone.utc)
    builds = sorted(builds, key=lambda b: timestamp(b["createTime"]), reverse=True)
    if len(builds) < 20:
        raise ValueError("Fewer than 20 builds: refuse to configure cleanup")
    if any(b["status"] not in {"SUCCESS", "FAILURE", "CANCELLED", "TIMEOUT", "EXPIRED", "INTERNAL_ERROR"} for b in builds):
        raise ValueError("Build in progress: recheck after it completes")
    recent = builds[:20]
    by_id = {b["id"]: b for b in builds}
    if not live_ids or any(id not in by_id for id in live_ids):
        raise ValueError("Live release not present in build history")
    live_time = min(timestamp(by_id[id]["createTime"]) for id in live_ids)
    rollback = next((b for b in builds if b["status"] == "SUCCESS" and timestamp(b["createTime"]) < live_time), None)
    if rollback is None:
        raise ValueError("No successful predecessor available for rollback protection")
    protected = {b["id"] for b in recent} | set(live_ids) | {rollback["id"]}
    sources = {o["metadata"]["name"]: o["metadata"] for o in objects}
    protected_sources = []
    for id in protected:
        source = by_id[id].get("source", {}).get("storageSource", {})
        if source.get("bucket") != BUCKET or not source.get("object", "").startswith("source/"):
            raise ValueError(f"Unexpected source location for build {id}")
        obj = sources.get(source["object"])
        if obj is None or str(obj["generation"]) != str(source["generation"]):
            raise ValueError(f"Protected source missing or generation changed: {id}")
        protected_sources.append(obj)
        if by_id[id]["status"] == "SUCCESS" or id in live_ids or id == rollback["id"]:
            for package in ("backend", "frontend"):
                if not any(i["package"] == f"{REPOSITORY}/{package}" and id in i.get("tags", []) for i in images):
                    raise ValueError(f"Protected {package} image missing for {id}")
    cutoff = min(timestamp(o["timeCreated"]) for o in protected_sources).date().isoformat()
    proposed = {
        "artifact_cleanup_dry_run": True,
        "artifact_protected_build_ids": sorted(protected),
        "build_source_created_before": cutoff,
    }
    effective = settings or proposed
    pinned = set(effective["artifact_protected_build_ids"])
    if not protected <= pinned:
        raise ValueError("Settings do not protect all latest-20/live/rollback build IDs")
    boundary = timestamp(effective["build_source_created_before"] + "T00:00:00Z")
    if any(timestamp(o["timeCreated"]) < boundary for o in protected_sources):
        raise ValueError("Source cutoff would expose a protected build upload")
    recent_versions = set()
    for package in ("backend", "frontend"):
        rows = sorted([i for i in images if i["package"] == f"{REPOSITORY}/{package}"], key=lambda i: timestamp(i["createTime"]), reverse=True)
        recent_versions.update((i["package"], i["version"]) for i in rows[:20])
    image_candidates = [i for i in images if i["package"] in {f"{REPOSITORY}/backend", f"{REPOSITORY}/frontend"}
                        and (i["package"], i["version"]) not in recent_versions
                        and not any(tag.startswith(id) for tag in i.get("tags", []) for id in pinned)
                        and now - timestamp(i["createTime"]) > timedelta(days=30)]
    source_candidates = [o for o in sources.values() if o["name"].startswith("source/") and o["name"].endswith(".tgz")
                         and timestamp(o["timeCreated"]) < boundary
                         and now - timestamp(o["timeCreated"]) >= timedelta(days=30)]
    return {"proposed_tfvars": proposed, "latest_build_ids": [b["id"] for b in recent],
            "live_build_ids": sorted(live_ids), "rollback_build_id": rollback["id"],
            "protected_sources_verified": len(protected_sources),
            "image_delete_candidates": [{"package": i["package"], "version": i["version"]} for i in image_candidates],
            "source_delete_candidates": [o["name"] for o in source_candidates]}


def gcloud_json(*args):
    return json.loads(subprocess.check_output(["gcloud", *args, "--quiet", "--format=json"], text=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path)
    args = parser.parse_args()
    builds = gcloud_json("builds", "list", f"--project={PROJECT}", "--limit=1000")
    images = gcloud_json("artifacts", "docker", "images", "list", REPOSITORY, "--include-tags")
    objects = json.loads(subprocess.check_output(["gcloud", "storage", "ls", "--json", "--quiet", f"gs://{BUCKET}/source/*"], text=True))
    live = subprocess.check_output(["gcloud", "compute", "ssh", "bodha-prod", f"--project={PROJECT}", "--zone=asia-south1-a", "--tunnel-through-iap", "--quiet", "--command=sudo docker ps --format '{{.Image}}'"], text=True)
    live_ids = {line.strip().rsplit(":", 1)[1] for line in live.splitlines() if line.startswith(REPOSITORY + "/")}
    settings = json.loads(args.settings.read_text()) if args.settings else None
    print(json.dumps(preview(builds, images, objects, live_ids, settings), indent=2))


if __name__ == "__main__":
    main()
