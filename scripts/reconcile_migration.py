#!/usr/bin/env python3
"""Compare source/destination MongoDB and S3/GCS migration manifests."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from typing import Any

import boto3
from google.cloud import storage
from pymongo import MongoClient


def canonical(document: dict[str, Any]) -> str:
    clean = {key: value for key, value in document.items() if key != "_id"}
    return hashlib.sha256(
        json.dumps(clean, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def compare_mongo(source_uri: str, destination_uri: str, db_name: str, sample: int) -> list[str]:
    source = MongoClient(source_uri)[db_name]
    destination = MongoClient(destination_uri)[db_name]
    errors: list[str] = []
    names = sorted(set(source.list_collection_names()) | set(destination.list_collection_names()))
    for name in names:
        if name.startswith("system."):
            continue
        src_count = source[name].count_documents({})
        dst_count = destination[name].count_documents({})
        print(f"mongo {name}: source={src_count} destination={dst_count}")
        if src_count != dst_count:
            errors.append(f"{name}: count mismatch {src_count} != {dst_count}")
        ids = [row["_id"] for row in source[name].find({}, {"_id": 1}).limit(max(sample * 10, sample))]
        for object_id in random.sample(ids, min(sample, len(ids))):
            src = source[name].find_one({"_id": object_id})
            dst = destination[name].find_one({"_id": object_id})
            if dst is None or canonical(src) != canonical(dst):
                errors.append(f"{name}/{object_id}: document mismatch")
    return errors


def compare_objects(aws_region: str, source_bucket: str, destination_bucket: str, prefix: str) -> list[str]:
    s3 = boto3.client("s3", region_name=aws_region)
    gcs_bucket = storage.Client().bucket(destination_bucket)
    source: dict[str, int] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=source_bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            source[item["Key"]] = item["Size"]
    destination = {blob.name: blob.size or 0 for blob in gcs_bucket.list_blobs(prefix=prefix)}
    errors = []
    for key in sorted(set(source) | set(destination)):
        if key not in source:
            errors.append(f"object only in GCS: {key}")
        elif key not in destination:
            errors.append(f"object missing from GCS: {key}")
        elif source[key] != destination[key]:
            errors.append(f"object size mismatch: {key} ({source[key]} != {destination[key]})")
    print(f"objects: source={len(source)} destination={len(destination)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-mongo", required=True)
    parser.add_argument("--destination-mongo", required=True)
    parser.add_argument("--db-name", default="bodhiai")
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument("--source-bucket", required=True)
    parser.add_argument("--destination-bucket", required=True)
    parser.add_argument("--prefix", default="bodhiai/")
    args = parser.parse_args()
    errors = compare_mongo(args.source_mongo, args.destination_mongo, args.db_name, args.sample)
    errors += compare_objects(
        args.aws_region, args.source_bucket, args.destination_bucket, args.prefix
    )
    for error in errors:
        print(f"ERROR {error}")
    print(f"reconciliation complete: {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
