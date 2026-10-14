"""Offline safety tests; no gcloud calls or resource changes."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID

from verify_build_retention import BUCKET, REPOSITORY, preview


class RetentionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.builds, self.images, self.objects = [], [], []
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for n in range(25):
            id = str(UUID(int=n + 1))
            created = (base + timedelta(days=n)).isoformat()
            source = {"bucket": BUCKET, "object": f"source/{n}.tgz", "generation": str(n + 1)}
            self.builds.append({"id": id, "createTime": created, "status": "SUCCESS", "source": {"storageSource": source}})
            self.objects.append({"metadata": {"name": source["object"], "generation": source["generation"], "timeCreated": created}})
            for package in ("backend", "frontend"):
                self.images.append({"package": f"{REPOSITORY}/{package}", "version": f"sha256:{n}", "tags": [id], "createTime": created})
        self.live = {self.builds[-1]["id"]}
        self.now = datetime(2026, 5, 1, tzinfo=timezone.utc)

    def run_preview(self, **kwargs):
        return preview(self.builds, self.images, self.objects, self.live, now=self.now, **kwargs)

    def test_latest_20_survive_even_when_older_than_30_days(self):
        result = self.run_preview()
        self.assertEqual(result["protected_sources_verified"], 20)
        self.assertEqual(len(result["image_delete_candidates"]), 10)
        self.assertEqual(result["source_delete_candidates"], [f"source/{n}.tgz" for n in range(5)])
        self.assertEqual(result["proposed_tfvars"]["build_source_created_before"], "2026-01-06")

    def test_live_and_rollback_outside_latest_20_are_protected(self):
        self.live = {self.builds[2]["id"]}
        result = self.run_preview()
        self.assertEqual(result["protected_sources_verified"], 22)
        self.assertEqual(result["proposed_tfvars"]["build_source_created_before"], "2026-01-02")
        candidates = {(i["package"], i["version"]) for i in result["image_delete_candidates"]}
        self.assertNotIn((f"{REPOSITORY}/backend", "sha256:2"), candidates)
        self.assertNotIn((f"{REPOSITORY}/frontend", "sha256:1"), candidates)

    def test_missing_source_fails_closed(self):
        self.objects.pop()
        with self.assertRaisesRegex(ValueError, "Protected source missing"):
            self.run_preview()

    def test_changed_generation_fails_closed(self):
        self.objects[-1]["metadata"]["generation"] = "999"
        with self.assertRaisesRegex(ValueError, "generation changed"):
            self.run_preview()

    def test_missing_successful_image_fails_closed(self):
        self.images.pop()
        with self.assertRaisesRegex(ValueError, "image missing"):
            self.run_preview()

    def test_cancelled_build_upload_still_protected(self):
        self.builds[-3]["status"] = "CANCELLED"
        id = self.builds[-3]["id"]
        self.images = [i for i in self.images if id not in i["tags"]]
        self.assertIn(id, self.run_preview()["proposed_tfvars"]["artifact_protected_build_ids"])

    def test_in_progress_build_fails_closed(self):
        self.builds[-1]["status"] = "WORKING"
        with self.assertRaisesRegex(ValueError, "Build in progress"):
            self.run_preview()

    def test_unsafe_cutoff_fails_closed(self):
        settings = copy.deepcopy(self.run_preview()["proposed_tfvars"])
        settings["build_source_created_before"] = "2026-01-07"
        with self.assertRaisesRegex(ValueError, "cutoff would expose"):
            self.run_preview(settings=settings)

    def test_missing_protection_pin_fails_closed(self):
        settings = copy.deepcopy(self.run_preview()["proposed_tfvars"])
        settings["artifact_protected_build_ids"].pop()
        with self.assertRaisesRegex(ValueError, "do not protect"):
            self.run_preview(settings=settings)

    def test_younger_objects_not_candidates(self):
        self.now = datetime(2026, 1, 26, tzinfo=timezone.utc)
        result = self.run_preview()
        self.assertEqual(result["image_delete_candidates"], [])
        self.assertEqual(result["source_delete_candidates"], [])


if __name__ == "__main__":
    unittest.main()
