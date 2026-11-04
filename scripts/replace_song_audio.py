import argparse
import json
import os
import sys
import time
from typing import Any

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient

try:
    import ipfshttpclient
except ImportError:
    ipfshttpclient = None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_artists(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_normalize_text(v) for v in value if _normalize_text(v)]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text.replace("'", '"'))
                if isinstance(parsed, list):
                    return [_normalize_text(v) for v in parsed if _normalize_text(v)]
            except Exception:
                pass
        return [_normalize_text(text)] if text else []
    return [_normalize_text(value)] if _normalize_text(value) else []


def _load_mapping(mapping_path: str) -> list[dict[str, Any]]:
    with open(mapping_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise ValueError("Mapping file must contain a JSON array.")
    return [row for row in payload if isinstance(row, dict)]


def _resolve_registered_song(registered_songs, row: dict[str, Any]) -> dict[str, Any] | None:
    song_id = str(row.get("song_id") or row.get("songId") or "").strip()
    spotify_id = str(row.get("spotify_id") or row.get("spotifyId") or "").strip()
    song_title = str(row.get("songTitle") or row.get("song_title") or "").strip()
    artist = str(row.get("artist") or row.get("artistName") or "").strip()
    object_id = str(row.get("_id") or "").strip()

    if object_id:
        try:
            return registered_songs.find_one({"_id": ObjectId(object_id)})
        except Exception:
            return None

    if spotify_id:
        doc = registered_songs.find_one({"spotify_id": spotify_id})
        if doc:
            return doc

    if song_id:
        doc = registered_songs.find_one({"song_id": song_id})
        if doc:
            return doc

    if not song_title:
        return None

    candidates = list(registered_songs.find({"songTitle": {"$regex": f"^{song_title}$", "$options": "i"}}).limit(20))
    if not candidates:
        return None
    if len(candidates) == 1 or not artist:
        return candidates[0]

    expected_artist = _normalize_text(artist)
    for candidate in candidates:
        names = _normalize_artists(candidate.get("artistNames"))
        if expected_artist in names:
            return candidate
    return candidates[0]


def _update_purchases(purchase_history, song_doc: dict[str, Any], new_cid: str, dry_run: bool) -> int:
    song_title = str(song_doc.get("songTitle") or "").strip()
    spotify_id = str(song_doc.get("spotify_id") or "").strip()
    song_id = str(song_doc.get("song_id") or "").strip()

    or_filters: list[dict[str, Any]] = []
    if spotify_id:
        or_filters.append({"spotify_id": spotify_id})
    if song_id:
        or_filters.append({"song_id": song_id})
    if song_title:
        or_filters.append({"song_title": {"$regex": f"^{song_title}$", "$options": "i"}})

    if not or_filters:
        return 0

    query = {"$or": or_filters}
    if dry_run:
        return purchase_history.count_documents(query)

    result = purchase_history.update_many(
        query,
        {
            "$set": {
                "song_file_cid": new_cid,
                "audio_url": f"http://127.0.0.1:5000/ipfs/file/{new_cid}",
                "storage": "ipfs",
                "audio_updated_at": int(time.time()),
                "audio_updated_by": "replace_song_audio.py",
            }
        },
    )
    return int(result.modified_count or 0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload full-length files to IPFS and replace song_file_cid for existing songs."
    )
    parser.add_argument("--mapping", required=True, help="Path to JSON mapping file.")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without writing to IPFS or Mongo.")
    parser.add_argument("--mongo-uri", default="", help="Mongo URI override (otherwise uses MONGO_URI env).")
    parser.add_argument("--ipfs-api", default="/ip4/127.0.0.1/tcp/5001/http", help="IPFS API multiaddr.")
    args = parser.parse_args()

    load_dotenv("spotify.env")
    mapping = _load_mapping(args.mapping)
    if not mapping:
        print("No rows found in mapping file.")
        return 1

    mongo_uri = args.mongo_uri or os.getenv("MONGO_URI") or "mongodb://localhost:27017/"
    mongo_client = MongoClient(mongo_uri)
    db = mongo_client["music_app"]
    registered_songs = db["registered_songs"]
    purchase_history = db["purchase_history"]

    ipfs_client = None
    if not args.dry_run:
        if ipfshttpclient is None:
            print("ipfshttpclient is not installed. Install it before running non-dry-run mode.")
            return 1
        ipfs_client = ipfshttpclient.connect(args.ipfs_api)

    processed = 0
    updated = 0
    failed = 0

    for row in mapping:
        processed += 1
        file_path = str(row.get("filePath") or row.get("file") or "").strip()
        if not file_path:
            print(f"[SKIP] Row {processed}: filePath is missing")
            failed += 1
            continue

        if not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)

        if not os.path.exists(file_path):
            print(f"[SKIP] Row {processed}: file not found -> {file_path}")
            failed += 1
            continue

        song_doc = _resolve_registered_song(registered_songs, row)
        if not song_doc:
            print(f"[SKIP] Row {processed}: no matching song document for mapping row")
            failed += 1
            continue

        title = str(song_doc.get("songTitle") or "<unknown>")
        old_cid = str(song_doc.get("song_file_cid") or "").strip()

        try:
            if args.dry_run:
                new_cid = "<dry-run-cid>"
                purchase_hits = _update_purchases(purchase_history, song_doc, new_cid, dry_run=True)
                print(
                    f"[DRY-RUN] {title}: would upload {os.path.basename(file_path)}, replace CID {old_cid or '-'} -> <new>, purchases matched={purchase_hits}"
                )
                updated += 1
                continue

            ipfs_result = ipfs_client.add(file_path)
            new_cid = str((ipfs_result or {}).get("Hash") or "").strip()
            if not new_cid:
                raise RuntimeError("IPFS returned empty CID")

            registered_songs.update_one(
                {"_id": song_doc["_id"]},
                {
                    "$set": {
                        "song_file_cid": new_cid,
                        "audio_updated_at": int(time.time()),
                        "audio_updated_by": "replace_song_audio.py",
                        "storage": "ipfs",
                    }
                },
            )

            purchases_updated = _update_purchases(purchase_history, song_doc, new_cid, dry_run=False)
            print(
                f"[OK] {title}: {old_cid or '-'} -> {new_cid} | purchases updated={purchases_updated} | file={os.path.basename(file_path)}"
            )
            updated += 1
        except Exception as error:
            print(f"[ERROR] {title}: {error}")
            failed += 1

    print("\nSummary")
    print(f"processed={processed} updated={updated} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())