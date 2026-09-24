"""One-time Firebase Authentication migration for existing Bodha users.

Run with --dry-run first. Existing local passwords are deliberately not copied:
their PBKDF2 cost is stronger than Firebase's supported import limit. Those
accounts receive a temporary unusable Firebase password and use the normal
Forgot password flow to choose a real one.
"""
import argparse
import asyncio
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import firebase_admin  # noqa: E402
from firebase_admin import auth as firebase_auth  # noqa: E402
from deps import db  # noqa: E402


def firebase_app():
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    return firebase_admin.get_app()


async def migrate(apply: bool) -> dict:
    users = await db.users.find({}, {"_id": 0}).to_list(None)
    report = {"total": len(users), "migrated": [], "skipped": [], "failed": []}
    for user in users:
        uid, email = user.get("id"), user.get("email")
        if not uid or not email:
            report["skipped"].append({"id": uid, "reason": "missing id or email"})
            continue
        try:
            if apply:
                try:
                    firebase_auth.get_user(uid, app=firebase_app())
                except firebase_auth.UserNotFoundError:
                    provider_data = []
                    if user.get("google_subject"):
                        provider_data.append(firebase_auth.UserProvider(
                            uid=str(user["google_subject"]), email=email,
                            display_name=user.get("full_name") or None,
                            provider_id="google.com",
                        ))
                    record = firebase_auth.ImportUserRecord(
                        uid=uid, email=email, email_verified=bool(user.get("google_subject")),
                        display_name=user.get("full_name") or None, provider_data=provider_data,
                        custom_claims={"role": user.get("role", "teacher")},
                    )
                    result = firebase_auth.import_users([record], app=firebase_app())
                    if result.failure_count:
                        raise RuntimeError(result.errors[0].reason)
                firebase_auth.set_custom_user_claims(uid, {"role": user.get("role", "teacher")}, app=firebase_app())
                if user.get("password_hash"):
                    firebase_auth.update_user(uid, password=secrets.token_urlsafe(48), app=firebase_app())
                await db.users.update_one({"id": uid}, {"$set": {"firebase_uid": uid, "auth_provider": "firebase"}})
            report["migrated"].append({"id": uid, "email": email, "requires_password_reset": bool(user.get("password_hash"))})
        except Exception as exc:  # noqa: BLE001
            report["failed"].append({"id": uid, "email": email, "reason": str(exc)})
    return report


def main():
    parser = argparse.ArgumentParser(description="Migrate Bodha user identities to Firebase Auth")
    parser.add_argument("--apply", action="store_true", help="perform writes; omitted means dry run")
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    report = asyncio.run(migrate(args.apply))
    text = json.dumps(report, indent=2)
    print(text)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
