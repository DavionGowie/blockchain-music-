import os
import re

from dotenv import load_dotenv
from pymongo import MongoClient


def main() -> int:
    load_dotenv('spotify.env')
    mongo_uri = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
    client = MongoClient(mongo_uri)
    db = client['music_app']
    registered_songs = db['registered_songs']
    purchases = db['purchase_history']

    scanned = 0
    fixed = 0
    cleared = 0

    cursor = purchases.find(
        {'song_file_cid': {'$nin': ['', None]}},
        {'song_file_cid': 1, 'song_title': 1, 'spotify_id': 1}
    )

    for purchase in cursor:
        scanned += 1
        purchase_cid = str(purchase.get('song_file_cid') or '').strip()
        purchase_title = str(purchase.get('song_title') or '').strip()
        purchase_spotify_id = str(purchase.get('spotify_id') or '').strip()
        if not purchase_cid or not purchase_title:
            continue

        owner_doc = registered_songs.find_one({'song_file_cid': purchase_cid}, {'songTitle': 1})
        owner_title = str((owner_doc or {}).get('songTitle') or '').strip().lower()
        if not owner_title or owner_title == purchase_title.lower():
            continue

        replacement = None
        if purchase_spotify_id:
            replacement = registered_songs.find_one(
                {'spotify_id': purchase_spotify_id, 'song_file_cid': {'$nin': ['', None]}},
                sort=[('registered_at', -1)],
                projection={'song_file_cid': 1}
            )

        if not replacement:
            replacement = registered_songs.find_one(
                {
                    'songTitle': {'$regex': f'^{re.escape(purchase_title)}$', '$options': 'i'},
                    'song_file_cid': {'$nin': ['', None]}
                },
                sort=[('registered_at', -1)],
                projection={'song_file_cid': 1}
            )

        replacement_cid = str((replacement or {}).get('song_file_cid') or '').strip()
        if replacement_cid:
            purchases.update_one(
                {'_id': purchase['_id']},
                {
                    '$set': {
                        'song_file_cid': replacement_cid,
                        'audio_url': f'http://127.0.0.1:5000/ipfs/file/{replacement_cid}'
                    }
                }
            )
            fixed += 1
        else:
            purchases.update_one(
                {'_id': purchase['_id']},
                {'$set': {'song_file_cid': '', 'audio_url': ''}}
            )
            cleared += 1

    print({'scanned': scanned, 'fixed': fixed, 'cleared': cleared})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())