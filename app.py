import os
import secrets
import time
import hashlib
import json
import re
import socket
import sys
import shutil
import subprocess
import mimetypes
from urllib.parse import quote_plus
from flask import Flask, session, request, jsonify, make_response, send_from_directory, Response
from flask_cors import CORS
from pymongo import MongoClient
from dotenv import load_dotenv
import requests
try:
    import ipfshttpclient
except ImportError:
    ipfshttpclient = None
    print('Warning: ipfshttpclient is not installed. IPFS features will be disabled.')
from web3 import Web3
from web3.auto import w3 as auto_w3
from eth_account.messages import encode_defunct
from eth_account import Account
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from bson import ObjectId
import datetime


# Optional AWS S3 support for storing ID documents
try:
    import boto3
    boto3_available = True
except Exception:
    boto3 = None
    boto3_available = False

load_dotenv('spotify.env')
client_id = os.getenv("SPOTIFY_CLIENT_ID")
client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
if client_id and client_secret:
    print("Spotify credentials loaded from environment.")
else:
    print("[WARN] Spotify credentials are missing or incomplete.")

# MongoDB connection
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)
db = client['music_app']

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', secrets.token_urlsafe(32))
CORS(app)

# --- User Registration (username/password) ---

# User collection
users_collection = db['users']
ipfs_initialised_songs_collection = db['ipfs_initialised_songs']

# User registration endpoint
@app.route('/user/register', methods=['POST'])
def user_register():
    data = request.get_json(force=True)
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')
    if not username or not password or not email:
        return jsonify({'success': False, 'error': 'Username, password, and email required'}), 400
    if users_collection.find_one({'username': username}):
        return jsonify({'success': False, 'error': 'Username already exists'}), 400
    hashed_pw = generate_password_hash(password)
    users_collection.insert_one({'username': username, 'password': hashed_pw, 'email': email, 'is_admin': False})
    return jsonify({'success': True, 'message': 'User registered successfully'})

# User login endpoint
@app.route('/user/login', methods=['POST'])
def user_login():
    data = request.get_json(force=True)
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'}), 400
    user = users_collection.find_one({'username': username})
    if not user or not check_password_hash(user['password'], password):
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
    session['user_id'] = str(user['_id'])
    session['is_admin'] = user.get('is_admin', False)
    return jsonify({'success': True, 'username': username, 'is_admin': user.get('is_admin', False)})

# Promote user to admin (manual, for demo)
@app.route('/user/promote', methods=['POST'])
def promote_user():
    data = request.get_json(force=True)
    username = data.get('username')
    if not username:
        return jsonify({'success': False, 'error': 'Username required'}), 400
    result = users_collection.update_one({'username': username}, {'$set': {'is_admin': True}})
    if result.matched_count == 0:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    return jsonify({'success': True, 'message': f'User {username} promoted to admin'})



    # ...existing code...
@app.route('/reward/spotify_activity', methods=['POST'])
def reward_spotify_activity():
    data = request.get_json()
    wallet = data.get('wallet')
    spotify_user_id = data.get('spotify_user_id')
    activity_data = data.get('activity_data', {})  # e.g., {'songs_listened': 12}
    action_type = 'reward'
    # Validate backend->blockchain action
    valid, reason = is_valid_blockchain_action(wallet, spotify_user_id, action_type, activity_data)
    if not valid:
        return jsonify({'success': False, 'error': reason}), 400
    # Prepare on-chain proof (hash)
    proof_hash = generate_onchain_proof({
        'wallet': wallet,
        'spotify_user_id': spotify_user_id,
        'activity_data': activity_data
    })
    # Store reward record off-chain (DB)
    db['rewards'].insert_one({
        'wallet': wallet,
        'spotify_user_id': spotify_user_id,
        'activity_data': activity_data,
        'proof_hash': proof_hash,
        'timestamp': time.time()
    })
    # TODO: Trigger blockchain transaction (mint NFT, reward token, etc.)
    # This should be done securely, e.g., by preparing a tx for the user to sign, or using a backend wallet
    # For now, just return the proof hash
    return jsonify({'success': True, 'proof_hash': proof_hash, 'message': 'Reward processed (blockchain tx not implemented in this demo)'})
# =========================
# BACKEND VALIDATION FOR BLOCKCHAIN ACTIONS
# =========================
def is_valid_blockchain_action(wallet, spotify_user_id, action_type, activity_data):
    """
    Validate backend->blockchain actions to prevent abuse and fake reward farming.
    - Check if Spotify activity is real (e.g., not repeated, not too frequent)
    - Rate limit rewards per user
    - Only allow if mapping exists in DB
    """
    # Check mapping exists
    mapping = db['connected_artists'].find_one({'wallet': wallet, 'spotify_user_id': spotify_user_id})
    if not mapping:
        return False, 'Wallet not linked to Spotify account.'
    # Example: Check activity_data for minimum requirements
    if action_type == 'reward':
        # Example: Only allow 1 reward per 10 songs listened in last 24h
        recent_rewards = db['rewards'].count_documents({
            'wallet': wallet,
            'timestamp': {'$gt': time.time() - 24*3600}
        })
        if recent_rewards >= (activity_data.get('songs_listened', 0) // 10):
            return False, 'Reward rate limit exceeded.'
    return True, ''
# =========================
# SPOTIFY ARTIST DIRECTORY ENDPOINT
# =========================
@app.route("/spotify/artists", methods=["GET"])
def get_all_spotify_artists():
    """Return all unique artists and their content from blockchain and Spotify."""
    # Get all contract songs
    contract_songs = get_contract_songs()
    artists = {}
    # Collect unique artists from blockchain
    for song in contract_songs:
        artist_name = song.get("artist") or "Unknown Artist"
        if artist_name not in artists:
            artists[artist_name] = {
                "name": artist_name,
                "songs": [],
                "albums": set(),
                "playlists": set()
            }
        artists[artist_name]["songs"].append({
            "id": song.get("id"),
            "title": song.get("title"),
            "album": song.get("album", ""),
            "blockchain": True
        })
    # Optionally, fetch more from Spotify API for each artist (if not using mock)
    if not USE_MOCK_DATA:
        token = get_spotify_token()
        if token:
            headers = {'Authorization': f'Bearer {token}'}
            for artist_name in artists:
                # Search Spotify for artist
                resp = requests.get(f'https://api.spotify.com/v1/search?q={artist_name}&type=artist&limit=1', headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get('artists', {}).get('items', [])
                    if items:
                        artist_id = items[0]['id']
                        # Get top tracks
                        tracks_resp = requests.get(f'https://api.spotify.com/v1/artists/{artist_id}/top-tracks?market=US', headers=headers)
                        if tracks_resp.status_code == 200:
                            tracks = tracks_resp.json().get('tracks', [])
                            for t in tracks:
                                artists[artist_name]["songs"].append({
                                    "id": t['id'],
                                    "title": t['name'],
                                    "album": t['album']['name'],
                                    "blockchain": False
                                })
                                
                        # Get albums
                        albums_resp = requests.get(f'https://api.spotify.com/v1/artists/{artist_id}/albums?include_groups=album,single&market=US&limit=10', headers=headers)
                        if albums_resp.status_code == 200:
                            albums = albums_resp.json().get('items', [])
                            for a in albums:
                                artists[artist_name]["albums"].add(a['name'])
                        # Get playlists (not directly available, skip or use search)
    # Convert sets to lists
    for artist in artists.values():
        artist["albums"] = list(artist["albums"])
        artist["playlists"] = list(artist["playlists"])
    # Return as list
    return jsonify(list(artists.values()))
# === Link Spotify Artist Channel to Platform/User ===
from eth_account.messages import encode_defunct
from web3.auto import w3 as auto_w3

@app.route('/artist/connect_spotify', methods=['POST'])
def connect_spotify_artist():
    data = request.get_json()
    artist_id = data.get('artist_id')
    artist_name = data.get('artist_name')
    wallet = data.get('wallet')
    signature = data.get('signature')
    spotify_user_id = data.get('spotify_user_id')
    if not (artist_id and artist_name and wallet and signature and spotify_user_id):
        return jsonify({'success': False, 'error': 'Missing required fields.'}), 400

    # The message to sign (should be the same on frontend)
    message = f"Link Spotify account {spotify_user_id} to wallet {wallet}"
    message_hash = encode_defunct(text=message)
    try:
        recovered_address = auto_w3.eth.account.recover_message(message_hash, signature=signature)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Invalid signature: {str(e)}'}), 400
    if recovered_address.lower() != wallet.lower():
        return jsonify({'success': False, 'error': 'Signature does not match wallet address.'}), 400

    # Store the mapping in MongoDB
    db['connected_artists'].update_one(
        {'artist_id': artist_id, 'wallet': wallet},
        {'$set': {
            'artist_name': artist_name,
            'spotify_user_id': spotify_user_id,
            'connected_at': time.time(),
            'signature': signature
        }},
        upsert=True
    )
    return jsonify({'success': True, 'message': f"Artist '{artist_name}' connected to wallet {wallet}"})


# === Blockchain Investment and Download Endpoints ===
def serialize_transaction_request(tx):
    """Convert transaction numeric fields into JSON-safe string values."""
    if not isinstance(tx, dict):
        return tx

    serialized = {}
    for key, value in tx.items():
        if isinstance(value, (bytes, bytearray)):
            serialized[key] = Web3.to_hex(value)
        elif isinstance(value, int) and not isinstance(value, bool):
            serialized[key] = str(value)
        else:
            serialized[key] = value
    return serialized


def decode_song_tuple(song_tuple):
    """Decode SmartMusic.getSong() tuples across ABI versions.

    Supported shapes:
    - v2 (len >= 12): includes investmentPrice + feature flags
    - v1 (len >= 9): no dedicated investmentPrice/feature flags
    """
    song = list(song_tuple or [])
    if len(song) >= 12:
        return {
            'id': song[0],
            'title': song[1],
            'artist': song[2],
            'spotifyId': song[3],
            'owner': song[4],
            'downloadPrice': int(song[5]),
            'investmentPrice': int(song[6]),
            'fullRightsPrice': int(song[7]),
            'investmentToken': song[8],
            'investmentEnabled': bool(song[9]),
            'fullRightsEnabled': bool(song[10]),
            'exists': bool(song[11])
        }

    if len(song) >= 9:
        # Legacy contract tuple where index 6 is the only secondary price field
        # and no explicit enable flags are returned.
        legacy_secondary_price = int(song[6])
        return {
            'id': song[0],
            'title': song[1],
            'artist': song[2],
            'spotifyId': song[3],
            'owner': song[4],
            'downloadPrice': int(song[5]),
            'investmentPrice': legacy_secondary_price,
            'fullRightsPrice': legacy_secondary_price,
            'investmentToken': song[7],
            'investmentEnabled': True,
            'fullRightsEnabled': True,
            'exists': bool(song[8])
        }

    return None


@app.route('/marketplace/invest', methods=['POST'])
def invest_in_song():
    data = request.get_json()
    song_id = data.get('song_id')
    token_amount = data.get('token_amount')
    wallet = data.get('wallet')
    if not (song_id and token_amount and wallet):
        return jsonify({'success': False, 'error': 'Missing required fields.'}), 400
    if not SMART_MUSIC_CONTRACT:
        return jsonify({'success': False, 'error': 'Smart contract not connected.'}), 500
    try:
        song_tuple = SMART_MUSIC_CONTRACT.functions.getSong(int(song_id)).call()
        song = decode_song_tuple(song_tuple)
        if not song or not song.get('exists'):
            return jsonify({'success': False, 'error': 'Song does not exist on contract.'}), 404

        token_amount_int = int(token_amount)
        if token_amount_int <= 0:
            return jsonify({'success': False, 'error': 'Token amount must be greater than zero.'}), 400

        if not song.get('investmentEnabled', True):
            return jsonify({'success': False, 'error': 'Investment is disabled for this song.'}), 400

        investment_price_wei = int(song.get('investmentPrice') or 0)
        if investment_price_wei <= 0:
            return jsonify({'success': False, 'error': 'Invalid investment price for this song.'}), 400

        value_wei = (investment_price_wei * token_amount_int) + get_platform_royalty_fee()

        tx = SMART_MUSIC_CONTRACT.functions.investInSong(int(song_id), token_amount_int).build_transaction({
            'from': wallet,
            'nonce': w3.eth.get_transaction_count(wallet),
            'value': value_wei,
            'gas': 300000,
            'gasPrice': w3.eth.gas_price
        })
        return jsonify({'success': True, 'tx': serialize_transaction_request(tx)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/marketplace/buy_download', methods=['POST'])
def buy_download_rights():
    data = request.get_json()
    song_id = data.get('song_id')
    wallet = data.get('wallet')
    if not (song_id and wallet):
        return jsonify({'success': False, 'error': 'Missing required fields.'}), 400
    if not SMART_MUSIC_CONTRACT:
        return jsonify({'success': False, 'error': 'Smart contract not connected.'}), 500
    try:
        song_tuple = SMART_MUSIC_CONTRACT.functions.getSong(int(song_id)).call()
        song = decode_song_tuple(song_tuple)
        if not song or not song.get('exists'):
            return jsonify({'success': False, 'error': 'Song does not exist on contract.'}), 404

        download_price_wei = int(song.get('downloadPrice') or 0)
        if download_price_wei <= 0:
            return jsonify({'success': False, 'error': 'Invalid download price for this song.'}), 400

        tx = SMART_MUSIC_CONTRACT.functions.buyDownloadRights(int(song_id)).build_transaction({
            'from': wallet,
            'nonce': w3.eth.get_transaction_count(wallet),
            'value': download_price_wei + get_platform_royalty_fee(),
            'gas': 200000,
            'gasPrice': w3.eth.gas_price
        })
        return jsonify({'success': True, 'tx': serialize_transaction_request(tx)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/marketplace/buy_rights', methods=['POST'])
def buy_full_rights():
    data = request.get_json()
    song_id = data.get('song_id')
    wallet = data.get('wallet')
    if not (song_id and wallet):
        return jsonify({'success': False, 'error': 'Missing required fields.'}), 400
    if not SMART_MUSIC_CONTRACT:
        return jsonify({'success': False, 'error': 'Smart contract not connected.'}), 500
    try:
        song_tuple = SMART_MUSIC_CONTRACT.functions.getSong(int(song_id)).call()
        song = decode_song_tuple(song_tuple)
        if not song or not song.get('exists'):
            return jsonify({'success': False, 'error': 'Song does not exist on contract.'}), 404

        if not song.get('fullRightsEnabled', True):
            return jsonify({'success': False, 'error': 'Full rights purchase is disabled for this song.'}), 400

        full_rights_price_wei = int(song.get('fullRightsPrice') or 0)
        if full_rights_price_wei <= 0:
            return jsonify({'success': False, 'error': 'Invalid full rights price for this song.'}), 400

        tx = SMART_MUSIC_CONTRACT.functions.buyFullRights(int(song_id)).build_transaction({
            'from': wallet,
            'nonce': w3.eth.get_transaction_count(wallet),
            'value': full_rights_price_wei + get_platform_royalty_fee(),
            'gas': 200000,
            'gasPrice': w3.eth.gas_price
        })
        return jsonify({'success': True, 'tx': serialize_transaction_request(tx)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/marketplace/add_spotify_song', methods=['POST'])
def add_spotify_song():
    data = request.get_json()
    title = data.get('title')
    artist = data.get('artist')
    spotify_id = data.get('spotify_id')
    download_price = data.get('download_price', 0.00001)
    investment_price = data.get('investment_price', 0.00001)
    full_rights_price = data.get('full_rights_price', 0.00001)
    investment_enabled = bool(data.get('investment_enabled', True))
    full_rights_enabled = bool(data.get('full_rights_enabled', True))
    wallet = data.get('wallet')

    if not (title and artist and spotify_id and wallet):
        return jsonify({'success': False, 'error': 'Missing required fields.'}), 400

    if not SMART_MUSIC_CONTRACT:
        return jsonify({'success': False, 'error': 'Smart contract not connected.'}), 500

    try:
        # Convert AVAX to Wei
        download_price_wei = Web3.to_wei(download_price, 'ether')
        full_rights_price_wei = Web3.to_wei(full_rights_price, 'ether')

        # Prepare transaction
        tx = SMART_MUSIC_CONTRACT.functions.addSongFromSpotify(
            title, artist, spotify_id, download_price_wei, full_rights_price_wei
        ).build_transaction({
            'from': wallet,
            'nonce': w3.eth.get_transaction_count(wallet),
            'gas': 300000,
            'gasPrice': w3.eth.gas_price
        })
        # NOTE: Must sign and send the transaction from the frontend or a secure backend wallet
        # Here I just return the tx data for signing
        return jsonify({'success': True, 'tx': serialize_transaction_request(tx)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/marketplace/upload_spotify_song', methods=['POST'])
def upload_spotify_song():
    data = request.get_json()
    title = data.get('title')
    artist = data.get('artist')
    spotify_id = data.get('spotify_id')
    cover_art_url = data.get('cover_art_url') or data.get('album_image') or data.get('coverArtUrl')
    duration_ms = data.get('duration_ms')
    download_price = data.get('download_price', 0.00001)
    investment_price = data.get('investment_price', 0.00001)
    full_rights_price = data.get('full_rights_price', 0.00001)
    investment_enabled = bool(data.get('investment_enabled', True))
    full_rights_enabled = bool(data.get('full_rights_enabled', True))
    requester_wallet = data.get('requester_wallet')

    if not (title and artist and spotify_id):
        return jsonify({'success': False, 'error': 'Missing required fields.'}), 400

    if not ipfs_client:
        connect_ipfs_client()
    if not ipfs_client:
        return jsonify({'success': False, 'error': 'IPFS client is not available.'}), 500

    try:
        preview_url = str(data.get('preview_url') or '').strip()
        metadata_backfill = fetch_spotify_cover_metadata(title, artist, spotify_id) or fetch_itunes_cover_metadata(title, artist) or {}
        existing_song_doc = None
        matching_docs = registered_songs_collection.find(
            {
                '$or': [
                    {'spotify_id': spotify_id},
                    {'songTitle': title, 'artistNames': {'$in': [artist]}},
                    {'songTitle': title, 'artist': artist}
                ]
            }
        ).sort('registered_at', -1)

        for candidate_doc in matching_docs:
            candidate_song_file_cid = str(candidate_doc.get('song_file_cid') or '').strip()
            candidate_preview_url = str(candidate_doc.get('preview_url') or '').strip()
            if candidate_song_file_cid or candidate_preview_url:
                existing_song_doc = candidate_doc
                break

        if not cover_art_url:
            cover_art_url = (
                metadata_backfill.get('album_image') or
                metadata_backfill.get('cover_art_url') or
                (existing_song_doc.get('album_image') if existing_song_doc else '') or
                (existing_song_doc.get('cover_art_url') if existing_song_doc else '') or
                ''
            )
        if not duration_ms:
            duration_ms = metadata_backfill.get('duration_ms') or (existing_song_doc.get('duration_ms') if existing_song_doc else None)
        if not preview_url:
            preview_url = str(
                metadata_backfill.get('preview_url') or
                (existing_song_doc.get('preview_url') if existing_song_doc else '') or
                ''
            ).strip()

        # Keep song_file_cid reserved for genuine full-length audio uploads.
        # Do not promote Spotify/iTunes preview URLs into full-track storage.
        song_file_cid = str((existing_song_doc.get('song_file_cid') if existing_song_doc else '') or '').strip() or None

        metadata = {
            'title': title,
            'artist': artist,
            'spotify_id': spotify_id,
            'album_image': cover_art_url,
            'duration_ms': duration_ms,
            'preview_url': preview_url,
            'download_price': download_price,
            'investment_price': investment_price,
            'full_rights_price': full_rights_price,
            'investment_enabled': investment_enabled,
            'full_rights_enabled': full_rights_enabled,
            'requester_wallet': requester_wallet,
            'song_file_cid': song_file_cid,
            'source': 'spotify',
            'storage': 'ipfs',
            'uploaded_at': int(time.time())
        }

        ipfs_result = upload_json_to_ipfs(metadata)
        ipfs_cid = ipfs_result.get('Hash') if isinstance(ipfs_result, dict) else ipfs_result
        ipfs_cid = str(ipfs_cid or '').strip()
        if not ipfs_cid:
            raise Exception('IPFS upload succeeded but CID was empty')

        registered_songs_collection.insert_one({
            'songTitle': title,
            'artistNames': [artist],
            'spotify_id': spotify_id,
            'album_image': cover_art_url,
            'duration_ms': duration_ms,
            'preview_url': preview_url,
            'download_price': download_price,
            'investment_price': investment_price,
            'full_rights_price': full_rights_price,
            'investment_enabled': investment_enabled,
            'full_rights_enabled': full_rights_enabled,
            'requester_wallet': requester_wallet,
            'song_file_cid': song_file_cid,
            'ipfs_cid': ipfs_cid,
            'storage': 'ipfs',
            'source': 'spotify',
            'registered_at': int(time.time()),
            'status': 'pending',
            'reviewed_at': None,
            'reviewed_by': None,
            'review_reason': None
        })

        print(f"Spotify song metadata uploaded to IPFS: {title} by {artist} -> {ipfs_cid}")

        # Keep an explicit audit trail of initialization writes to IPFS.
        ipfs_initialised_songs_collection.update_one(
            {'spotify_id': spotify_id, 'requester_wallet': requester_wallet},
            {'$set': {
                'title': title,
                'artist': artist,
                'spotify_id': spotify_id,
                'album_image': cover_art_url,
                'duration_ms': duration_ms,
                'preview_url': preview_url,
                'song_file_cid': song_file_cid,
                'ipfs_cid': ipfs_cid,
                'ipfs_gateway_url': f'https://ipfs.io/ipfs/{ipfs_cid}',
                'storage': 'ipfs',
                'source': 'spotify',
                'requester_wallet': requester_wallet,
                'updated_at': int(time.time())
            }, '$setOnInsert': {'created_at': int(time.time())}},
            upsert=True
        )

        # Register song on smart contract if owner account and contract are available
        tx_hash = None
        song_id = None
        blockchain_error = None
        if OWNER_ACCOUNT and SMART_MUSIC_CONTRACT:
            try:
                # Convert prices to Wei
                download_price_wei = Web3.to_wei(download_price, 'ether')
                full_rights_price_wei = Web3.to_wei(full_rights_price, 'ether')

                # Build transaction
                tx = SMART_MUSIC_CONTRACT.functions.addSongFromSpotify(
                    title, artist, spotify_id, download_price_wei, full_rights_price_wei
                ).build_transaction({
                    'from': OWNER_ACCOUNT.address,
                    'nonce': w3.eth.get_transaction_count(OWNER_ACCOUNT.address),
                    'gas': 300000,
                    'gasPrice': w3.eth.gas_price
                })

                # Sign and send transaction
                signed_tx = w3.eth.account.sign_transaction(tx, OWNER_PRIVATE_KEY)
                raw_signed_tx = getattr(signed_tx, 'rawTransaction', None) or getattr(signed_tx, 'raw_transaction', None)
                if not raw_signed_tx:
                    raise Exception('Signed transaction payload missing (rawTransaction/raw_transaction not found)')

                tx_hash = w3.eth.send_raw_transaction(raw_signed_tx).hex()
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

                # Extract emitted song id from SongAdded event.
                try:
                    song_events = SMART_MUSIC_CONTRACT.events.SongAdded().process_receipt(receipt)
                    if song_events:
                        song_id = int(song_events[0]['args']['songId'])
                except Exception as event_err:
                    print(f"[WARN] Could not decode SongAdded event: {str(event_err)}")
                
                print(f"Song registered on smart contract: {title} by {artist} -> {tx_hash}")
                
                # Update MongoDB record with transaction hash
                registered_songs_collection.update_one(
                    {'ipfs_cid': ipfs_cid},
                    {'$set': {
                        'blockchain_tx_hash': tx_hash,
                        'blockchain_registered': True,
                        'blockchain_song_id': song_id
                    }}
                )

                # Fallback if event decode doesn't provide song id.
                if song_id is None:
                    try:
                        next_song_id = int(SMART_MUSIC_CONTRACT.functions.nextSongId().call())
                        if next_song_id > 0:
                            song_id = next_song_id - 1
                            registered_songs_collection.update_one(
                                {'ipfs_cid': ipfs_cid},
                                {'$set': {'blockchain_song_id': song_id}}
                            )
                    except Exception as fallback_err:
                        print(f"[WARN] Could not resolve song id from nextSongId fallback: {str(fallback_err)}")
            except Exception as e:
                print(f"[WARN] Failed to register song on smart contract: {str(e)}")
                # Don't fail the upload if contract registration fails
                tx_hash = None
                blockchain_error = str(e)

        response = {
            'success': True,
            'song_file_cid': song_file_cid,
            'preview_url': preview_url,
            'ipfs_cid': ipfs_cid,
            'ipfs_gateway_url': f'https://ipfs.io/ipfs/{ipfs_cid}',
            'storage': 'ipfs'
        }
        if tx_hash:
            response['tx_hash'] = tx_hash
            response['blockchain_registered'] = True
            if song_id is not None:
                response['song_id'] = song_id
        else:
            response['blockchain_registered'] = False
            if blockchain_error:
                response['blockchain_error'] = blockchain_error
            
        return jsonify(response)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _is_port_in_use(host, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, port)) == 0
    except Exception:
        return False


IPFS_API = os.getenv('IPFS_API', '/ip4/127.0.0.1/tcp/5001/http')
IPFS_BIN = os.getenv('IPFS_BIN', '').strip()

def _candidate_ipfs_binaries():
    candidates = []
    if IPFS_BIN:
        candidates.append(IPFS_BIN)
    discovered = shutil.which('ipfs')
    if discovered:
        candidates.append(discovered)
    candidates.extend([
        r'C:\Users\davio\Desktop\go-ipfs_0.7.0\go-ipfs\ipfs.exe',
        r'C:\Program Files\IPFS Desktop\resources\app.asar.unpacked\daemon\ipfs.exe',
        r'C:\Program Files\go-ipfs\ipfs.exe'
    ])
    seen = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            yield candidate

def _start_ipfs_daemon():
    if _is_port_in_use('127.0.0.1', 5001):
        return True

    for binary in _candidate_ipfs_binaries():
        try:
            if not os.path.exists(binary):
                continue
            subprocess.Popen(
                [binary, 'daemon'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=os.path.dirname(binary) or None,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            for _ in range(20):
                time.sleep(0.5)
                if _is_port_in_use('127.0.0.1', 5001):
                    return True
        except Exception as start_error:
            print('[WARN] Failed to start IPFS daemon from', binary, ':', start_error)
    return False

def connect_ipfs_client():
    global ipfs_client
    if ipfshttpclient is None:
        ipfs_client = None
        return False
    try:
        ipfs_client = ipfshttpclient.connect(IPFS_API)
        print('Connected to IPFS node:', IPFS_API)
        return True
    except Exception as first_error:
        print('Failed to connect to IPFS:', first_error)
        if _start_ipfs_daemon():
            try:
                ipfs_client = ipfshttpclient.connect(IPFS_API)
                print('Connected to IPFS node after daemon bootstrap:', IPFS_API)
                return True
            except Exception as second_error:
                print('Failed to connect to IPFS after bootstrap:', second_error)
        ipfs_client = None
        return False

try:
    connect_ipfs_client()
except Exception as e:
    ipfs_client = None
    print('Failed to initialize IPFS:', e)

# Cache repeated CID misses so we do not re-run expensive local/gateway lookups
# for every request while the same content is unavailable.
IPFS_MISS_CACHE = {}
IPFS_MISS_TTL_SECONDS = int(os.getenv('IPFS_MISS_TTL_SECONDS', '300'))
IPFS_WARN_CACHE = {}
IPFS_WARN_COOLDOWN_SECONDS = int(os.getenv('IPFS_WARN_COOLDOWN_SECONDS', '120'))


def _log_ipfs_warning(kind, cid, error, method='GET'):
    """Throttle repetitive IPFS warning logs; suppress noisy HEAD timeout warnings."""
    req_method = str(method or 'GET').upper()
    if req_method == 'HEAD':
        return

    now = time.time()
    key = f'{kind}:{cid}'
    next_allowed_at = IPFS_WARN_CACHE.get(key, 0)
    if now < next_allowed_at:
        return

    IPFS_WARN_CACHE[key] = now + IPFS_WARN_COOLDOWN_SECONDS
    print(f'[WARN] {kind} failed for {cid}: {error}')

# Utility: Upload file to IPFS
def upload_file_to_ipfs(filepath):
    if not ipfs_client:
        connect_ipfs_client()
    if not ipfs_client:
        raise Exception('IPFS client not available')
    res = ipfs_client.add(filepath)
    return res['Hash']


# Utility: Upload metadata (dict) to IPFS
def upload_json_to_ipfs(data):
    if not ipfs_client:
        connect_ipfs_client()
    if not ipfs_client:
        raise Exception('IPFS client not available')
    res = ipfs_client.add_json(data)
    return res


@app.route('/ipfs/file/<path:cid>', methods=['GET', 'HEAD'])
def stream_ipfs_file(cid):
    safe_cid = (cid or '').strip()
    if not safe_cid:
        return jsonify({'success': False, 'error': 'CID is required.'}), 400
    if len(safe_cid) > 120 or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' for ch in safe_cid):
        return jsonify({'success': False, 'error': 'Invalid CID format.'}), 400

    if not ipfs_client:
        connect_ipfs_client()

    now = time.time()
    miss_until = IPFS_MISS_CACHE.get(safe_cid)
    if miss_until and miss_until > now:
        return jsonify({'success': False, 'error': 'CID not found on IPFS (cached miss).'}), 404
    if miss_until and miss_until <= now:
        IPFS_MISS_CACHE.pop(safe_cid, None)

    try:
        filename_hint = request.args.get('filename', '').strip()
        guessed_mime, _ = mimetypes.guess_type(filename_hint)
        content_type = guessed_mime or 'audio/mpeg'
        request_method = request.method

        payload = None
        if ipfs_client:
            try:
                # Use shorter local timeout so unavailable content does not stall
                # requests for minutes.
                local_timeout_seconds = 4 if request_method == 'HEAD' else 8
                payload = ipfs_client.cat(safe_cid, timeout=local_timeout_seconds)
            except TypeError:
                payload = ipfs_client.cat(safe_cid)
            except Exception as local_error:
                _log_ipfs_warning('Local IPFS cat', safe_cid, local_error, request_method)

        # Avoid expensive gateway fetches for HEAD probes; repeated HEADs were
        # creating noisy fallback timeouts in logs.
        should_try_gateway = request.method != 'HEAD'
        if not payload and should_try_gateway:
            try:
                gateway_response = requests.get(f'https://ipfs.io/ipfs/{safe_cid}', timeout=6)
                if gateway_response.ok and gateway_response.content:
                    payload = gateway_response.content
                    content_type = gateway_response.headers.get('Content-Type') or content_type
            except Exception as gateway_error:
                _log_ipfs_warning('IPFS gateway fallback', safe_cid, gateway_error, request_method)

        if not payload:
            IPFS_MISS_CACHE[safe_cid] = time.time() + IPFS_MISS_TTL_SECONDS
            return jsonify({'success': False, 'error': 'CID not found on IPFS.'}), 404

        total_size = len(payload)
        range_header = request.headers.get('Range', '')
        if range_header.startswith('bytes='):
            range_spec = range_header.replace('bytes=', '', 1).strip()
            start_str, end_str = (range_spec.split('-', 1) + [''])[:2]
            try:
                if start_str == '':
                    # Suffix-byte-range-spec: bytes=-500
                    suffix_len = int(end_str)
                    if suffix_len <= 0:
                        raise ValueError('Invalid suffix range')
                    start = max(0, total_size - suffix_len)
                    end = total_size - 1
                else:
                    start = int(start_str)
                    end = int(end_str) if end_str else total_size - 1
                    if start < 0:
                        start = 0
                    if end >= total_size:
                        end = total_size - 1

                if start > end or start >= total_size:
                    return Response(status=416)

                chunk = payload[start:end + 1]
                return Response(
                    chunk,
                    206,
                    mimetype=content_type,
                    headers={
                        'Accept-Ranges': 'bytes',
                        'Content-Range': f'bytes {start}-{end}/{total_size}',
                        'Content-Length': str(len(chunk)),
                        'Cache-Control': 'public, max-age=86400'
                    }
                )
            except Exception:
                # Fall through to full response when range parsing fails.
                pass

        return Response(
            payload,
            mimetype=content_type,
            headers={
                'Cache-Control': 'public, max-age=86400',
                'Accept-Ranges': 'bytes',
                'Content-Length': str(total_size)
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to read CID from IPFS: {str(e)}'}), 404

# Collections
admin_contacts_collection = db['admin_contacts']
songs_collection = db['songs']
purchase_history_collection = db['purchase_history']
registered_songs_collection = db['registered_songs']
admin_sessions_collection = db['admin_sessions']
deleted_contract_songs_collection = db['deleted_contract_songs']
maintenance_jobs_collection = db['maintenance_jobs']


def resolve_registered_song_for_purchase(song_id, song_title=''):
    """Find the most relevant registered song document for a purchase."""
    try:
        song_id_int = int(song_id)
    except (TypeError, ValueError):
        song_id_int = None

    if song_id_int is not None:
        id_candidates = [song_id_int, str(song_id_int)]
        query = {
            '$or': [
                {'blockchain_song_id': {'$in': id_candidates}},
                {'song_id': {'$in': id_candidates}},
                {'songId': {'$in': id_candidates}}
            ]
        }
        candidates = list(registered_songs_collection.find(query).sort('registered_at', -1).limit(25))
        if candidates:
            def _score(doc):
                score = 0
                if str(doc.get('song_file_cid') or '').strip():
                    score += 100
                if str(doc.get('album_image') or doc.get('cover_art_url') or doc.get('coverArtUrl') or '').strip():
                    score += 40
                if doc.get('duration_ms'):
                    score += 20
                if str(doc.get('preview_url') or '').strip():
                    score += 10
                if str(doc.get('ipfs_cid') or '').strip():
                    score += 5
                return score

            candidates.sort(key=lambda d: (_score(d), int(d.get('registered_at') or 0)), reverse=True)
            return candidates[0]

    title = str(song_title or '').strip()
    if title:
        return registered_songs_collection.find_one(
            {'songTitle': {'$regex': f'^{re.escape(title)}$', '$options': 'i'}},
            sort=[('registered_at', -1)]
        )

    return None


def serialize_registered_song_document(song_document):
    def _json_safe(value):
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, dict):
            return {key: _json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_json_safe(item) for item in value]
        return value

    song = _json_safe(dict(song_document))
    song['id'] = str(song.get('_id', ''))
    song.pop('_id', None)
    if not song.get('status'):
        song['status'] = 'approved'
    return song


def dedupe_registered_song_documents(song_documents):
    deduped = []
    seen = set()

    for song in song_documents:
        title = str(song.get('songTitle') or song.get('title') or '').strip().lower()
        artists_value = song.get('artistNames') or song.get('artist') or []
        if isinstance(artists_value, list):
            artist_text = ','.join(str(item).strip().lower() for item in artists_value)
        else:
            artist_text = str(artists_value).strip().lower()

        song_file_cid = str(song.get('song_file_cid') or '').strip().lower()
        ipfs_cid = str(song.get('ipfs_cid') or '').strip().lower()
        blockchain_song_id = str(song.get('blockchain_song_id') or '').strip().lower()

        identity = (
            title,
            artist_text,
            song_file_cid or ipfs_cid or blockchain_song_id
        )

        if identity in seen:
            continue

        seen.add(identity)
        deduped.append(song)

    return deduped

# =========================
# ADMIN CONTACT FORM ENDPOINT
@app.route('/admin/contact', methods=['POST'])
def admin_contact():
    data = request.get_json()
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    message = data.get('message', '').strip()
    if not name or not email or not message:
        return jsonify({'success': False, 'error': 'All fields are required.'}), 400
    admin_contacts_collection.insert_one({
        'name': name,
        'email': email,
        'message': message,
        'timestamp': time.time()
    })
    return jsonify({'success': True, 'message': 'Your message has been sent to the admin.'})
# ADMIN AUTH
# Simple session token auth for admin dashboard access.
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'password')
SESSION_EXPIRY_SECONDS = 3600


def get_admin_token_from_request():
    # Prefer cookie, fallback to header or query param
    token = request.cookies.get('admin_token')
    if token:
        return token
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header.split(' ', 1)[1].strip()
    return request.args.get('admin_token')


def validate_admin_token(token):
    if not token:
        return False
    session = admin_sessions_collection.find_one({'token': token})
    if not session:
        return False
    if session['expires_at'] < time.time():
        admin_sessions_collection.delete_one({'token': token})
        return False
    return True


# =========================
# ID VERIFICATION ENDPOINTS
# =========================

UPLOAD_DIR = os.path.join(os.getcwd(), 'uploads', 'ids')
os.makedirs(UPLOAD_DIR, exist_ok=True)

id_verifications_collection = db['id_verifications']
audit_logs_collection = db['audit_logs']

# S3 configuration (optional)
S3_BUCKET = os.getenv('AWS_S3_BUCKET', '').strip()
S3_REGION = os.getenv('AWS_REGION', '').strip() or os.getenv('AWS_DEFAULT_REGION', '').strip()
if S3_BUCKET and boto3_available:
    s3_client = boto3.client('s3')
else:
    s3_client = None


@app.route('/id/submit', methods=['POST'])
def submit_id():
    # Accepts form-data: idType, idNumber (string), idDocument (file list).
    # ID verification uploads are stored on IPFS and are not linked to wallet addresses.
    if 'idDocument' not in request.files:
        return jsonify({'success': False, 'error': 'No idDocument file provided.'}), 400

    id_docs = request.files.getlist('idDocument')
    id_docs = [doc for doc in id_docs if doc and str(doc.filename or '').strip()]

    id_type = request.form.get('idType', '').strip().lower()
    id_number = request.form.get('idNumber', '').strip()

    allowed_id_types = {
        'passport',
        'drivers_license',
        'national_id',
        'cae',
        'ipi'
    }

    if not id_type:
        return jsonify({'success': False, 'error': 'idType is required.'}), 400

    if id_type not in allowed_id_types:
        return jsonify({'success': False, 'error': 'Invalid idType. Allowed values: passport, drivers_license, national_id, cae, ipi.'}), 400

    if not id_number:
        return jsonify({'success': False, 'error': 'idNumber is required.'}), 400

    if not id_docs:
        return jsonify({'success': False, 'error': 'No selected file.'}), 400

    if id_type == 'drivers_license':
        if len(id_docs) != 2:
            return jsonify({'success': False, 'error': 'drivers_license requires exactly 2 images (front and back).'}), 400
    else:
        if len(id_docs) != 1:
            return jsonify({'success': False, 'error': 'Selected idType requires exactly 1 document.'}), 400

    # retention/expiry
    retention_days = int(os.getenv('ID_RETENTION_DAYS', '30'))
    expires_at = time.time() + retention_days * 86400

    if not ipfs_client:
        connect_ipfs_client()
    if not ipfs_client:
        return jsonify({'success': False, 'error': 'IPFS client is not available. Start IPFS daemon and retry.'}), 500

    documents = []
    for id_doc in id_docs:
        filename = secure_filename(id_doc.filename)
        stored_name = f"{int(time.time())}_{secrets.token_hex(8)}_{filename}"
        filepath = os.path.join(UPLOAD_DIR, stored_name)
        try:
            id_doc.save(filepath)
        except Exception as e:
            return jsonify({'success': False, 'error': f'Failed to save file: {e}'}), 500

        ipfs_cid = None
        try:
            ipfs_cid = upload_file_to_ipfs(filepath)
        except Exception as ipfs_error:
            return jsonify({'success': False, 'error': f'Failed to upload ID document to IPFS: {ipfs_error}'}), 500

        # Remove local sensitive document once it is persisted to IPFS.
        try:
            os.remove(filepath)
            filepath = None
        except Exception:
            pass

        documents.append({
            'filename': stored_name,
            'original_filename': filename,
            'filepath': filepath,
            'ipfs_cid': ipfs_cid,
            'ipfs_gateway_url': f'https://ipfs.io/ipfs/{ipfs_cid}' if ipfs_cid else None,
            's3_key': None,
            's3_bucket': None
        })

    primary_doc = documents[0]

    record = {
        'id_type': id_type,
        'id_number': id_number,
        'user_address': None,
        # Legacy top-level document fields retained for backward compatibility.
        'filename': primary_doc.get('filename'),
        'original_filename': primary_doc.get('original_filename'),
        'filepath': primary_doc.get('filepath'),
        'ipfs_cid': primary_doc.get('ipfs_cid'),
        'ipfs_gateway_url': primary_doc.get('ipfs_gateway_url'),
        's3_key': primary_doc.get('s3_key'),
        's3_bucket': primary_doc.get('s3_bucket'),
        'storage': 'ipfs',
        'documents': documents,
        'status': 'pending',
        'submitted_at': time.time(),
        'submitted_at_iso': datetime.datetime.utcnow().isoformat(),
        'expires_at': expires_at
    }

    res = id_verifications_collection.insert_one(record)
    record_id = str(res.inserted_id)
    return jsonify({'success': True, 'status': 'pending', 'id': record_id})


@app.route('/id/status', methods=['GET'])
def id_status():
    id_number = request.args.get('idNumber')
    user_address = request.args.get('userAddress')
    query = {}
    if id_number:
        query['id_number'] = id_number
    if user_address:
        query['user_address'] = user_address
    if not query:
        return jsonify({'success': False, 'error': 'Provide idNumber or userAddress as query parameter.'}), 400
    rec = id_verifications_collection.find_one(query, {'filepath': 0})
    if not rec:
        return jsonify({'success': False, 'error': 'No verification record found.'}), 404
    # Convert ObjectId and datetime
    rec['id'] = str(rec.get('_id'))
    rec['id_type'] = str(rec.get('id_type') or 'national_id').strip().lower()
    if isinstance(rec.get('documents'), list):
        sanitized_docs = []
        for doc in rec.get('documents', []):
            if not isinstance(doc, dict):
                continue
            cleaned = dict(doc)
            cleaned.pop('filepath', None)
            sanitized_docs.append(cleaned)
        rec['documents'] = sanitized_docs
    rec.pop('_id', None)
    return jsonify({'success': True, 'record': rec})


@app.route('/id/approve', methods=['POST'])
def approve_id():
    # Admin-protected endpoint. Accepts either X-ADMIN-TOKEN (static) or admin session token via cookie/Authorization
    admin_token_header = request.headers.get('X-ADMIN-TOKEN')
    expected = os.getenv('ADMIN_TOKEN', '').strip()
    token = get_admin_token_from_request()
    # Validate either static admin token or admin session token
    if expected and admin_token_header and admin_token_header == expected:
        authorized = True
    elif token and validate_admin_token(token):
        authorized = True
    else:
        return jsonify({'success': False, 'error': 'Invalid admin token or session.'}), 401

    data = request.get_json(force=True)
    # allow approving by internal record id
    rec_id = data.get('id')
    id_number = data.get('idNumber')
    user_address = data.get('userAddress')
    query = {}
    if rec_id:
        try:
            query['_id'] = ObjectId(rec_id)
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid id format.'}), 400
    else:
        if not id_number and not user_address:
            return jsonify({'success': False, 'error': 'Provide id or idNumber or userAddress to approve.'}), 400
        if id_number:
            query['id_number'] = id_number
        if user_address:
            query['user_address'] = user_address

    update = {
        '$set': {
            'status': 'verified',
            'verified_at': time.time(),
            'verified_at_iso': datetime.datetime.utcnow().isoformat()
        }
    }
    result = id_verifications_collection.update_one(query, update)
    if result.matched_count == 0:
        return jsonify({'success': False, 'error': 'No matching record to approve.'}), 404

    # Audit log
    admin_user = 'ADMIN'
    token_session = get_admin_token_from_request()
    if token_session and validate_admin_token(token_session):
        s = admin_sessions_collection.find_one({'token': token_session})
        if s:
            admin_user = s.get('username', admin_user)
    try:
        audit_logs_collection.insert_one({
            'action': 'approve_id',
            'admin': admin_user,
            'target_query': query,
            'timestamp': time.time(),
            'note': f'Approved by {admin_user}'
        })
    except Exception as e:
        print('Failed to write audit log:', e)

    return jsonify({'success': True, 'message': 'ID record approved.'})



@app.route('/admin/id_submissions', methods=['GET'])
def admin_list_id_submissions():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({'status': 'error', 'message': 'Admin access required.'}), 403
    # pagination & search
    try:
        page = int(request.args.get('page', '1'))
        page_size = int(request.args.get('page_size', '10'))
    except Exception:
        page = 1
        page_size = 10
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()

    query = {}
    if q:
        query['$or'] = [{'id_number': {'$regex': q, '$options': 'i'}}, {'user_address': {'$regex': q, '$options': 'i'}}]
    if status:
        query['status'] = status

    total = id_verifications_collection.count_documents(query)
    cursor = id_verifications_collection.find(query).sort('submitted_at', -1).skip((page-1)*page_size).limit(page_size)
    docs = []
    for d in cursor:
        d['id'] = str(d.get('_id'))
        d.pop('_id', None)
        # don't expose raw filepath
        if d.get('filepath'):
            d['has_local_file'] = True
            d.pop('filepath', None)
        else:
            d['has_local_file'] = False
        d['has_s3'] = bool(d.get('s3_key'))
        d['has_ipfs'] = bool(d.get('ipfs_cid'))
        d['id_type'] = str(d.get('id_type') or 'national_id').strip().lower()
        if isinstance(d.get('documents'), list):
            sanitized_docs = []
            for doc in d.get('documents', []):
                if not isinstance(doc, dict):
                    continue
                sanitized_docs.append({
                    'filename': doc.get('filename'),
                    'original_filename': doc.get('original_filename'),
                    'ipfs_cid': doc.get('ipfs_cid'),
                    'ipfs_gateway_url': doc.get('ipfs_gateway_url'),
                    'has_local_file': bool(doc.get('filepath')),
                    'has_s3': bool(doc.get('s3_key'))
                })
            d['documents'] = sanitized_docs
        docs.append(d)
    return jsonify({'status': 'success', 'count': total, 'page': page, 'page_size': page_size, 'submissions': docs})


@app.route('/admin/id_file/<id_str>', methods=['GET'])
def admin_get_id_file(id_str):
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({'status': 'error', 'message': 'Admin access required.'}), 403
    try:
        rec = id_verifications_collection.find_one({'_id': ObjectId(id_str)})
        if not rec:
            return jsonify({'status': 'error', 'message': 'Not found'}), 404
        docs = rec.get('documents') if isinstance(rec.get('documents'), list) else []
        doc_index_raw = request.args.get('doc_index', '0')
        try:
            doc_index = int(doc_index_raw)
        except Exception:
            doc_index = 0

        selected_doc = None
        if docs:
            if doc_index < 0 or doc_index >= len(docs):
                return jsonify({'status': 'error', 'message': 'Invalid document index'}), 400
            selected_doc = docs[doc_index]

        target_doc = selected_doc or rec

        # IPFS-first download path for ID docs.
        cid = str(target_doc.get('ipfs_cid') or rec.get('ipfs_cid') or '').strip()
        if cid:
            filename_hint = str(target_doc.get('original_filename') or rec.get('original_filename') or '').strip()
            local_url = f'http://127.0.0.1:5000/ipfs/file/{cid}'
            if filename_hint:
                local_url += f'?filename={quote_plus(filename_hint)}'
            return jsonify({'status': 'success', 'url': local_url, 'cid': cid})

        # If stored in S3, redirect to a presigned URL (valid briefly)
        if target_doc.get('s3_key') and s3_client and S3_BUCKET:
            try:
                url = s3_client.generate_presigned_url('get_object', Params={'Bucket': target_doc.get('s3_bucket') or rec.get('s3_bucket'), 'Key': target_doc.get('s3_key')}, ExpiresIn=300)
                return jsonify({'status': 'success', 'url': url})
            except Exception as e:
                print('Presign failed', e)
                return jsonify({'status': 'error', 'message': 'Failed to generate download link'}), 500
        path = target_doc.get('filepath') or rec.get('filepath')
        if not path or not os.path.exists(path):
            return jsonify({'status': 'error', 'message': 'File not available'}), 404
        from flask import send_file
        return send_file(path, as_attachment=True, download_name=target_doc.get('original_filename') or rec.get('original_filename'))
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/id/reject', methods=['POST'])
def reject_id():
    # Admin-protected endpoint
    admin_token_header = request.headers.get('X-ADMIN-TOKEN')
    expected = os.getenv('ADMIN_TOKEN', '').strip()
    token = get_admin_token_from_request()
    if expected and admin_token_header and admin_token_header == expected:
        authorized = True
    elif token and validate_admin_token(token):
        authorized = True
    else:
        return jsonify({'success': False, 'error': 'Invalid admin token or session.'}), 401

    data = request.get_json(force=True)
    rec_id = data.get('id')
    reason = data.get('reason', '')
    if not rec_id:
        return jsonify({'success': False, 'error': 'Provide id to reject.'}), 400
    try:
        objid = ObjectId(rec_id)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid id format.'}), 400
    result = id_verifications_collection.update_one({'_id': objid}, {'$set': {'status': 'rejected', 'rejected_at': time.time(), 'rejected_reason': reason}})
    if result.matched_count == 0:
        return jsonify({'success': False, 'error': 'No matching record to reject.'}), 404
    # Audit log
    admin_user = 'ADMIN'
    token_session = get_admin_token_from_request()
    if token_session and validate_admin_token(token_session):
        s = admin_sessions_collection.find_one({'token': token_session})
        if s:
            admin_user = s.get('username', admin_user)
    try:
        audit_logs_collection.insert_one({'action': 'reject_id', 'admin': admin_user, 'target_id': rec_id, 'reason': reason, 'timestamp': time.time()})
    except Exception as e:
        print('Failed to write audit log:', e)
    return jsonify({'success': True, 'message': 'ID record rejected.'})


@app.route('/id/delete', methods=['POST'])
@app.route('/admin/id_delete', methods=['POST'])
def delete_id():
    # Admin-protected endpoint
    admin_token_header = request.headers.get('X-ADMIN-TOKEN')
    expected = os.getenv('ADMIN_TOKEN', '').strip()
    token = get_admin_token_from_request()
    if expected and admin_token_header and admin_token_header == expected:
        authorized = True
    elif token and validate_admin_token(token):
        authorized = True
    else:
        return jsonify({'success': False, 'error': 'Invalid admin token or session.'}), 401

    data = request.get_json(force=True)
    rec_id = data.get('id')
    hard_delete = bool(data.get('hard_delete', True))
    reason = str(data.get('reason') or '').strip()
    if not rec_id:
        return jsonify({'success': False, 'error': 'Provide id to delete.'}), 400

    try:
        objid = ObjectId(rec_id)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid id format.'}), 400

    rec = id_verifications_collection.find_one({'_id': objid})
    if not rec:
        return jsonify({'success': False, 'error': 'No matching record to delete.'}), 404

    # Remove local artifacts and unpin IPFS content when available.
    docs = rec.get('documents') if isinstance(rec.get('documents'), list) else [rec]
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        local_path = doc.get('filepath')
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except Exception as file_error:
                print('Local file delete failed', file_error)
        cid = str(doc.get('ipfs_cid') or '').strip()
        if cid and ipfs_client:
            try:
                ipfs_client.pin.rm(cid)
            except Exception as pin_error:
                print('IPFS unpin failed', pin_error)

    if hard_delete:
        id_verifications_collection.delete_one({'_id': objid})
    else:
        id_verifications_collection.update_one(
            {'_id': objid},
            {'$set': {'status': 'deleted', 'deleted_at': time.time(), 'deleted_reason': reason}}
        )

    # Audit log
    admin_user = 'ADMIN'
    token_session = get_admin_token_from_request()
    if token_session and validate_admin_token(token_session):
        s = admin_sessions_collection.find_one({'token': token_session})
        if s:
            admin_user = s.get('username', admin_user)
    try:
        audit_logs_collection.insert_one({
            'action': 'delete_id',
            'admin': admin_user,
            'target_id': rec_id,
            'reason': reason,
            'hard_delete': hard_delete,
            'timestamp': time.time()
        })
    except Exception as e:
        print('Failed to write audit log:', e)

    return jsonify({'success': True, 'message': 'ID record deleted.'})


def cleanup_expired_id_submissions(retention_days=None, admin_user='SYSTEM'):
    try:
        if retention_days is None:
            retention_days = int(os.getenv('ID_RETENTION_DAYS', '30'))
        retention_days = int(retention_days)
    except Exception:
        retention_days = int(os.getenv('ID_RETENTION_DAYS', '30'))
    cutoff = time.time() - retention_days * 86400
    expired = list(id_verifications_collection.find({'submitted_at': {'$lt': cutoff}, 'status': {'$ne': 'verified'}}))
    removed = 0
    for rec in expired:
        try:
            if rec.get('s3_key') and s3_client and rec.get('s3_bucket'):
                try:
                    s3_client.delete_object(Bucket=rec.get('s3_bucket'), Key=rec.get('s3_key'))
                except Exception as e:
                    print('S3 delete failed', e)
            if rec.get('filepath') and os.path.exists(rec.get('filepath')):
                try:
                    os.remove(rec.get('filepath'))
                except Exception as e:
                    print('Local file delete failed', e)
            id_verifications_collection.update_one({'_id': rec['_id']}, {'$set': {'status': 'expired', 'expired_at': time.time()}})
            audit_logs_collection.insert_one({'action': 'expire_id', 'admin': admin_user, 'target_id': str(rec['_id']), 'timestamp': time.time()})
            removed += 1
        except Exception as e:
            print('Cleanup error', e)
    return removed


@app.route('/admin/cleanup', methods=['POST'])
def admin_cleanup():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({'status': 'error', 'message': 'Admin access required.'}), 403
    try:
        retention_days = int(request.json.get('retention_days', os.getenv('ID_RETENTION_DAYS', '30')))
    except Exception:
        retention_days = int(os.getenv('ID_RETENTION_DAYS', '30'))
    admin_user = 'ADMIN'
    token_session = get_admin_token_from_request()
    if token_session and validate_admin_token(token_session):
        s = admin_sessions_collection.find_one({'token': token_session})
        if s:
            admin_user = s.get('username', admin_user)
    removed = cleanup_expired_id_submissions(retention_days=retention_days, admin_user=admin_user)
    return jsonify({'status': 'success', 'removed': removed})


# =========================
# SPOTIFY CONFIGURATION
# =========================
# NEVER expose client secret to frontend or logs!
# To get Spotify API credentials:
# 1. Go to https://developer.spotify.com/dashboard
# 2. Create an app
# 3. Copy Client ID and Client Secret
# 4. Set environment variables or edit the lines below

SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID', 'your_client_id_here')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET', 'your_client_secret_here')

# For testing without real credentials, set USE_MOCK_DATA = True.
# When Spotify credentials are available, the app will automatically use real API access.
USE_MOCK_DATA = (
    not SPOTIFY_CLIENT_ID
    or SPOTIFY_CLIENT_ID == 'your_client_id_here'
    or not SPOTIFY_CLIENT_SECRET
    or SPOTIFY_CLIENT_SECRET == 'your_client_secret_here'
)
print(f"[DEBUG] Spotify mode: {'MOCK' if USE_MOCK_DATA else 'LIVE'}")

SPOTIFY_ACCESS_TOKEN = None
SPOTIFY_TOKEN_EXPIRES_AT = 0
SPOTIFY_COVER_LOOKUP_CACHE = {}
IPFS_METADATA_COVER_CACHE = {}
ITUNES_COVER_LOOKUP_CACHE = {}
COVER_BACKFILL_MAX_LOOKUPS_PER_REQUEST = int(os.getenv('COVER_BACKFILL_MAX_LOOKUPS_PER_REQUEST', '3'))

def get_spotify_token(force_refresh=False):
    global SPOTIFY_ACCESS_TOKEN, SPOTIFY_TOKEN_EXPIRES_AT
    now = time.time()
    if (not force_refresh) and SPOTIFY_ACCESS_TOKEN and now < SPOTIFY_TOKEN_EXPIRES_AT:
        return SPOTIFY_ACCESS_TOKEN

    # Only backend can use client secret
    try:
        auth = requests.post(
            'https://accounts.spotify.com/api/token',
            {
                'grant_type': 'client_credentials',
                'client_id': SPOTIFY_CLIENT_ID,
                'client_secret': SPOTIFY_CLIENT_SECRET,
            },
            timeout=10
        )

        if auth.status_code == 200:
            body = auth.json()
            SPOTIFY_ACCESS_TOKEN = body['access_token']
            expires_in = int(body.get('expires_in', 3600))
            # Refresh one minute before expiry.
            SPOTIFY_TOKEN_EXPIRES_AT = now + max(60, expires_in - 60)
            print("[DEBUG] Spotify token acquired successfully")
            return SPOTIFY_ACCESS_TOKEN
        else:
            print(f"[ERROR] Spotify token request failed: {auth.status_code} - {auth.text}")
            return None
    except Exception as e:
        print(f"[ERROR] Spotify token request exception: {e}")
        return None


def requests_get_with_retries(url, headers=None, timeout=5, retries=3, backoff=0.5, params=None):
    """Perform a GET request with simple retry/backoff on timeout or transient network errors."""
    for attempt in range(retries):
        try:
            return requests.get(url, headers=headers, timeout=timeout, params=params)
        except requests.Timeout:
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
                continue
            raise
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
                continue
            raise


def store_remote_audio_to_ipfs(audio_url, suffix='.mp3'):
    source_url = str(audio_url or '').strip()
    if not source_url:
        return None

    response = requests_get_with_retries(source_url, timeout=20, retries=2, backoff=1.0)
    if not response.ok or not response.content:
        return None

    tmp_path = None
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(response.content)
            tmp.flush()
            tmp_path = tmp.name
        return upload_file_to_ipfs(tmp_path)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _extract_spotify_error_message(response):
    try:
        payload = response.json()
        if isinstance(payload, dict):
            err = payload.get('error')
            if isinstance(err, dict):
                return str(err.get('message') or err.get('status') or payload)
            if isinstance(err, str):
                return err
            return str(payload)
    except Exception:
        pass
    try:
        text = response.text or ''
        return text.strip()[:500] if text else 'Unknown Spotify API error'
    except Exception:
        return 'Unknown Spotify API error'


def fetch_spotify_cover_metadata(title, artist, spotify_id=''):
    """Best-effort Spotify lookup used to backfill missing artwork metadata."""
    title_text = str(title or '').strip()
    artist_text = str(artist or '').strip()
    spotify_id_text = str(spotify_id or '').strip()
    cache_key = f"{spotify_id_text.lower()}::{title_text.lower()}::{artist_text.lower()}"
    cached = SPOTIFY_COVER_LOOKUP_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if USE_MOCK_DATA:
        SPOTIFY_COVER_LOOKUP_CACHE[cache_key] = None
        return None

    token = get_spotify_token()
    if not token:
        SPOTIFY_COVER_LOOKUP_CACHE[cache_key] = None
        return None

    headers = {'Authorization': f'Bearer {token}'}

    def _extract_from_item(item):
        album = item.get('album') or {}
        images = album.get('images') or []
        image_url = images[0].get('url') if images else None
        if not image_url:
            return None
        return {
            'album_image': image_url,
            'cover_art_url': image_url,
            'duration_ms': item.get('duration_ms'),
            'preview_url': item.get('preview_url'),
            'album': album.get('name')
        }

    def _with_refresh(request_callable):
        response = request_callable(headers)
        if response.status_code == 401:
            refreshed = get_spotify_token(force_refresh=True)
            if refreshed:
                refreshed_headers = {'Authorization': f'Bearer {refreshed}'}
                response = request_callable(refreshed_headers)
        return response

    try:
        if spotify_id_text:
            def _track_req(h):
                return requests_get_with_retries(
                    f'https://api.spotify.com/v1/tracks/{spotify_id_text}',
                    headers=h,
                    timeout=4,
                    retries=1,
                    backoff=0.5
                )

            track_resp = _with_refresh(_track_req)
            if track_resp.status_code == 200:
                metadata = _extract_from_item(track_resp.json() or {})
                SPOTIFY_COVER_LOOKUP_CACHE[cache_key] = metadata
                return metadata
            if track_resp.status_code in (403, 404, 429):
                SPOTIFY_COVER_LOOKUP_CACHE[cache_key] = None
                return None

        query = f"track:{title_text} artist:{artist_text}".strip()
        if not title_text:
            query = f"{artist_text}".strip()
        if not query:
            SPOTIFY_COVER_LOOKUP_CACHE[cache_key] = None
            return None

        def _search_req(h):
            return requests_get_with_retries(
                'https://api.spotify.com/v1/search',
                headers=h,
                timeout=4,
                retries=1,
                backoff=0.5,
                params={
                    'q': query,
                    'type': 'track',
                    'limit': 5,
                    'offset': 0
                }
            )

        search_resp = _with_refresh(_search_req)
        if search_resp.status_code != 200:
            SPOTIFY_COVER_LOOKUP_CACHE[cache_key] = None
            return None

        payload = search_resp.json() or {}
        items = (payload.get('tracks') or {}).get('items') or []
        if not items:
            SPOTIFY_COVER_LOOKUP_CACHE[cache_key] = None
            return None

        target_artist = artist_text.lower()
        preferred_item = None
        for item in items:
            item_artists = [str((a or {}).get('name') or '').strip().lower() for a in (item.get('artists') or [])]
            if target_artist and any(target_artist in a or a in target_artist for a in item_artists if a):
                preferred_item = item
                break
        if preferred_item is None:
            preferred_item = items[0]

        metadata = _extract_from_item(preferred_item)
        SPOTIFY_COVER_LOOKUP_CACHE[cache_key] = metadata
        return metadata
    except Exception:
        SPOTIFY_COVER_LOOKUP_CACHE[cache_key] = None
        return None


def resolve_cover_from_ipfs_metadata(ipfs_cid):
    """Extract a cover URL from stored IPFS JSON metadata when available."""
    cid = str(ipfs_cid or '').strip()
    if not cid:
        return None
    if cid in IPFS_METADATA_COVER_CACHE:
        return IPFS_METADATA_COVER_CACHE[cid]

    if not ipfs_client:
        connect_ipfs_client()

    payload = None
    if ipfs_client:
        try:
            payload = ipfs_client.cat(cid)
        except Exception:
            payload = None

    if payload is None:
        try:
            gateway_resp = requests.get(f'https://ipfs.io/ipfs/{cid}', timeout=3)
            if gateway_resp.ok and gateway_resp.content:
                payload = gateway_resp.content
        except Exception:
            payload = None

    if payload is None:
        IPFS_METADATA_COVER_CACHE[cid] = None
        return None

    try:
        if isinstance(payload, bytes):
            text = payload.decode('utf-8', errors='ignore')
        else:
            text = str(payload)
        metadata = json.loads(text)
    except Exception:
        IPFS_METADATA_COVER_CACHE[cid] = None
        return None

    candidate = (
        metadata.get('album_image')
        or metadata.get('cover_art_url')
        or metadata.get('coverArtUrl')
        or metadata.get('image')
        or metadata.get('artwork')
        or metadata.get('thumbnail')
        or metadata.get('cover')
    )
    candidate = str(candidate or '').strip()
    if not candidate:
        IPFS_METADATA_COVER_CACHE[cid] = None
        return None

    if candidate.startswith('ipfs://'):
        normalized = candidate.replace('ipfs://', '').strip('/ ')
        candidate = f'https://ipfs.io/ipfs/{normalized}' if normalized else ''
    elif candidate and candidate.startswith('Qm') and len(candidate) >= 46:
        candidate = f'https://ipfs.io/ipfs/{candidate}'

    if not candidate:
        IPFS_METADATA_COVER_CACHE[cid] = None
        return None

    IPFS_METADATA_COVER_CACHE[cid] = candidate
    return candidate


def fetch_itunes_cover_metadata(title, artist):
    """Public fallback cover lookup when Spotify/IPFS metadata are unavailable."""
    title_text = str(title or '').strip()
    artist_text = str(artist or '').strip()
    cache_key = f"{title_text.lower()}::{artist_text.lower()}"
    if cache_key in ITUNES_COVER_LOOKUP_CACHE:
        return ITUNES_COVER_LOOKUP_CACHE[cache_key]

    if not title_text and not artist_text:
        ITUNES_COVER_LOOKUP_CACHE[cache_key] = None
        return None

    term = f"{title_text} {artist_text}".strip()
    try:
        response = requests.get(
            'https://itunes.apple.com/search',
            params={
                'term': term,
                'entity': 'song',
                'limit': 5
            },
            timeout=4
        )
        if response.status_code != 200:
            ITUNES_COVER_LOOKUP_CACHE[cache_key] = None
            return None

        payload = response.json() or {}
        results = payload.get('results') or []
        if not results:
            ITUNES_COVER_LOOKUP_CACHE[cache_key] = None
            return None

        target_title = title_text.lower()
        target_artist = artist_text.lower()

        def _score(item):
            item_title = str(item.get('trackName') or '').strip().lower()
            item_artist = str(item.get('artistName') or '').strip().lower()
            score = 0
            if target_title and item_title == target_title:
                score += 4
            elif target_title and target_title in item_title:
                score += 2
            if target_artist and item_artist == target_artist:
                score += 4
            elif target_artist and target_artist in item_artist:
                score += 2
            return score

        best = sorted(results, key=_score, reverse=True)[0]
        image = str(best.get('artworkUrl100') or best.get('artworkUrl60') or '').strip()
        if not image:
            ITUNES_COVER_LOOKUP_CACHE[cache_key] = None
            return None

        # Use higher-resolution variant when available.
        high_res = image.replace('100x100bb', '1000x1000bb').replace('60x60bb', '1000x1000bb')
        metadata = {
            'album_image': high_res,
            'cover_art_url': high_res,
            'duration_ms': int(best.get('trackTimeMillis') or 0) or None,
            'preview_url': best.get('previewUrl'),
            'album': best.get('collectionName')
        }
        ITUNES_COVER_LOOKUP_CACHE[cache_key] = metadata
        return metadata
    except Exception:
        ITUNES_COVER_LOOKUP_CACHE[cache_key] = None
        return None


def build_registered_song_cover_backfill(song_document, allow_external=True):
    """Resolve missing artwork for a registered song and return DB update fields."""
    if not song_document:
        return None, None

    current_image = (
        song_document.get('album_image')
        or song_document.get('cover_art_url')
        or song_document.get('coverArtUrl')
    )
    if current_image:
        return None, 'existing'

    title = song_document.get('songTitle') or song_document.get('title') or 'Unknown'
    if isinstance(song_document.get('artistNames'), list):
        artist_name = ', '.join([str(x) for x in song_document.get('artistNames') if x])
    else:
        artist_name = song_document.get('artist') or song_document.get('artistNames') or ''

    spotify_id = str(song_document.get('spotify_id') or song_document.get('id') or '').strip()
    update_fields = None
    source = None

    ipfs_image = resolve_cover_from_ipfs_metadata(song_document.get('ipfs_cid'))
    if ipfs_image:
        update_fields = {
            'album_image': ipfs_image,
            'cover_art_url': ipfs_image,
            'coverArtUrl': ipfs_image
        }
        source = 'ipfs'

    if update_fields is None and allow_external:
        spotify_metadata = fetch_spotify_cover_metadata(title, artist_name, spotify_id)
        if spotify_metadata and spotify_metadata.get('album_image'):
            image = spotify_metadata.get('album_image')
            update_fields = {
                'album_image': image,
                'cover_art_url': image,
                'coverArtUrl': image
            }
            if spotify_metadata.get('duration_ms'):
                update_fields['duration_ms'] = spotify_metadata.get('duration_ms')
            if spotify_metadata.get('preview_url'):
                update_fields['preview_url'] = spotify_metadata.get('preview_url')
            if spotify_metadata.get('album') and not song_document.get('album'):
                update_fields['album'] = spotify_metadata.get('album')
            source = 'spotify'

    if update_fields is None and allow_external:
        itunes_metadata = fetch_itunes_cover_metadata(title, artist_name)
        if itunes_metadata and itunes_metadata.get('album_image'):
            image = itunes_metadata.get('album_image')
            update_fields = {
                'album_image': image,
                'cover_art_url': image,
                'coverArtUrl': image
            }
            if itunes_metadata.get('duration_ms'):
                update_fields['duration_ms'] = itunes_metadata.get('duration_ms')
            if itunes_metadata.get('preview_url'):
                update_fields['preview_url'] = itunes_metadata.get('preview_url')
            if itunes_metadata.get('album') and not song_document.get('album'):
                update_fields['album'] = itunes_metadata.get('album')
            source = 'itunes'

    return update_fields, source


def persist_registered_song_cover_backfill(song_document, allow_external=True):
    """Resolve and persist missing artwork for a single registered song."""
    update_fields, source = build_registered_song_cover_backfill(song_document, allow_external=allow_external)
    if not update_fields:
        return {
            'updated': False,
            'source': source,
            'title': song_document.get('songTitle') or song_document.get('title') or 'Unknown'
        }

    try:
        registered_songs_collection.update_one(
            {'_id': song_document.get('_id')},
            {'$set': update_fields}
        )
        return {
            'updated': True,
            'source': source,
            'title': song_document.get('songTitle') or song_document.get('title') or 'Unknown',
            'album_image': update_fields.get('album_image')
        }
    except Exception:
        return {
            'updated': False,
            'source': source,
            'title': song_document.get('songTitle') or song_document.get('title') or 'Unknown'
        }


def build_registered_song_audio_backfill(song_document):
    """Resolve missing song_file_cid/preview_url from matching Spotify records."""
    if not song_document:
        return None, None

    current_song_file_cid = str(song_document.get('song_file_cid') or '').strip()
    current_preview_url = str(song_document.get('preview_url') or '').strip()
    if current_song_file_cid and current_preview_url:
        return None, 'existing'

    title = str(song_document.get('songTitle') or song_document.get('title') or '').strip()
    spotify_id = str(song_document.get('spotify_id') or song_document.get('spotifyId') or '').strip()
    artists_value = song_document.get('artistNames') or song_document.get('artist') or []
    if isinstance(artists_value, list):
        artist_candidates = [str(item).strip() for item in artists_value if str(item).strip()]
    else:
        artist_candidates = [str(artists_value).strip()] if str(artists_value).strip() else []

    candidate_query = {'_id': {'$ne': song_document.get('_id')}}
    candidate_filters = []
    if spotify_id:
        candidate_filters.append({'spotify_id': spotify_id})
    if title and artist_candidates:
        candidate_filters.append({'songTitle': title, 'artistNames': {'$in': artist_candidates}})
        candidate_filters.append({'songTitle': title, 'artist': {'$in': artist_candidates}})
    elif title:
        candidate_filters.append({'songTitle': title})

    if not candidate_filters:
        return None, 'no-match'

    candidate_query['$or'] = candidate_filters
    matching_docs = registered_songs_collection.find(candidate_query).sort('registered_at', -1)

    donor_doc = None
    for candidate_doc in matching_docs:
        donor_song_file_cid = str(candidate_doc.get('song_file_cid') or '').strip()
        donor_preview_url = str(candidate_doc.get('preview_url') or '').strip()
        if donor_song_file_cid or donor_preview_url:
            donor_doc = candidate_doc
            break

    if donor_doc is None:
        return None, 'no-match'

    update_fields = {}
    donor_song_file_cid = str(donor_doc.get('song_file_cid') or '').strip()
    donor_preview_url = str(donor_doc.get('preview_url') or '').strip()
    if donor_song_file_cid and not current_song_file_cid:
        update_fields['song_file_cid'] = donor_song_file_cid
    if donor_preview_url and not current_preview_url:
        update_fields['preview_url'] = donor_preview_url

    if not update_fields:
        return None, 'existing'

    update_fields['audio_backfill_source_id'] = str(donor_doc.get('_id'))
    update_fields['audio_backfill_updated_at'] = int(time.time())
    return update_fields, 'matched-record'


def persist_registered_song_audio_backfill(song_document):
    update_fields, source = build_registered_song_audio_backfill(song_document)
    if not update_fields:
        return {
            'updated': False,
            'source': source,
            'title': song_document.get('songTitle') or song_document.get('title') or 'Unknown'
        }

    try:
        registered_songs_collection.update_one(
            {'_id': song_document.get('_id')},
            {'$set': update_fields}
        )
        return {
            'updated': True,
            'source': source,
            'title': song_document.get('songTitle') or song_document.get('title') or 'Unknown',
            'song_file_cid': update_fields.get('song_file_cid') or '',
            'preview_url': update_fields.get('preview_url') or ''
        }
    except Exception:
        return {
            'updated': False,
            'source': source,
            'title': song_document.get('songTitle') or song_document.get('title') or 'Unknown'
        }


def run_song_audio_backfill(limit=1000):
    query = {
        '$and': [
            {'source': 'spotify'},
            {'$or': [{'status': 'approved'}, {'status': {'$exists': False}}]},
            {'$or': [
                {'song_file_cid': {'$exists': False}},
                {'song_file_cid': None},
                {'song_file_cid': ''}
            ]}
        ]
    }

    docs = list(registered_songs_collection.find(query).sort('registered_at', -1).limit(limit))
    processed = []
    updated_count = 0
    skipped_count = 0
    for song_document in docs:
        result = persist_registered_song_audio_backfill(song_document)
        processed.append({
            'id': str(song_document.get('_id')),
            'title': result.get('title'),
            'updated': result.get('updated', False),
            'source': result.get('source'),
            'song_file_cid': result.get('song_file_cid') or '',
            'preview_url': result.get('preview_url') or ''
        })
        if result.get('updated'):
            updated_count += 1
        else:
            skipped_count += 1

    return {
        'processed_count': len(processed),
        'updated_count': updated_count,
        'skipped_count': skipped_count,
        'songs': processed
    }


def run_one_off_song_audio_backfill_migration():
    migration_key = 'backfill_song_audio_v1'
    existing_marker = maintenance_jobs_collection.find_one({'job_key': migration_key, 'completed': True})
    if existing_marker:
        return {'status': 'skipped', 'reason': 'already-completed'}

    result = run_song_audio_backfill(limit=1000)
    maintenance_jobs_collection.update_one(
        {'job_key': migration_key},
        {'$set': {
            'completed': True,
            'completed_at': int(time.time()),
            'result': {
                'processed_count': result.get('processed_count', 0),
                'updated_count': result.get('updated_count', 0),
                'skipped_count': result.get('skipped_count', 0)
            }
        }},
        upsert=True
    )
    print(f"[MIGRATION] {migration_key}: updated={result.get('updated_count', 0)} skipped={result.get('skipped_count', 0)} processed={result.get('processed_count', 0)}")
    return {'status': 'completed', **result}

# Utility: Generate on-chain proof (hash) for storing minimal data on-chain
import hashlib
def generate_onchain_proof(data_dict):
    # Only store hash on-chain, keep full data off-chain (IPFS/DB)
    data_str = str(sorted(data_dict.items()))
    return hashlib.sha256(data_str.encode()).hexdigest()

# =========================
# AVALANCHE CONNECTION
# =========================
w3_rpc = os.getenv('RPC', '').strip() or os.getenv('ETH_RPC_URL', '').strip()
if not w3_rpc:
    # Default to Avalanche Fuji testnet RPC
    w3_rpc = 'https://api.avax-test.network/ext/bc/C/rpc'
RPC = w3_rpc
w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={'timeout': 10}))

RPC_FALLBACKS = [
    'https://api.avax-test.network/ext/bc/C/rpc',
    'https://avalanche-fuji-c-chain.publicnode.com',
    'https://rpc.ankr.com/avalanche_fuji',
    'http://127.0.0.1:8545',
]
_LAST_BLOCKCHAIN_INIT_ATTEMPT = 0
_BLOCKCHAIN_RETRY_COOLDOWN_SEC = 15

OWNER_PRIVATE_KEY = os.getenv('OWNER_PRIVATE_KEY', '').strip()
OWNER_ACCOUNT = None
if OWNER_PRIVATE_KEY and len(OWNER_PRIVATE_KEY) >= 64:
    try:
        OWNER_ACCOUNT = Account.from_key(OWNER_PRIVATE_KEY)
        print("Owner account loaded:", OWNER_ACCOUNT.address)
    except Exception as e:
        print("[WARN] Invalid OWNER_PRIVATE_KEY format (ignored):", str(e)[:50])
elif OWNER_PRIVATE_KEY:
    print("[WARN] OWNER_PRIVATE_KEY is set but too short. Smart contract registration disabled.")

SMART_MUSIC_ADDRESS = os.getenv('SMART_MUSIC_ADDRESS', '').strip()
SMART_MUSIC_CONTRACT = None
SMART_MUSIC_ADDRESS_CHECKSUM = ''

if not SMART_MUSIC_ADDRESS:
    print("[WARN] SMART_MUSIC_ADDRESS is not set. Marketplace and contract functions will be disabled.")
elif not (SMART_MUSIC_ADDRESS.startswith('0x') and len(SMART_MUSIC_ADDRESS) == 42):
    print("[WARN] SMART_MUSIC_ADDRESS format is invalid. Expected 0x + 40 hex chars.")
else:
    try:
        SMART_MUSIC_ADDRESS_CHECKSUM = Web3.to_checksum_address(SMART_MUSIC_ADDRESS)
    except Exception:
        print("[WARN] SMART_MUSIC_ADDRESS could not be converted to checksum format.")

SMART_MUSIC_ABI = [
    {
        "inputs": [],
        "name": "nextSongId",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "uint256", "name": "_songId", "type": "uint256"}],
        "name": "getSong",
        "outputs": [
            {"internalType": "uint256", "name": "id", "type": "uint256"},
            {"internalType": "string", "name": "title", "type": "string"},
            {"internalType": "string", "name": "artist", "type": "string"},
            {"internalType": "string", "name": "spotifyId", "type": "string"},
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "uint256", "name": "downloadPrice", "type": "uint256"},
            {"internalType": "uint256", "name": "fullRightsPrice", "type": "uint256"},
            {"internalType": "address", "name": "investmentToken", "type": "address"},
            {"internalType": "bool", "name": "exists", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "string", "name": "_title", "type": "string"},
            {"internalType": "string", "name": "_artist", "type": "string"},
            {"internalType": "string", "name": "_spotifyId", "type": "string"},
            {"internalType": "uint256", "name": "_downloadPrice", "type": "uint256"},
            {"internalType": "uint256", "name": "_fullRightsPrice", "type": "uint256"}
        ],
        "name": "addSongFromSpotify",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "uint256", "name": "_songId", "type": "uint256"}],
        "name": "buyDownloadRights",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "_songId", "type": "uint256"},
            {"internalType": "uint256", "name": "_tokenAmount", "type": "uint256"}
        ],
        "name": "investInSong",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "uint256", "name": "_songId", "type": "uint256"}],
        "name": "buyFullRights",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "_songId", "type": "uint256"},
            {"internalType": "string", "name": "_tokenName", "type": "string"},
            {"internalType": "string", "name": "_tokenSymbol", "type": "string"}
        ],
        "name": "createInvestmentToken",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "songId", "type": "uint256"},
            {"indexed": False, "internalType": "string", "name": "title", "type": "string"},
            {"indexed": True, "internalType": "address", "name": "owner", "type": "address"}
        ],
        "name": "SongAdded",
        "type": "event"
    }
]

def _candidate_rpcs():
    candidates = []
    for value in [os.getenv('RPC', ''), os.getenv('ETH_RPC_URL', ''), RPC]:
        rpc = (value or '').strip()
        if rpc:
            candidates.append(rpc)
    candidates.extend(RPC_FALLBACKS)

    # Preserve order while removing duplicates.
    deduped = []
    seen = set()
    for rpc in candidates:
        if rpc not in seen:
            seen.add(rpc)
            deduped.append(rpc)
    return deduped


def initialize_blockchain_connection(force=False):
    global w3, RPC, SMART_MUSIC_CONTRACT, SMART_MUSIC_ADDRESS, _LAST_BLOCKCHAIN_INIT_ATTEMPT

    now = time.time()
    if not force and (now - _LAST_BLOCKCHAIN_INIT_ATTEMPT) < _BLOCKCHAIN_RETRY_COOLDOWN_SEC:
        return bool(w3 and w3.is_connected())

    _LAST_BLOCKCHAIN_INIT_ATTEMPT = now
    SMART_MUSIC_CONTRACT = None

    selected_rpc = None
    selected_w3 = None

    for rpc_url in _candidate_rpcs():
        try:
            candidate = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 10}))
            if candidate.is_connected():
                selected_rpc = rpc_url
                selected_w3 = candidate
                break
        except Exception as e:
            print(f"[WARN] RPC probe failed for {rpc_url}: {str(e)[:120]}")

    if selected_w3 is None:
        print("[WARN] Blockchain RPC unavailable. Tried:", ", ".join(_candidate_rpcs()))
        return False

    w3 = selected_w3
    RPC = selected_rpc
    print("Blockchain connected:", True, "via", RPC)

    if SMART_MUSIC_ADDRESS_CHECKSUM:
        try:
            contract_code = w3.eth.get_code(SMART_MUSIC_ADDRESS_CHECKSUM)
            if not contract_code or contract_code in (b'', b'0x'):
                print("[WARN] SMART_MUSIC_ADDRESS points to an EOA or empty address, not a deployed contract:", SMART_MUSIC_ADDRESS_CHECKSUM)
            else:
                SMART_MUSIC_CONTRACT = w3.eth.contract(address=SMART_MUSIC_ADDRESS_CHECKSUM, abi=SMART_MUSIC_ABI)
                SMART_MUSIC_ADDRESS = SMART_MUSIC_ADDRESS_CHECKSUM
                print("SmartMusic contract loaded:", SMART_MUSIC_ADDRESS)
        except Exception as e:
            print("Failed to load SmartMusic contract:", e)

    return True


def ensure_blockchain_ready(require_contract=True):
    connected = bool(w3 and w3.is_connected())
    contract_loaded = SMART_MUSIC_CONTRACT is not None
    if connected and ((not require_contract) or contract_loaded):
        return True

    initialize_blockchain_connection(force=False)
    connected = bool(w3 and w3.is_connected())
    if require_contract:
        return connected and SMART_MUSIC_CONTRACT is not None
    return connected


print("Blockchain connected:", bool(w3 and w3.is_connected()))
if not (w3 and w3.is_connected()):
    initialize_blockchain_connection(force=True)
elif SMART_MUSIC_ADDRESS_CHECKSUM:
    # Attempt a contract bind immediately when RPC was reachable at startup.
    initialize_blockchain_connection(force=True)


def from_wei(wei_value):
    try:
        return float(w3.from_wei(wei_value, "ether"))
    except Exception:
        return 0.0


def get_platform_royalty_fee():
    if not ensure_blockchain_ready(require_contract=True):
        return 0
    try:
        return int(SMART_MUSIC_CONTRACT.functions.platformRoyaltyFee().call())
    except Exception:
        return 0


def is_contract_song_blocked(song_id):
    try:
        song_id_int = int(song_id)
    except Exception:
        return False

    try:
        return deleted_contract_songs_collection.find_one({
            'song_id': song_id_int,
            'deleted': True
        }) is not None
    except Exception:
        return False



def get_song_metadata(song_id):
    if not ensure_blockchain_ready(require_contract=True):
        return None
    try:
        song_id = int(song_id)
        if is_contract_song_blocked(song_id):
            return None
        song_tuple = SMART_MUSIC_CONTRACT.functions.getSong(song_id).call()
        song = decode_song_tuple(song_tuple)
        if not song:
            return None
        return {
            "id": song.get("id"),
            "title": song.get("title"),
            "artist": song.get("artist"),
            "spotifyId": song.get("spotifyId"),
            "owner": song.get("owner"),
            "downloadPrice": song.get("downloadPrice"),
            "investmentPrice": song.get("investmentPrice"),
            "fullRightsPrice": song.get("fullRightsPrice"),
            "investmentToken": song.get("investmentToken"),
            "investmentEnabled": song.get("investmentEnabled", True),
            "fullRightsEnabled": song.get("fullRightsEnabled", True),
            "exists": song.get("exists", False)
        }
    except Exception as e:
        print(f"Error fetching song metadata for song {song_id}:", e)
        return None


def get_contract_songs():
    if not ensure_blockchain_ready(require_contract=True):
        return []
    try:
        next_id = SMART_MUSIC_CONTRACT.functions.nextSongId().call()
        songs = []
        for song_id in range(1, int(next_id)):
            if is_contract_song_blocked(song_id):
                continue
            metadata = get_song_metadata(song_id)
            if metadata and metadata.get('exists'):
                songs.append(metadata)
        return songs
    except Exception as e:
        print("Error loading contract song list:", e)
        return []

# =========================
# ANALYSIS DATA STORAGE
# =========================
# Now using MongoDB for persistent storage
streaming_data = {
    "Artist A": {"streams": 12500, "trend": 15, "top_track": "Song Alpha"},
    "Artist B": {"streams": 8300, "trend": -5, "top_track": "Song Beta"},
    "Davion Gowie": {"streams": 20000, "trend": 30, "top_track": "Song One"}
}

# =========================
# ANALYSIS ENDPOINTS
# =========================

@app.route("/analysis/sales", methods=["GET"])
def get_sales_analysis():
    """Get sales analysis data from purchase history and contract metadata"""
    purchases = list(purchase_history_collection.find())
    if not purchases:
        contract_songs = get_contract_songs()
        if contract_songs:
            top_song = contract_songs[0]
            return jsonify({
                "total_revenue": "0.00",
                "currency": "AVAX",
                "top_selling_song": {
                    "title": top_song["title"],
                    "revenue": "0.00"
                },
                "top_album": {
                    "title": "No sales yet",
                    "revenue": "0.00"
                },
                "recent_transactions": []
            })

        return jsonify({
            "total_revenue": "1.25",
            "currency": "AVAX",
            "top_selling_song": {
                "title": "Song One",
                "revenue": "0.50"
            },
            "top_album": {
                "title": "Fresh Album",
                "revenue": "0.75"
            },
            "recent_transactions": [
                {"item": "Song One", "amount": "0.01", "type": "download"},
                {"item": "New Hit Song", "amount": "0.01", "type": "download"},
                {"item": "Fresh Album", "amount": "0.03", "type": "download"}
            ]
        })

    total_revenue = 0.0
    song_sales = {}
    album_sales = {}
    recent_transactions = []

    for purchase in reversed(purchases[-10:]):
        amount = float(purchase.get("amount", purchase.get("price", 0)) or 0)
        total_revenue += amount

        item_name = purchase.get("song_title")
        if not item_name:
            metadata = get_song_metadata(purchase.get("song_id"))
            item_name = metadata["title"] if metadata else f"Song {purchase['song_id']}"

        purchase_type = purchase.get("type", "download")

        if purchase_type in ["download", "rights"]:
            song_sales[item_name] = song_sales.get(item_name, 0) + amount

        if "album" in item_name.lower():
            album_sales[item_name] = album_sales.get(item_name, 0) + amount

        recent_transactions.append({
            "item": item_name,
            "amount": f"{amount:.2f}",
            "type": purchase_type
        })

    top_song = max(song_sales.items(), key=lambda x: x[1]) if song_sales else ("Song One", 0.00)
    top_album = max(album_sales.items(), key=lambda x: x[1]) if album_sales else ("Fresh Album", 0.00)

    return jsonify({
        "total_revenue": f"{total_revenue:.2f}",
        "currency": "AVAX",
        "top_selling_song": {
            "title": top_song[0],
            "revenue": f"{top_song[1]:.2f}"
        },
        "top_album": {
            "title": top_album[0],
            "revenue": f"{top_album[1]:.2f}"
        },
        "recent_transactions": recent_transactions[:5]
    })

@app.route("/analysis/streaming", methods=["GET"])
def get_streaming_analysis():
    """Get streaming trends data based on contract songs and recent activity"""
    contract_songs = get_contract_songs()
    artist_stats = {}
    current_time = int(time.time())
    purchases = list(purchase_history_collection.find())

    if contract_songs:
        for song in contract_songs:
            artist = song.get("artist") or "Unknown Artist"
            artist_stats.setdefault(artist, {
                "streams": 2000,
                "trend": 5,
                "top_track": song.get("title", "Unknown")
            })

            song_purchases = [p for p in purchases if p.get("song_id") == song["id"]]
            purchase_score = len(song_purchases) * 2500
            artist_stats[artist]["streams"] += purchase_score

            if purchase_score > artist_stats[artist].get("streams", 0):
                artist_stats[artist]["top_track"] = song.get("title", artist_stats[artist]["top_track"])

        for artist, data in artist_stats.items():
            recent_activity = sum(1 for p in purchases if p.get("song_id") in [s["id"] for s in contract_songs if s.get("artist") == artist] and p.get("timestamp", 0) > current_time - 30 * 24 * 3600)
            data["trend"] = min(40, max(-10, recent_activity * 5))

        top_track = max(
            [(data["top_track"], data["streams"]) for data in artist_stats.values()],
            key=lambda x: x[1]
        ) if artist_stats else ("Song One", 0)

        return jsonify({
            "artists": [
                {
                    "name": artist,
                    "streams": stats["streams"],
                    "trend": stats["trend"],
                    "top_track": stats["top_track"]
                }
                for artist, stats in artist_stats.items()
            ],
            "top_performing_track": {
                "title": top_track[0],
                "streams": top_track[1]
            },
            "last_updated": current_time
        })

    import random
    
    for artist, data in streaming_data.items():
        variation = random.randint(-500, 500)
        data["streams"] += variation
        data["streams"] = max(0, data["streams"])
        data["trend"] = random.randint(-10, 40)

    top_track = max(
        [(artist_data["top_track"], artist_data["streams"]) for artist_data in streaming_data.values()],
        key=lambda x: x[1]
    )

    return jsonify({
        "artists": [
            {
                "name": artist,
                "streams": data["streams"],
                "trend": data["trend"],
                "top_track": data["top_track"]
            }
            for artist, data in streaming_data.items()
        ],
        "top_performing_track": {
            "title": top_track[0],
            "streams": top_track[1]
        },
        "last_updated": current_time
    })

    return jsonify({
        "artists": [
            {
                "name": artist,
                "streams": data["streams"],
                "trend": data["trend"],
                "top_track": data["top_track"]
            }
            for artist, data in streaming_data.items()
        ],
        "top_performing_track": {
            "title": top_track[0],
            "streams": top_track[1]
        },
        "last_updated": current_time
    })

@app.route("/analysis/artist/<artist_name>", methods=["GET"])
def get_artist_details(artist_name):
    """Get detailed analysis for a specific artist"""
    artist_name_lower = artist_name.lower()
    contract_songs = get_contract_songs()
    artist_songs = [song for song in contract_songs if song.get("artist", "").lower() == artist_name_lower]
    purchases = list(purchase_history_collection.find())

    if artist_songs:
        song_ids = [song["id"] for song in artist_songs]
        artist_purchases = [p for p in purchases if p.get("song_id") in song_ids]
        total_streams = sum(len([p for p in purchases if p.get("song_id") == song_id]) * 3000 + 1500 for song_id in song_ids)
        recent_songs = []
        for song in artist_songs:
            song_count = sum(1 for p in artist_purchases if p.get("song_id") == song["id"])
            recent_songs.append({
                "title": song["title"],
                "streams": song_count * 3000 + 1500
            })

        downloads = sum(1 for p in artist_purchases if p.get("type") == "download")
        investments = sum(float(p.get("amount", 0)) for p in artist_purchases if p.get("type") == "invest")
        monthly_trend = min(40, max(-10, sum(1 for p in artist_purchases if p.get("timestamp", 0) > time.time() - 30 * 24 * 3600) * 4))
        top_song = max(recent_songs, key=lambda s: s["streams"]) if recent_songs else {"title": artist_name, "streams": 0}

        return jsonify({
            "name": artist_name,
            "total_streams": total_streams,
            "monthly_trend": monthly_trend,
            "top_track": top_song["title"],
            "recent_songs": recent_songs,
            "revenue_breakdown": {
                "streaming": f"{total_streams * 0.003:.2f}",
                "downloads": f"{downloads * 0.01:.2f}",
                "investments": f"{investments:.2f}"
            }
        })

    if artist_name in streaming_data:
        artist_data = streaming_data[artist_name]
        return jsonify({
            "name": artist_name,
            "total_streams": artist_data["streams"],
            "monthly_trend": artist_data["trend"],
            "top_track": artist_data["top_track"],
            "recent_songs": [
                {"title": f"{artist_name} Hit 1", "streams": artist_data["streams"] // 3},
                {"title": f"{artist_name} Hit 2", "streams": artist_data["streams"] // 4},
                {"title": artist_data["top_track"], "streams": artist_data["streams"] // 2}
            ],
            "revenue_breakdown": {
                "streaming": f"{artist_data['streams'] * 0.003:.2f}",
                "downloads": "0.25",
                "investments": "0.50"
            }
        })

    return jsonify({"error": "Artist not found"}), 404

@app.route("/analysis/blockchain", methods=["GET"])
def get_blockchain_analysis():
    """Get blockchain-specific analytics"""
    purchases = list(purchase_history_collection.find())
    total_transactions = len(purchases)
    unique_buyers = len(set(purchase["wallet"] for purchase in purchases)) if purchases else 0

    # Calculate transaction types
    transaction_types = {}
    for purchase in purchases:
        tx_type = purchase.get("type", "download")
        transaction_types[tx_type] = transaction_types.get(tx_type, 0) + 1

    # Calculate average transaction value
    if purchases:
        total_value = sum(float(purchase.get("amount", purchase.get("price", 0))) for purchase in purchases)
        avg_transaction = total_value / total_transactions
    else:
        avg_transaction = 0.01

    return jsonify({
        "total_transactions": total_transactions,
        "unique_buyers": unique_buyers,
        "transaction_types": transaction_types,
        "average_transaction_value": f"{avg_transaction:.4f}",
        "currency": "AVAX",
        "network": "Avalanche",
        "gas_saved_vs_ethereum": "99%"  # Avalanche benefit
    })

# =========================
# RECORD PURCHASE (backend tracking)
# =========================
@app.route("/buy", methods=["POST"])
def buy_song():
    data = request.get_json(force=True)

    song_id = data.get("song_id")
    wallet = data.get("wallet")
    if song_id is None or not wallet:
        return jsonify({"status": "error", "message": "song_id and wallet are required"}), 400

    tx_hash = data.get("tx_hash", "N/A")
    purchase_type = data.get("type", "download")  # download, invest, rights
    amount = data.get("amount", 0)

    metadata = get_song_metadata(song_id)
    song_title = data.get("song_title") or (metadata["title"] if metadata and metadata.get("exists") else f"Song {song_id}")
    artist_name = data.get("artist") or (metadata["artist"] if metadata and metadata.get("exists") else "Unknown Artist")

    matched_song_doc = resolve_registered_song_for_purchase(song_id, song_title)
    song_file_cid = str((matched_song_doc or {}).get("song_file_cid") or '').strip()
    ipfs_cid = str((matched_song_doc or {}).get("ipfs_cid") or '').strip()
    preview_url = str((matched_song_doc or {}).get("preview_url") or '').strip()
    album_image = str(
        (matched_song_doc or {}).get("album_image")
        or (matched_song_doc or {}).get("cover_art_url")
        or (matched_song_doc or {}).get("coverArtUrl")
        or ''
    ).strip()
    duration_ms = (matched_song_doc or {}).get("duration_ms") or (matched_song_doc or {}).get("durationMs")
    if not album_image and ipfs_cid:
        album_image = str(resolve_cover_from_ipfs_metadata(ipfs_cid) or '').strip()

    # Keep purchases aligned with true full-length availability.
    # If only preview_url exists, keep it as preview metadata and do not mint song_file_cid.

    audio_url = f"http://127.0.0.1:5000/ipfs/file/{song_file_cid}" if song_file_cid else ''

    if not amount:
        if purchase_type == "download" and metadata:
            amount = from_wei(metadata.get("downloadPrice", 0))
        elif purchase_type == "invest" and metadata:
            amount = float(data.get("amount", 0) or 0)
        elif purchase_type == "rights" and metadata:
            amount = from_wei(metadata.get("fullRightsPrice", 0))
        else:
            amount = float(data.get("amount", 0) or 0)

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 0.0

    purchase_record = {
        "song_id": song_id,
        "song_title": song_title,
        "artist": artist_name,
        "wallet": wallet,
        "tx_hash": tx_hash,
        "type": purchase_type,
        "amount": amount,
        "price": amount,
        "timestamp": int(time.time()),
        "song_file_cid": song_file_cid,
        "ipfs_cid": ipfs_cid,
        "preview_url": preview_url,
        "audio_url": audio_url,
        "ipfs_gateway_url": f"https://ipfs.io/ipfs/{song_file_cid}" if song_file_cid else '',
        "album_image": album_image,
        "cover_art_url": album_image,
        "duration_ms": duration_ms,
        "storage": "ipfs" if song_file_cid or ipfs_cid else ""
    }
    purchase_history_collection.insert_one(purchase_record)

    # Log different types of purchases
    if purchase_type == "download":
        print(f"Wallet {wallet} bought download rights for song {song_id}, Tx: {tx_hash}")
    elif purchase_type == "invest":
        print(f"Wallet {wallet} invested {amount} tokens in song {song_id}, Tx: {tx_hash}")
    elif purchase_type == "rights":
        print(f"Wallet {wallet} bought full rights for song {song_id}, Tx: {tx_hash}")

    return jsonify({
        "status": "success",
        "song_id": song_id,
        "wallet": wallet,
        "tx_hash": tx_hash,
        "type": purchase_type,
        "amount": amount,
        "song_file_cid": song_file_cid,
        "ipfs_cid": ipfs_cid,
        "audio_url": audio_url
    })

# =========================
# SONG REGISTRATION
# =========================


@app.route("/song/register", methods=["POST"])
def register_song():
    try:
        # Accept both JSON and form data
        if request.content_type and request.content_type.startswith('multipart/form-data'):
            # Handle file upload
            title = request.form.get("songTitle")
            artists = request.form.get("artistName")
            writers = request.form.get("writerNames")
            society = request.form.get("collectionSociety", "PRS")
            cae_ipi = request.form.get("caeIpi")
            isrc = request.form.get("isrc")
            publisher = request.form.get("publisher", "")
            user_address = request.form.get('userAddress') or None
            song_file = request.files.get("songFile")
            # Parse writers if JSON string
            if writers:
                import json
                try:
                    writers = json.loads(writers)
                except Exception as e:
                    print("Failed to parse writerNames JSON:", e)
                    writers = []
        else:
            data = request.get_json(force=True)
            title = data.get("songTitle")
            artists = data.get("artistNames", [])
            writers = data.get("writerNames", [])
            society = data.get("collectionSociety", "PRS")
            cae_ipi = data.get("caeIpi")
            isrc = data.get("isrc")
            publisher = data.get("publisher", "")
            user_address = data.get('userAddress') or None
            song_file = None

        ipfs_metadata_cid = None
        ipfs_song_cid = None

        if not title or not cae_ipi or not isrc or not society:
            print("Missing required fields:", title, cae_ipi, isrc, society)
            return jsonify({"status": "error", "message": "Missing required fields: song title, CAE/IPI, ISRC, and collection society."}), 400

        # Normalize values: accept strings or arrays
        if isinstance(artists, str):
            artists = [artists] if artists.strip() else []
        if isinstance(writers, str):
            writers = [writers] if writers.strip() else []

        registration_record = {
            "songTitle": title,
            "artistNames": artists,
            "writerNames": writers,
            "collectionSociety": society,
            "caeIpi": cae_ipi,
            "isrc": isrc,
            "publisher": publisher,
            "user_address": user_address,
            "registered_at": int(time.time()),
            "status": "pending",
            "reviewed_at": None,
            "reviewed_by": None,
            "review_reason": None
        }

        # Store song file on IPFS if present
        if song_file:
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    song_file.save(tmp.name)
                    ipfs_song_cid = upload_file_to_ipfs(tmp.name)
                registration_record["song_file_cid"] = ipfs_song_cid
            except Exception as e:
                print("Song file IPFS upload failed:", e)
                registration_record["song_file_cid"] = None

        # Store metadata on IPFS
        try:
            ipfs_metadata_cid = upload_json_to_ipfs(registration_record)
            registration_record["ipfs_cid"] = ipfs_metadata_cid
        except Exception as e:
            print("IPFS upload failed:", e)
            registration_record["ipfs_cid"] = None

        insert_result = registered_songs_collection.insert_one(registration_record)
        registration_record['_id'] = insert_result.inserted_id

        print(f"Registered song: {title}, Society={society}, CAE/IPI={cae_ipi}, ISRC={isrc}, IPFS CID={ipfs_metadata_cid}, Song CID={ipfs_song_cid}")

        safe_registration_record = serialize_registered_song_document(registration_record)

        return jsonify({
            "status": "success",
            "message": f"Song registered successfully with {society}.",
            "song": safe_registration_record
        }), 201
    except Exception as e:
        import traceback
        print("Song registration failed:", e)
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Song registration failed: {e}"}), 500


@app.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(force=True)
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '').strip()
    if not username or not password:
        return jsonify({"status": "error", "message": "Username and password required."}), 400

    env_admin_match = (
        username == str(ADMIN_USERNAME or '').strip() and
        password == str(ADMIN_PASSWORD or '').strip()
    )

    db_admin_match = False
    if not env_admin_match:
        user = users_collection.find_one({'username': username, 'is_admin': True})
        db_admin_match = bool(user and check_password_hash(user.get('password', ''), password))

    if not env_admin_match and not db_admin_match:
        return jsonify({"status": "error", "message": "Invalid admin credentials."}), 401

    token = secrets.token_urlsafe(32)
    admin_sessions_collection.insert_one({
        "token": token,
        "username": username,
        "expires_at": time.time() + 30*24*3600  # 30 days
    })
    resp = make_response(jsonify({
        "status": "success",
        "admin_token": token,
        "expires_in": 30*24*3600
    }))
    resp.set_cookie('admin_token', token, max_age=30*24*3600, httponly=True, samesite='Lax')
    return resp



@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    token = request.cookies.get('admin_token') or get_admin_token_from_request()
    admin_sessions_collection.delete_one({'token': token})
    resp = make_response(jsonify({"status": "success", "message": "Logged out."}))
    resp.set_cookie('admin_token', '', expires=0)
    return resp


@app.route("/admin/validate", methods=["GET"])
def admin_validate():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Invalid admin token."}), 401
    session = admin_sessions_collection.find_one({'token': token})
    return jsonify({"status": "success", "username": session['username']})


@app.route("/song/registered", methods=["GET"])
def get_registered_songs():
    token = get_admin_token_from_request()
    status = request.args.get('status', 'approved').strip().lower()
    if status != 'approved' and not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    if status == 'approved':
        query = {'$or': [{'status': 'approved'}, {'status': {'$exists': False}}]}
    elif status:
        query = {'status': status}
    else:
        query = {}

    cursor = registered_songs_collection.find(query).sort('registered_at', -1)
    songs = [serialize_registered_song_document(song) for song in cursor]
    if status == 'approved':
        songs = dedupe_registered_song_documents(songs)

    return jsonify({
        "status": "success",
        "count": len(songs),
        "songs": songs
    })


@app.route("/admin/song_registrations", methods=["GET"])
def admin_song_registrations():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    status = request.args.get('status', 'pending').strip().lower() or 'pending'
    if status == 'approved':
        query = {'$or': [{'status': 'approved'}, {'status': {'$exists': False}}]}
    else:
        query = {'status': status}

    cursor = registered_songs_collection.find(query).sort('registered_at', -1)
    songs = [serialize_registered_song_document(song) for song in cursor]
    if status == 'approved':
        songs = dedupe_registered_song_documents(songs)

    return jsonify({
        "status": "success",
        "count": len(songs),
        "songs": songs,
        "review_status": status
    })


@app.route("/admin/song_approve", methods=["POST"])
def admin_song_approve():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    data = request.get_json(force=True)
    record_id = data.get('id') or data.get('record_id')
    if not record_id:
        return jsonify({"status": "error", "message": "Song id is required."}), 400

    from bson import ObjectId
    try:
        obj_id = ObjectId(record_id)
    except Exception:
        return jsonify({"status": "error", "message": "Invalid song id."}), 400

    admin_session = admin_sessions_collection.find_one({'token': token})
    admin_name = admin_session.get('username', 'ADMIN') if admin_session else 'ADMIN'

    result = registered_songs_collection.update_one(
        {'_id': obj_id},
        {'$set': {
            'status': 'approved',
            'reviewed_at': int(time.time()),
            'reviewed_by': admin_name,
            'review_reason': None
        }}
    )
    if result.matched_count == 0:
        return jsonify({"status": "error", "message": "Song not found."}), 404

    return jsonify({"status": "success", "message": "Song approved."})


@app.route("/admin/song_reject", methods=["POST"])
def admin_song_reject():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    data = request.get_json(force=True)
    record_id = data.get('id') or data.get('record_id')
    reason = (data.get('reason') or '').strip()
    if not record_id:
        return jsonify({"status": "error", "message": "Song id is required."}), 400

    from bson import ObjectId
    try:
        obj_id = ObjectId(record_id)
    except Exception:
        return jsonify({"status": "error", "message": "Invalid song id."}), 400

    admin_session = admin_sessions_collection.find_one({'token': token})
    admin_name = admin_session.get('username', 'ADMIN') if admin_session else 'ADMIN'

    result = registered_songs_collection.update_one(
        {'_id': obj_id},
        {'$set': {
            'status': 'rejected',
            'reviewed_at': int(time.time()),
            'reviewed_by': admin_name,
            'review_reason': reason or 'Rejected by admin'
        }}
    )
    if result.matched_count == 0:
        return jsonify({"status": "error", "message": "Song not found."}), 404

    return jsonify({"status": "success", "message": "Song rejected."})


@app.route("/admin/song_delete", methods=["POST"])
def admin_song_delete():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    data = request.get_json(force=True)
    record_id = data.get('id') or data.get('record_id')
    if not record_id:
        return jsonify({"status": "error", "message": "Song id is required."}), 400

    from bson import ObjectId
    try:
        obj_id = ObjectId(record_id)
    except Exception:
        return jsonify({"status": "error", "message": "Invalid song id."}), 400

    admin_session = admin_sessions_collection.find_one({'token': token})
    admin_name = admin_session.get('username', 'ADMIN') if admin_session else 'ADMIN'

    result = registered_songs_collection.update_one(
        {'_id': obj_id},
        {'$set': {
            'status': 'deleted',
            'reviewed_at': int(time.time()),
            'reviewed_by': admin_name,
            'review_reason': 'Deleted by admin'
        }}
    )
    if result.matched_count == 0:
        return jsonify({"status": "error", "message": "Song not found."}), 404

    return jsonify({"status": "success", "message": "Song deleted."})


@app.route("/admin/contract_song_delete", methods=["POST"])
def admin_contract_song_delete():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    data = request.get_json(force=True)
    song_id = data.get('song_id')
    reason = (data.get('reason') or '').strip()

    try:
        song_id_int = int(song_id)
        if song_id_int <= 0:
            raise ValueError('Invalid song id')
    except Exception:
        return jsonify({"status": "error", "message": "Valid contract song_id is required."}), 400

    admin_session = admin_sessions_collection.find_one({'token': token})
    admin_name = admin_session.get('username', 'ADMIN') if admin_session else 'ADMIN'

    deleted_contract_songs_collection.update_one(
        {'song_id': song_id_int},
        {'$set': {
            'song_id': song_id_int,
            'deleted': True,
            'reason': reason or 'Deleted by admin',
            'deleted_by': admin_name,
            'deleted_at': int(time.time())
        }},
        upsert=True
    )

    return jsonify({"status": "success", "message": f"Contract song {song_id_int} deleted from platform view."})


@app.route("/admin/contract_song_restore", methods=["POST"])
def admin_contract_song_restore():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    data = request.get_json(force=True)
    song_id = data.get('song_id')

    try:
        song_id_int = int(song_id)
        if song_id_int <= 0:
            raise ValueError('Invalid song id')
    except Exception:
        return jsonify({"status": "error", "message": "Valid contract song_id is required."}), 400

    result = deleted_contract_songs_collection.delete_one({'song_id': song_id_int})
    if result.deleted_count == 0:
        return jsonify({"status": "error", "message": f"Contract song {song_id_int} was not in deleted list."}), 404

    return jsonify({"status": "success", "message": f"Contract song {song_id_int} restored."})


@app.route("/admin/contract_song_deletions", methods=["GET"])
def admin_contract_song_deletions():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    docs = list(
        deleted_contract_songs_collection.find({'deleted': True}).sort('deleted_at', -1).limit(200)
    )

    output = []
    for doc in docs:
        output.append({
            'id': str(doc.get('_id')),
            'song_id': doc.get('song_id'),
            'reason': doc.get('reason') or '',
            'deleted_by': doc.get('deleted_by') or 'ADMIN',
            'deleted_at': doc.get('deleted_at')
        })

    return jsonify({
        "status": "success",
        "count": len(output),
        "songs": output
    })


@app.route("/admin/backfill_song_covers", methods=["POST"])
def admin_backfill_song_covers():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    data = request.get_json(silent=True) or {}
    try:
        limit = int(data.get('limit', 20))
    except Exception:
        limit = 20
    limit = max(1, min(limit, 100))

    query = {
        '$and': [
            {'$or': [{'status': 'approved'}, {'status': {'$exists': False}}]},
            {
                '$and': [
                    {'$or': [{'album_image': {'$exists': False}}, {'album_image': ''}, {'album_image': None}]},
                    {'$or': [{'cover_art_url': {'$exists': False}}, {'cover_art_url': ''}, {'cover_art_url': None}]},
                    {'$or': [{'coverArtUrl': {'$exists': False}}, {'coverArtUrl': ''}, {'coverArtUrl': None}]}
                ]
            }
        ]
    }

    docs = list(registered_songs_collection.find(query).sort('registered_at', -1).limit(limit))

    processed = []
    updated_count = 0
    skipped_count = 0
    for song_document in docs:
        result = persist_registered_song_cover_backfill(song_document, allow_external=True)
        processed.append({
            'id': str(song_document.get('_id')),
            'title': result.get('title'),
            'updated': result.get('updated', False),
            'source': result.get('source'),
            'album_image': result.get('album_image') or ''
        })
        if result.get('updated'):
            updated_count += 1
        else:
            skipped_count += 1

    return jsonify({
        'status': 'success',
        'requested_limit': limit,
        'processed_count': len(processed),
        'updated_count': updated_count,
        'skipped_count': skipped_count,
        'songs': processed
    })


@app.route("/admin/backfill_song_audio", methods=["POST"])
def admin_backfill_song_audio():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    data = request.get_json(silent=True) or {}
    try:
        limit = int(data.get('limit', 100))
    except Exception:
        limit = 100
    limit = max(1, min(limit, 1000))
    result = run_song_audio_backfill(limit=limit)
    return jsonify({
        'status': 'success',
        'requested_limit': limit,
        **result
    })


@app.route("/admin/backfill_purchase_media", methods=["POST"])
def admin_backfill_purchase_media():
    token = get_admin_token_from_request()
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    data = request.get_json(silent=True) or {}
    wallet = str(data.get('wallet') or '').strip()
    try:
        limit = int(data.get('limit', 500))
    except Exception:
        limit = 500
    limit = max(1, min(limit, 5000))

    purchase_query = {}
    if wallet:
        purchase_query = {"wallet": {"$regex": f"^{re.escape(wallet)}$", "$options": "i"}}

    purchases = list(purchase_history_collection.find(purchase_query).sort('timestamp', -1).limit(limit))

    processed = 0
    updated = 0
    skipped = 0
    updated_rows = []

    for purchase in purchases:
        processed += 1
        song_id = purchase.get('song_id')
        song_title = purchase.get('song_title')
        matched_song_doc = resolve_registered_song_for_purchase(song_id, song_title)
        song_metadata = get_song_metadata(song_id)

        purchase_song_file_cid = str(purchase.get('song_file_cid') or '').strip()
        matched_song_file_cid = str((matched_song_doc or {}).get('song_file_cid') or '').strip()
        song_file_cid = matched_song_file_cid or purchase_song_file_cid
        if purchase_song_file_cid and not matched_song_file_cid:
            # Drop legacy polluted CID values that clearly belong to a different title.
            try:
                owner_doc = registered_songs_collection.find_one({'song_file_cid': purchase_song_file_cid})
                owner_title = str((owner_doc or {}).get('songTitle') or '').strip().lower()
                purchase_title = str(purchase.get('song_title') or '').strip().lower()
                if owner_title and purchase_title and owner_title != purchase_title:
                    song_file_cid = ''
            except Exception:
                pass

        purchase_ipfs_cid = str(purchase.get('ipfs_cid') or '').strip()
        matched_ipfs_cid = str((matched_song_doc or {}).get('ipfs_cid') or '').strip()
        ipfs_cid = matched_ipfs_cid or purchase_ipfs_cid
        preview_url = str(purchase.get('preview_url') or (matched_song_doc or {}).get('preview_url') or '').strip()
        audio_url = str(purchase.get('audio_url') or '').strip()
        if not audio_url and song_file_cid:
            audio_url = f"http://127.0.0.1:5000/ipfs/file/{song_file_cid}"

        album_image = str(
            purchase.get('album_image')
            or purchase.get('cover_art_url')
            or (matched_song_doc or {}).get('album_image')
            or (matched_song_doc or {}).get('cover_art_url')
            or (matched_song_doc or {}).get('coverArtUrl')
            or ''
        ).strip()
        if not album_image and ipfs_cid:
            album_image = str(resolve_cover_from_ipfs_metadata(ipfs_cid) or '').strip()

        duration_ms = (
            purchase.get('duration_ms')
            or purchase.get('durationMs')
            or (matched_song_doc or {}).get('duration_ms')
            or (matched_song_doc or {}).get('durationMs')
        )

        title_for_lookup = (
            (song_metadata or {}).get('title')
            or song_title
            or (matched_song_doc or {}).get('songTitle')
            or (matched_song_doc or {}).get('title')
            or ''
        )
        artist_for_lookup = (
            (song_metadata or {}).get('artist')
            or purchase.get('artist')
            or ((matched_song_doc or {}).get('artistNames') or [''])[0]
            if isinstance((matched_song_doc or {}).get('artistNames'), list)
            else (matched_song_doc or {}).get('artist')
            or ''
        )
        spotify_id = str(
            purchase.get('spotify_id')
            or (song_metadata or {}).get('spotifyId')
            or (matched_song_doc or {}).get('spotify_id')
            or (matched_song_doc or {}).get('spotifyId')
            or ''
        ).strip()

        if (not album_image or not duration_ms) and (title_for_lookup or artist_for_lookup or spotify_id):
            fetched_metadata = fetch_spotify_cover_metadata(title_for_lookup, artist_for_lookup, spotify_id) or fetch_itunes_cover_metadata(title_for_lookup, artist_for_lookup) or {}
            if not album_image:
                album_image = str(fetched_metadata.get('album_image') or '').strip()
            if not duration_ms:
                duration_ms = fetched_metadata.get('duration_ms')
            if not preview_url:
                preview_url = str(fetched_metadata.get('preview_url') or '').strip()

        audio_fallback_url = f"https://ipfs.io/ipfs/{song_file_cid}" if song_file_cid else ''
        audio_alt_gateway_url = f"https://gateway.pinata.cloud/ipfs/{song_file_cid}" if song_file_cid else ''

        update_fields = {}
        if matched_song_file_cid and matched_song_file_cid != purchase_song_file_cid:
            update_fields['song_file_cid'] = matched_song_file_cid
        elif song_file_cid and not purchase_song_file_cid:
            update_fields['song_file_cid'] = song_file_cid
        if matched_ipfs_cid and matched_ipfs_cid != purchase_ipfs_cid:
            update_fields['ipfs_cid'] = matched_ipfs_cid
        elif ipfs_cid and not purchase_ipfs_cid:
            update_fields['ipfs_cid'] = ipfs_cid
        if purchase_song_file_cid and not song_file_cid:
            update_fields['song_file_cid'] = ''
        if preview_url and not str(purchase.get('preview_url') or '').strip():
            update_fields['preview_url'] = preview_url
        if audio_url and not str(purchase.get('audio_url') or '').strip():
            update_fields['audio_url'] = audio_url
        if album_image and not str(purchase.get('album_image') or '').strip():
            update_fields['album_image'] = album_image
        if album_image and not str(purchase.get('cover_art_url') or '').strip():
            update_fields['cover_art_url'] = album_image
        if duration_ms and not purchase.get('duration_ms'):
            update_fields['duration_ms'] = duration_ms
        if audio_fallback_url and not str(purchase.get('audio_fallback_url') or '').strip():
            update_fields['audio_fallback_url'] = audio_fallback_url
        if audio_alt_gateway_url and not str(purchase.get('audio_alt_gateway_url') or '').strip():
            update_fields['audio_alt_gateway_url'] = audio_alt_gateway_url

        if not update_fields:
            skipped += 1
            continue

        try:
            purchase_history_collection.update_one(
                {'_id': purchase.get('_id')},
                {'$set': update_fields}
            )
            updated += 1
            updated_rows.append({
                'id': str(purchase.get('_id')),
                'song_id': purchase.get('song_id'),
                'type': purchase.get('type', 'download'),
                'fields': sorted(list(update_fields.keys()))
            })
        except Exception:
            skipped += 1

    return jsonify({
        'status': 'success',
        'wallet': wallet or None,
        'requested_limit': limit,
        'processed_count': processed,
        'updated_count': updated,
        'skipped_count': skipped,
        'updated_rows': updated_rows[:200]
    })


@app.route("/spotify/search", methods=["GET"])
def search_spotify():
    query = request.args.get('q', '')
    if not query:
        return jsonify({"error": "Query parameter required"}), 400
    # Allow client to request more results (Spotify max 50) and offset for paging
    try:
        limit = int(request.args.get('limit', 50))
    except Exception:
        limit = 50
    try:
        offset = int(request.args.get('offset', 0))
    except Exception:
        offset = 0
    if limit > 50:
        limit = 50
    if offset < 0:
        offset = 0

    # Use mock data for testing
    if USE_MOCK_DATA:
        mock_tracks = [
            {
                'id': '4iV5W9uYEdYUVa79Axb7Rh',
                'title': 'Blinding Lights',
                'artist': 'The Weeknd',
                'album': 'After Hours',
                'album_image': 'https://i.scdn.co/image/ab67616d0000b273c6f7c1d1b72f3d9f4e2e5d5f',
                'preview_url': 'https://p.scdn.co/mp3-preview/example1',
                'duration_ms': 200040,
                'external_url': 'https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh'
            },
            {
                'id': '11dFghVXANMlKmJXsNCbNl',
                'title': 'Watermelon Sugar',
                'artist': 'Harry Styles',
                'album': 'Fine Line',
                'album_image': 'https://i.scdn.co/image/ab67616d0000b273b46f74097655d7c2c8f7dd1b',
                'preview_url': 'https://p.scdn.co/mp3-preview/example2',
                'duration_ms': 174000,
                'external_url': 'https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl'
            },
            {
                'id': '0VjIjW4GlUZAMYd2vXMi3b',
                'title': 'Levitating',
                'artist': 'Dua Lipa',
                'album': 'Future Nostalgia',
                'album_image': 'https://i.scdn.co/image/ab67616d0000b2735f5f8f5e10d75cc33a7d1d62',
                'preview_url': 'https://p.scdn.co/mp3-preview/example3',
                'duration_ms': 203000,
                'external_url': 'https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b'
            }
        ]

        # Filter mock data based on query
        filtered_tracks = [
            track for track in mock_tracks
            if query.lower() in track['title'].lower() or query.lower() in track['artist'].lower()
        ]

        return jsonify(filtered_tracks[offset:offset+limit])

    # Real Spotify API call
    token = get_spotify_token()
    if not token:
        return jsonify({"error": "Failed to get Spotify access token"}), 500

    def _spotify_search(access_token):
        headers = {'Authorization': f'Bearer {access_token}'}
        return requests_get_with_retries(
            'https://api.spotify.com/v1/search',
            headers=headers,
            timeout=8,
            retries=2,
            backoff=0.5,
            params={
                'q': query,
                'type': 'track',
                'limit': limit,
                'offset': offset
            }
        )

    try:
        response = _spotify_search(token)
    except requests.Timeout:
        return jsonify({"error": "Spotify API request timed out"}), 504
    except Exception as e:
        return jsonify({"error": f"Failed to search Spotify: {str(e)}"}), 500

    # Refresh expired token once and retry.
    if response.status_code == 401:
        refreshed_token = get_spotify_token(force_refresh=True)
        if refreshed_token:
            try:
                response = _spotify_search(refreshed_token)
            except requests.Timeout:
                return jsonify({"error": "Spotify API request timed out"}), 504
            except Exception as e:
                return jsonify({"error": f"Failed to search Spotify after token refresh: {str(e)}"}), 500

    if response.status_code == 200:
        data = response.json()
        tracks = []
        for item in data['tracks']['items']:
            tracks.append({
                'id': item['id'],
                'title': item['name'],
                'artist': item['artists'][0]['name'],
                'album': item['album']['name'],
                'album_image': item['album']['images'][0]['url'] if item['album'].get('images') else None,
                'preview_url': item['preview_url'],
                'duration_ms': item.get('duration_ms'),
                'external_url': item['external_urls']['spotify']
            })
        return jsonify(tracks)
    elif response.status_code in (401, 403, 429):
        message = _extract_spotify_error_message(response)
        print(f"[WARN] Spotify search unavailable for query '{query}' (status {response.status_code}): {message}")
        return jsonify({
            "error": "Spotify search unavailable",
            "status": response.status_code,
            "details": message
        }), response.status_code
    else:
        error_detail = None
        try:
            error_detail = response.json().get('error')
        except Exception:
            error_detail = response.text[:200]
        return jsonify({"error": "Failed to search Spotify", "details": error_detail}), response.status_code

@app.route('/spotify/init')
def spotify_init():
    try:
        token = get_spotify_token()
        if not token:
            return jsonify({
                "status": "error",
                "message": "Spotify token could not be acquired. Check Spotify credentials or subscription status."
            }), 503

        return jsonify({
            "status": "connected",
            "message": "Spotify API connected successfully"
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/spotify/connect')
def spotify_connect():
    return spotify_init()







@app.route("/spotify/artist", methods=["GET"])
def get_spotify_artist():
    query = request.args.get('artist', '')
    if not query:
        return jsonify({"error": "Artist query parameter required"}), 400

    query_clean = " ".join(str(query or '').strip().split())
    is_probable_artist_id = len(query_clean) == 22 and query_clean.isalnum()

    def _normalize_artist_lookup(value):
        return re.sub(r'[^a-z0-9]', '', str(value or '').strip().lower())

    def _local_artist_fallback_response(q, allow_backfill=True):
        query_clean_local = " ".join(str(q or '').strip().split())
        if not query_clean_local:
            return {
                'id': '',
                'name': q,
                'tracks': [],
                'albums': [],
                'playlists': []
            }

        candidate_docs = list(
            registered_songs_collection.find({
                '$or': [{'status': 'approved'}, {'status': {'$exists': False}}]
            }).sort('registered_at', -1).limit(500)
        )

        wanted_artist = _normalize_artist_lookup(query_clean_local)
        docs = []
        for song_doc in candidate_docs:
            if isinstance(song_doc.get('artistNames'), list):
                artist_candidates = [str(x) for x in song_doc.get('artistNames') if x]
            else:
                artist_candidates = [song_doc.get('artist') or song_doc.get('artistNames') or '']

            matches_artist = any(
                _normalize_artist_lookup(candidate) == wanted_artist or
                wanted_artist in _normalize_artist_lookup(candidate) or
                _normalize_artist_lookup(candidate) in wanted_artist
                for candidate in artist_candidates if candidate
            )

            if matches_artist:
                docs.append(song_doc)

        docs = docs[:200]

        tracks = []
        seen = set()
        cover_lookup_budget = max(0, COVER_BACKFILL_MAX_LOOKUPS_PER_REQUEST)
        for song in docs:
            title = song.get('songTitle') or song.get('title') or 'Unknown'
            if isinstance(song.get('artistNames'), list):
                artist_name = ', '.join([str(x) for x in song.get('artistNames') if x])
            else:
                artist_name = song.get('artist') or song.get('artistNames') or q

            is_initialised = bool(song.get('blockchain_registered') or song.get('blockchain_song_id') or song.get('song_id') or song.get('songId'))

            spotify_id = str(song.get('spotify_id') or song.get('id') or '')
            album_image = song.get('album_image') or song.get('cover_art_url') or song.get('coverArtUrl')
            fetched_metadata = None
            if allow_backfill and not album_image and cover_lookup_budget > 0:
                cover_lookup_budget -= 1
                album_image = resolve_cover_from_ipfs_metadata(song.get('ipfs_cid'))
                if album_image:
                    try:
                        if song.get('_id'):
                            registered_songs_collection.update_one(
                                {'_id': song.get('_id')},
                                {'$set': {
                                    'album_image': album_image,
                                    'cover_art_url': album_image,
                                    'coverArtUrl': album_image
                                }}
                            )
                    except Exception:
                        pass
            if allow_backfill and not album_image and cover_lookup_budget > 0:
                cover_lookup_budget -= 1
                fetched_metadata = fetch_spotify_cover_metadata(title, artist_name, spotify_id)
                if fetched_metadata and fetched_metadata.get('album_image'):
                    album_image = fetched_metadata.get('album_image')
                    set_fields = {
                        'album_image': album_image,
                        'cover_art_url': album_image,
                        'coverArtUrl': album_image
                    }
                    if fetched_metadata.get('duration_ms'):
                        set_fields['duration_ms'] = fetched_metadata.get('duration_ms')
                    if fetched_metadata.get('preview_url'):
                        set_fields['preview_url'] = fetched_metadata.get('preview_url')
                    if fetched_metadata.get('album') and not song.get('album'):
                        set_fields['album'] = fetched_metadata.get('album')
                    try:
                        if song.get('_id'):
                            registered_songs_collection.update_one({'_id': song.get('_id')}, {'$set': set_fields})
                    except Exception:
                        pass
            if allow_backfill and not album_image and cover_lookup_budget > 0:
                cover_lookup_budget -= 1
                fetched_metadata = fetch_itunes_cover_metadata(title, artist_name)
                if fetched_metadata and fetched_metadata.get('album_image'):
                    album_image = fetched_metadata.get('album_image')
                    set_fields = {
                        'album_image': album_image,
                        'cover_art_url': album_image,
                        'coverArtUrl': album_image
                    }
                    if fetched_metadata.get('duration_ms'):
                        set_fields['duration_ms'] = fetched_metadata.get('duration_ms')
                    if fetched_metadata.get('preview_url'):
                        set_fields['preview_url'] = fetched_metadata.get('preview_url')
                    if fetched_metadata.get('album') and not song.get('album'):
                        set_fields['album'] = fetched_metadata.get('album')
                    try:
                        if song.get('_id'):
                            registered_songs_collection.update_one({'_id': song.get('_id')}, {'$set': set_fields})
                    except Exception:
                        pass

            key = f"{str(title).strip().lower()}::{str(artist_name).strip().lower()}"
            if key in seen:
                continue
            seen.add(key)

            tracks.append({
                'id': spotify_id,
                'name': title,
                'artist': artist_name,
                'album': {
                    'name': song.get('album') or 'Platform Registered Song',
                    'image': album_image or None,
                    'type': 'registered' if is_initialised else 'pending'
                },
                'duration_ms': song.get('duration_ms') or song.get('duration') or (fetched_metadata.get('duration_ms') if fetched_metadata else None),
                'external_urls': {
                    'spotify': song.get('spotify_url') or song.get('external_url') or ''
                },
                'source': 'platform' if is_initialised else 'local',
                'isInitialised': is_initialised
            })

        resolved_name = q
        if tracks and docs:
            first = docs[0]
            if isinstance(first.get('artistNames'), list) and first.get('artistNames'):
                resolved_name = first.get('artistNames')[0]
            elif first.get('artist'):
                resolved_name = first.get('artist')

        return {
            'id': '',
            'name': resolved_name,
            'tracks': tracks,
            'albums': [],
            'playlists': []
        }

    if USE_MOCK_DATA:
        mock_artist_info = {
            'The Weeknd': {
                'id': '1Xyo4u8uXC1ZmMpatF05PJ',
                'name': 'The Weeknd',
                'tracks': [
                    {'name': 'Blinding Lights', 'album': {'name': 'After Hours', 'image': 'https://i.scdn.co/image/ab67616d0000b273c6f7c1d1b72f3d9f4e2e5d5f', 'type': 'album'}, 'duration_ms': 200040, 'external_urls': {'spotify': 'https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh'}},
                    {'name': 'Save Your Tears', 'album': {'name': 'After Hours', 'image': 'https://i.scdn.co/image/ab67616d0000b273c6f7c1d1b72f3d9f4e2e5d5f', 'type': 'album'}, 'duration_ms': 215000, 'external_urls': {'spotify': 'https://open.spotify.com/track/4kQ0Y4xW7OsNxaGQDSNM2e'}}
                ],
                'albums': [
                    {'name': 'After Hours', 'external_urls': {'spotify': 'https://open.spotify.com/album/4yP0hdKOZPNshxUOjY0cZj'}}
                ],
                'playlists': [
                    {'name': 'The Weeknd Essentials', 'external_urls': {'spotify': 'https://open.spotify.com/playlist/37i9dQZF1DX2sUQwD7tbmL'}}
                ]
            },
            'Harry Styles': {
                'id': '6KImCVD70vtIoJWnq6nGn3',
                'name': 'Harry Styles',
                'tracks': [
                    {'name': 'Watermelon Sugar', 'album': {'name': 'Fine Line', 'image': 'https://i.scdn.co/image/ab67616d0000b273b46f74097655d7c2c8f7dd1b', 'type': 'album'}, 'duration_ms': 174000, 'external_urls': {'spotify': 'https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl'}},
                    {'name': 'Adore You', 'album': {'name': 'Fine Line', 'image': 'https://i.scdn.co/image/ab67616d0000b273b46f74097655d7c2c8f7dd1b', 'type': 'album'}, 'duration_ms': 207000, 'external_urls': {'spotify': 'https://open.spotify.com/track/6KImCVD70vtIoJWnq6nGn3'}}
                ],
                'albums': [
                    {'name': 'Fine Line', 'external_urls': {'spotify': 'https://open.spotify.com/album/4R1Cts5yU9G8a2yuK2Q4Ag'}}
                ],
                'playlists': [
                    {'name': 'Harry Styles Hits', 'external_urls': {'spotify': 'https://open.spotify.com/playlist/37i9dQZF1DX9x8uJtIy8Z1'}}
                ]
            }
        }

        artist_info = None
        for key, value in mock_artist_info.items():
            if query.lower() in key.lower() or query.lower() == value['id'].lower():
                artist_info = value
                break

        if not artist_info:
            return jsonify({
                'name': query,
                'tracks': [],
                'albums': [],
                'playlists': []
            })

        return jsonify(artist_info)


    token = get_spotify_token()
    if not token:
        print("[WARN] Failed to get Spotify access token.")
        return jsonify({
            "error": "Spotify artist search unavailable",
            "status": 503,
            "details": "Failed to get Spotify access token"
        }), 503

    headers = {'Authorization': f'Bearer {token}'}

    def _fallback_artist_from_track_search(q, auth_headers):
        try:
            query_candidates = []
            for candidate in [
                q,
                str(q or '').replace('-', ' '),
                str(q or '').replace(' ', '-'),
                str(q or '').replace(' ', '')
            ]:
                cleaned = " ".join(str(candidate or '').split())
                if cleaned and cleaned not in query_candidates:
                    query_candidates.append(cleaned)

            payload_items = []
            for query_variant in query_candidates:
                fallback_resp = requests_get_with_retries(
                    'https://api.spotify.com/v1/search',
                    headers=auth_headers,
                    timeout=3,
                    retries=1,
                    backoff=0.5,
                    params={
                        'q': query_variant,
                        'type': 'track',
                        'limit': 10,
                        'offset': 0
                    }
                )

                if fallback_resp.status_code == 401:
                    refreshed = get_spotify_token(force_refresh=True)
                    if refreshed:
                        auth_headers = {'Authorization': f'Bearer {refreshed}'}
                        fallback_resp = requests_get_with_retries(
                            'https://api.spotify.com/v1/search',
                            headers=auth_headers,
                            timeout=4,
                            retries=1,
                            backoff=0.5,
                            params={
                                'q': query_variant,
                                'type': 'track',
                                'limit': 50,
                                'offset': 0
                            }
                        )

                if fallback_resp.status_code != 200:
                    continue

                payload = fallback_resp.json() or {}
                items = (payload.get('tracks') or {}).get('items') or []
                if items:
                    payload_items = items
                    break

            if not payload_items:
                return None

            def _norm(value):
                return re.sub(r'[^a-z0-9]', '', str(value or '').lower())

            artist_counts = {}
            for item in payload_items:
                for artist_obj in (item.get('artists') or []):
                    aid = str(artist_obj.get('id') or '').strip()
                    aname = str(artist_obj.get('name') or '').strip()
                    if not aname:
                        continue
                    key = aid or _norm(aname)
                    current = artist_counts.get(key, {'id': aid, 'name': aname, 'count': 0})
                    current['count'] += 1
                    if not current.get('id') and aid:
                        current['id'] = aid
                    artist_counts[key] = current

            if artist_counts:
                dominant_artist = sorted(artist_counts.values(), key=lambda x: x.get('count', 0), reverse=True)[0]
            else:
                dominant_artist = {'id': '', 'name': q, 'count': 0}

            dominant_id = str(dominant_artist.get('id') or '').strip()
            dominant_name = dominant_artist.get('name') or q

            filtered_items = []
            for item in payload_items:
                artists = item.get('artists') or []
                if dominant_id:
                    if any(str(a.get('id') or '').strip() == dominant_id for a in artists):
                        filtered_items.append(item)
                else:
                    if any(_norm(a.get('name')) == _norm(dominant_name) for a in artists):
                        filtered_items.append(item)

            items_for_tracks = filtered_items or payload_items

            tracks = []
            seen_ids = set()
            for item in items_for_tracks:
                tid = item.get('id')
                if not tid or tid in seen_ids:
                    continue
                seen_ids.add(tid)
                album = item.get('album') or {}
                images = album.get('images') or []
                primary_artist = ((item.get('artists') or [{}])[0] or {})
                artist_name = primary_artist.get('name') or dominant_name or q
                tracks.append({
                    'id': tid,
                    'name': item.get('name'),
                    'album': {
                        'name': album.get('name'),
                        'image': images[0].get('url') if images else None,
                        'type': album.get('album_type') or 'album'
                    },
                    'duration_ms': item.get('duration_ms'),
                    'external_urls': item.get('external_urls', {}),
                    'artist': artist_name,
                    'source': 'spotify',
                    'isInitialised': False
                })

            if not tracks:
                return None

            return {
                'id': dominant_id,
                'name': dominant_name,
                'tracks': tracks,
                'albums': [],
                'playlists': []
            }

        except Exception:
            return None

    artist = None
    if is_probable_artist_id:
        try:
            artist_resp = requests_get_with_retries(
                f'https://api.spotify.com/v1/artists/{query_clean}',
                headers=headers,
                timeout=5,
                retries=1
            )
            if artist_resp.status_code == 401:
                refreshed = get_spotify_token(force_refresh=True)
                if refreshed:
                    headers = {'Authorization': f'Bearer {refreshed}'}
                    artist_resp = requests_get_with_retries(
                        f'https://api.spotify.com/v1/artists/{query_clean}',
                        headers=headers,
                        timeout=5,
                        retries=1
                    )
            if artist_resp.status_code == 200:
                artist = artist_resp.json()
        except requests.Timeout:
            fallback = _fallback_artist_from_track_search(query_clean, headers)
            if fallback:
                return jsonify(fallback)
            return jsonify({"error": "Spotify API request timed out"}), 504
        except Exception as e:
            return jsonify({"error": f"Failed to load artist by ID: {str(e)}"}), 500

    if artist is None:
        try:
            artist_search = requests_get_with_retries(
                f'https://api.spotify.com/v1/search?q={query_clean}&type=artist&limit=50',
                headers=headers,
                timeout=5,
                retries=1
            )
            if artist_search.status_code == 401:
                refreshed = get_spotify_token(force_refresh=True)
                if refreshed:
                    headers = {'Authorization': f'Bearer {refreshed}'}
                    artist_search = requests_get_with_retries(
                        f'https://api.spotify.com/v1/search?q={query_clean}&type=artist&limit=50',
                        headers=headers,
                        timeout=5,
                        retries=1
                    )
        except requests.Timeout:
            fallback = _fallback_artist_from_track_search(query_clean, headers)
            if fallback:
                return jsonify(fallback)
            return jsonify({"error": "Spotify API request timed out"}), 504
        except Exception as e:
            return jsonify({"error": f"Failed to search artist: {str(e)}"}), 500

        print("[DEBUG] Artist search status:", artist_search.status_code)
        if artist_search.status_code != 200:
            if artist_search.status_code in (401, 403, 429):
                details = _extract_spotify_error_message(artist_search)
                print(f"[WARN] Spotify artist search blocked ({artist_search.status_code}): {details}")
                return jsonify({
                    "error": "Spotify artist search unavailable",
                    "status": artist_search.status_code,
                    "details": details
                }), artist_search.status_code
            print("[WARN] Failed artist search. Falling back to track search.")
            fallback = _fallback_artist_from_track_search(query_clean, headers)
            if fallback:
                return jsonify(fallback)
            print("[WARN] Track search fallback failed. Returning empty Spotify result.")
            return jsonify({
                'id': '',
                'name': query_clean,
                'tracks': [],
                'albums': [],
                'playlists': []
            })

        data = artist_search.json()
        artists = data.get('artists', {}).get('items', [])
        if not artists:
            print("[WARN] Artist not found on Spotify. Trying track search fallback.")
            fallback = _fallback_artist_from_track_search(query_clean, headers)
            if fallback:
                return jsonify(fallback)
            print("[WARN] Track search fallback failed. Returning empty Spotify result.")
            return jsonify({
                'id': '',
                'name': query_clean,
                'tracks': [],
                'albums': [],
                'playlists': []
            })

        query_norm = query_clean.lower()
        query_tokens = [token for token in query_norm.split(' ') if token]

        def artist_rank(candidate):
            name = (candidate.get('name') or '').strip().lower()
            popularity = int(candidate.get('popularity') or 0)
            followers_total = int((candidate.get('followers') or {}).get('total') or 0)

            token_hits = 0
            for token in query_tokens:
                if token in name:
                    token_hits += 1

            token_coverage = (token_hits / len(query_tokens)) if query_tokens else 0

            if name == query_norm:
                match_score = 5
            elif name.startswith(query_norm):
                match_score = 4
            elif query_norm in name:
                match_score = 3
            elif token_coverage >= 0.99:
                match_score = 2
            elif token_coverage >= 0.5:
                match_score = 1
            else:
                match_score = 0

            return (match_score, token_hits, popularity, followers_total)

        artist = sorted(artists, key=artist_rank, reverse=True)[0]

    artist_id = artist['id']
    artist_name = artist['name']

    # Get top tracks plus some album tracks to show richer results.
    # Degrade gracefully if one of these calls fails.
    top_tracks = []
    albums = []

    try:
        top_tracks_resp = requests_get_with_retries(
            f'https://api.spotify.com/v1/artists/{artist_id}/top-tracks?market=US&limit=10',
            headers=headers,
            timeout=5,
            retries=1
        )
        print("[DEBUG] Top tracks status:", top_tracks_resp.status_code)
        if top_tracks_resp.status_code == 200:
            top_tracks = top_tracks_resp.json().get('tracks', [])
    except (requests.Timeout, requests.RequestException):
        top_tracks = []

    MAX_ARTIST_TRACKS = int(os.getenv('MAX_ARTIST_TRACKS', '120'))
    MAX_ALBUMS_TO_EXPAND = int(os.getenv('MAX_ALBUMS_TO_EXPAND', '12'))

    try:
        offset = 0
        while len(albums) < MAX_ALBUMS_TO_EXPAND:
            albums_resp = requests_get_with_retries(
                f'https://api.spotify.com/v1/artists/{artist_id}/albums',
                headers=headers,
                timeout=5,
                retries=1,
                params={
                    'include_groups': 'album,single',
                    'market': 'US',
                    'limit': 50,
                    'offset': offset
                }
            )
            print("[DEBUG] Albums status:", albums_resp.status_code)
            if albums_resp.status_code != 200:
                break

            page_items = albums_resp.json().get('items', []) or []
            if not page_items:
                break

            albums.extend(page_items)
            if len(page_items) < 50:
                break
            offset += 50

        if len(albums) > MAX_ALBUMS_TO_EXPAND:
            albums = albums[:MAX_ALBUMS_TO_EXPAND]
    except (requests.Timeout, requests.RequestException):
        albums = []

    # De-duplicate albums by id to avoid repeated album-track fetches.
    unique_albums = []
    seen_album_ids = set()
    for album in albums:
        album_id = album.get('id')
        if not album_id or album_id in seen_album_ids:
            continue
        seen_album_ids.add(album_id)
        unique_albums.append(album)
    albums = unique_albums

    # Build album image lookup from artist albums response.
    album_image_map = {}
    for alb in albums:
        images = alb.get('images') or []
        album_image_map[alb.get('id')] = images[0].get('url') if images else None

    # Fetch album tracks to aggregate more songs (dedupe by track id)
    track_map = {}

    for track in top_tracks:
        album_obj = track.get('album', {})
        images = album_obj.get('images') or []
        track_map[track['id']] = {
            'id': track.get('id'),
            'name': track.get('name'),
            'duration_ms': track.get('duration_ms'),
            'album': {
                'id': album_obj.get('id'),
                'name': album_obj.get('name'),
                'image': images[0].get('url') if images else album_image_map.get(album_obj.get('id')),
                'type': album_obj.get('album_type') or album_obj.get('type') or 'album'
            },
            'external_urls': track.get('external_urls', {})
        }

    # Limit total tracks collected
    for album in albums[:MAX_ALBUMS_TO_EXPAND]:
        album_id = album.get('id')
        if not album_id:
            continue
        try:
            album_offset = 0
            while len(track_map) < MAX_ARTIST_TRACKS:
                album_tracks_resp = requests_get_with_retries(
                    f'https://api.spotify.com/v1/albums/{album_id}/tracks',
                    headers=headers,
                    timeout=4,
                    retries=1,
                    params={
                        'market': 'US',
                        'limit': 20,
                        'offset': album_offset
                    }
                )

                if album_tracks_resp.status_code != 200:
                    break

                album_track_items = album_tracks_resp.json().get('items', []) or []
                if not album_track_items:
                    break

                for t in album_track_items:
                    track_id = t.get('id')
                    if not track_id:
                        continue
                    if track_id not in track_map:
                        # Attach album info from parent album.
                        track_map[track_id] = {
                            'id': track_id,
                            'name': t.get('name'),
                            'duration_ms': t.get('duration_ms'),
                            'album': {
                                'id': album_id,
                                'name': album.get('name'),
                                'image': album_image_map.get(album_id),
                                'type': album.get('album_group') or album.get('album_type') or 'album'
                            },
                            'external_urls': t.get('external_urls', {}),
                        }

                    if len(track_map) >= MAX_ARTIST_TRACKS:
                        break
                if len(album_track_items) < 50 or len(track_map) >= MAX_ARTIST_TRACKS:
                    break
                album_offset += 50
        except (requests.Timeout, requests.RequestException):
            # Skip this album if timeout, continue with others.
            continue
        if len(track_map) >= MAX_ARTIST_TRACKS:
            break

    # Build response tracks list with album image where possible
    response_tracks = []
    for t in track_map.values():
        album_info = t.get('album', {})
        image = None
        if isinstance(album_info, dict) and album_info.get('image'):
            image = album_info.get('image')
        response_tracks.append({
            'name': t.get('name'),
            'album': {
                'name': album_info.get('name') if album_info else None,
                'image': image,
                'type': album_info.get('type') if album_info else 'album'
            },
            'duration_ms': t.get('duration_ms'),
            'external_urls': t.get('external_urls', {}),
            'id': t.get('id'),
            'source': 'spotify',
            'isInitialised': False
        })

    return jsonify({
        'id': artist_id,
        'name': artist_name,
        'tracks': response_tracks,
        'albums': [
            {
                'name': album['name'],
                'external_urls': album['external_urls']
            }
            for album in albums
        ],
        'playlists': []
    })

@app.route("/spotify/track/<track_id>", methods=["GET"])
def get_spotify_track(track_id):
    # Use mock data for testing
    if USE_MOCK_DATA:
        mock_track_data = {
            '4iV5W9uYEdYUVa79Axb7Rh': {
                'id': '4iV5W9uYEdYUVa79Axb7Rh',
                'title': 'Blinding Lights',
                'artist': 'The Weeknd',
                'album': 'After Hours',
                'preview_url': 'https://p.scdn.co/mp3-preview/example1',
                'external_url': 'https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh',
                'duration_ms': 200040,
                'popularity': 95
            },
            '11dFghVXANMlKmJXsNCbNl': {
                'id': '11dFghVXANMlKmJXsNCbNl',
                'title': 'Watermelon Sugar',
                'artist': 'Harry Styles',
                'album': 'Fine Line',
                'preview_url': 'https://p.scdn.co/mp3-preview/example2',
                'external_url': 'https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl',
                'duration_ms': 174000,
                'popularity': 88
            }
        }



    # Real Spotify API call
    token = get_spotify_token()
    if not token:
        return jsonify({"error": "Failed to get Spotify access token"}), 500

    headers = {'Authorization': f'Bearer {token}'}
    response = requests.get(f'https://api.spotify.com/v1/tracks/{track_id}', headers=headers)

    if response.status_code == 200:
        item = response.json()
        return jsonify({
            'id': item['id'],
            'title': item['name'],
            'artist': item['artists'][0]['name'],
            'album': item['album']['name'],
            'album_image': item['album']['images'][0]['url'] if item['album'].get('images') else None,
            'preview_url': item['preview_url'],
            'external_url': item['external_urls']['spotify'],
            'duration_ms': item['duration_ms'],
            'popularity': item['popularity']
        })
    else:
        return jsonify({"error": "Failed to get track"}), response.status_code

# =========================
# GET SONG LIST (for your HTML cards)
# =========================
@app.route("/songs", methods=["GET"])
def get_songs():
    contract_songs = get_contract_songs()
    if not contract_songs:
        return jsonify([])

    title_artist_keys = set()
    spotify_ids = set()
    for song in contract_songs:
        title = str(song.get('title') or '').strip().lower()
        artist = str(song.get('artist') or '').strip().lower()
        spotify_id = str(song.get('spotifyId') or '').strip()
        if title and artist:
            title_artist_keys.add((title, artist))
        if spotify_id:
            spotify_ids.add(spotify_id)

    registered_docs = list(
        registered_songs_collection.find({
            '$or': [
                {'spotify_id': {'$in': list(spotify_ids)}} if spotify_ids else {'spotify_id': {'$exists': False}},
                {'status': 'approved'},
                {'status': {'$exists': False}}
            ]
        }).sort('registered_at', -1).limit(2000)
    )

    by_spotify_id = {}
    by_title_artist = {}
    for raw_doc in registered_docs:
        doc = serialize_registered_song_document(raw_doc)
        doc_spotify_id = str(doc.get('spotify_id') or '').strip()
        doc_title = str(doc.get('songTitle') or doc.get('title') or '').strip().lower()
        if isinstance(doc.get('artistNames'), list):
            doc_artist = str(doc.get('artistNames')[0] if doc.get('artistNames') else '').strip().lower()
        else:
            doc_artist = str(doc.get('artistNames') or doc.get('artist') or '').strip().lower()

        if doc_spotify_id and doc_spotify_id not in by_spotify_id:
            by_spotify_id[doc_spotify_id] = doc
        if doc_title and doc_artist:
            key = (doc_title, doc_artist)
            if key not in by_title_artist:
                by_title_artist[key] = doc

    enriched_songs = []
    cover_lookup_budget = max(0, COVER_BACKFILL_MAX_LOOKUPS_PER_REQUEST)
    for song in contract_songs:
        spotify_id = str(song.get('spotifyId') or '').strip()
        title = str(song.get('title') or '').strip().lower()
        artist = str(song.get('artist') or '').strip().lower()
        matched_doc = None

        if spotify_id and spotify_id in by_spotify_id:
            matched_doc = by_spotify_id[spotify_id]
        elif title and artist and (title, artist) in by_title_artist:
            matched_doc = by_title_artist[(title, artist)]

        enriched_song = dict(song)
        album_image = None
        if matched_doc:
            album_image = matched_doc.get('album_image') or matched_doc.get('cover_art_url') or matched_doc.get('coverArtUrl')
            if album_image:
                enriched_song['album_image'] = album_image
                enriched_song['cover_art_url'] = album_image
            if matched_doc.get('duration_ms') and not enriched_song.get('duration_ms'):
                enriched_song['duration_ms'] = matched_doc.get('duration_ms')
            if matched_doc.get('preview_url') and not enriched_song.get('preview_url'):
                enriched_song['preview_url'] = matched_doc.get('preview_url')
            if matched_doc.get('album') and not enriched_song.get('album'):
                enriched_song['album'] = matched_doc.get('album')

        # Autofill and persist missing art from Spotify so later requests don't repeat lookups.
        if not album_image and matched_doc and cover_lookup_budget > 0:
            cover_lookup_budget -= 1
            album_image = resolve_cover_from_ipfs_metadata(matched_doc.get('ipfs_cid'))
            if album_image:
                enriched_song['album_image'] = album_image
                enriched_song['cover_art_url'] = album_image
                try:
                    if matched_doc.get('id'):
                        registered_songs_collection.update_one(
                            {'_id': ObjectId(str(matched_doc.get('id')))},
                            {'$set': {
                                'album_image': album_image,
                                'cover_art_url': album_image,
                                'coverArtUrl': album_image
                            }}
                        )
                except Exception:
                    pass

        if not album_image and cover_lookup_budget > 0:
            cover_lookup_budget -= 1
            fetched_metadata = fetch_spotify_cover_metadata(
                enriched_song.get('title') or song.get('title') or '',
                enriched_song.get('artist') or song.get('artist') or '',
                spotify_id
            )
            if fetched_metadata and fetched_metadata.get('album_image'):
                fetched_image = fetched_metadata.get('album_image')
                enriched_song['album_image'] = fetched_image
                enriched_song['cover_art_url'] = fetched_image
                if fetched_metadata.get('duration_ms') and not enriched_song.get('duration_ms'):
                    enriched_song['duration_ms'] = fetched_metadata.get('duration_ms')
                if fetched_metadata.get('preview_url') and not enriched_song.get('preview_url'):
                    enriched_song['preview_url'] = fetched_metadata.get('preview_url')
                if fetched_metadata.get('album') and not enriched_song.get('album'):
                    enriched_song['album'] = fetched_metadata.get('album')

                set_fields = {
                    'album_image': fetched_image,
                    'cover_art_url': fetched_image,
                    'coverArtUrl': fetched_image
                }
                if fetched_metadata.get('duration_ms'):
                    set_fields['duration_ms'] = fetched_metadata.get('duration_ms')
                if fetched_metadata.get('preview_url'):
                    set_fields['preview_url'] = fetched_metadata.get('preview_url')
                if fetched_metadata.get('album'):
                    set_fields['album'] = fetched_metadata.get('album')

                try:
                    if matched_doc and matched_doc.get('id'):
                        registered_songs_collection.update_one(
                            {'_id': ObjectId(str(matched_doc.get('id')))},
                            {'$set': set_fields}
                        )
                    elif spotify_id:
                        registered_songs_collection.update_many(
                            {'spotify_id': spotify_id},
                            {'$set': set_fields}
                        )
                except Exception:
                    pass

        if not album_image and cover_lookup_budget > 0:
            cover_lookup_budget -= 1
            fetched_metadata = fetch_itunes_cover_metadata(
                enriched_song.get('title') or song.get('title') or '',
                enriched_song.get('artist') or song.get('artist') or ''
            )
            if fetched_metadata and fetched_metadata.get('album_image'):
                fetched_image = fetched_metadata.get('album_image')
                enriched_song['album_image'] = fetched_image
                enriched_song['cover_art_url'] = fetched_image
                if fetched_metadata.get('duration_ms') and not enriched_song.get('duration_ms'):
                    enriched_song['duration_ms'] = fetched_metadata.get('duration_ms')
                if fetched_metadata.get('preview_url') and not enriched_song.get('preview_url'):
                    enriched_song['preview_url'] = fetched_metadata.get('preview_url')
                if fetched_metadata.get('album') and not enriched_song.get('album'):
                    enriched_song['album'] = fetched_metadata.get('album')

                set_fields = {
                    'album_image': fetched_image,
                    'cover_art_url': fetched_image,
                    'coverArtUrl': fetched_image
                }
                if fetched_metadata.get('duration_ms'):
                    set_fields['duration_ms'] = fetched_metadata.get('duration_ms')
                if fetched_metadata.get('preview_url'):
                    set_fields['preview_url'] = fetched_metadata.get('preview_url')
                if fetched_metadata.get('album'):
                    set_fields['album'] = fetched_metadata.get('album')

                try:
                    if matched_doc and matched_doc.get('id'):
                        registered_songs_collection.update_one(
                            {'_id': ObjectId(str(matched_doc.get('id')))},
                            {'$set': set_fields}
                        )
                    elif spotify_id:
                        registered_songs_collection.update_many(
                            {'spotify_id': spotify_id},
                            {'$set': set_fields}
                        )
                except Exception:
                    pass

        enriched_songs.append(enriched_song)

    return jsonify(enriched_songs)

# =========================
# GET SINGLE SONG
# =========================
@app.route("/song/<int:song_id>")
def get_song(song_id):
    metadata = get_song_metadata(song_id)
    return jsonify(metadata if metadata else {})

# =========================
# USER LIBRARY
# =========================
@app.route("/library", methods=["GET"])
def get_library():
    wallet = request.args.get("wallet", None)
    library_items = []

    if wallet:
        purchases = list(purchase_history_collection.find({"wallet": {"$regex": f"^{wallet}$", "$options": "i"}}))
        for purchase in purchases:
            song_metadata = get_song_metadata(purchase.get("song_id"))
            matched_song_doc = resolve_registered_song_for_purchase(purchase.get("song_id"), purchase.get("song_title"))

            # Force a robust song-id match for older purchase rows that have generic titles (e.g. "Song 41").
            if not matched_song_doc or not (
                str(matched_song_doc.get("album_image") or matched_song_doc.get("cover_art_url") or matched_song_doc.get("coverArtUrl") or '').strip()
                and (matched_song_doc.get("duration_ms") or matched_song_doc.get("durationMs"))
            ):
                try:
                    sid = int(purchase.get("song_id"))
                    id_candidates = [sid, str(sid)]
                    candidate_query = {
                        '$or': [
                            {'blockchain_song_id': {'$in': id_candidates}},
                            {'song_id': {'$in': id_candidates}},
                            {'songId': {'$in': id_candidates}}
                        ]
                    }
                    candidates = list(registered_songs_collection.find(candidate_query).sort('registered_at', -1).limit(30))
                    if candidates:
                        def _candidate_score(doc):
                            score = 0
                            if str(doc.get('song_file_cid') or '').strip():
                                score += 100
                            if str(doc.get('album_image') or doc.get('cover_art_url') or doc.get('coverArtUrl') or '').strip():
                                score += 60
                            if doc.get('duration_ms') or doc.get('durationMs'):
                                score += 30
                            if str(doc.get('ipfs_cid') or '').strip():
                                score += 10
                            if str(doc.get('preview_url') or '').strip():
                                score += 5
                            return score

                        candidates.sort(key=lambda d: (_candidate_score(d), int(d.get('registered_at') or 0)), reverse=True)
                        if not matched_song_doc or _candidate_score(candidates[0]) > _candidate_score(matched_song_doc):
                            matched_song_doc = candidates[0]
                except Exception:
                    pass
            purchase_song_file_cid = str(purchase.get("song_file_cid") or '').strip()
            matched_song_file_cid = str((matched_song_doc or {}).get("song_file_cid") or '').strip()
            song_file_cid = matched_song_file_cid or purchase_song_file_cid
            if purchase_song_file_cid and not matched_song_file_cid:
                # Defensive cleanup for legacy rows where a CID from another title was persisted.
                try:
                    owner_doc = registered_songs_collection.find_one({'song_file_cid': purchase_song_file_cid})
                    owner_title = str((owner_doc or {}).get('songTitle') or '').strip().lower()
                    purchase_title = str(purchase.get('song_title') or '').strip().lower()
                    if owner_title and purchase_title and owner_title != purchase_title:
                        song_file_cid = ''
                except Exception:
                    pass

            purchase_ipfs_cid = str(purchase.get("ipfs_cid") or '').strip()
            matched_ipfs_cid = str((matched_song_doc or {}).get("ipfs_cid") or '').strip()
            ipfs_cid = matched_ipfs_cid or purchase_ipfs_cid
            preview_url = str(purchase.get("preview_url") or (matched_song_doc or {}).get("preview_url") or '').strip()
            audio_url = str(purchase.get("audio_url") or '').strip()
            if not audio_url and song_file_cid:
                audio_url = f"http://127.0.0.1:5000/ipfs/file/{song_file_cid}"
            audio_fallback_url = f"https://ipfs.io/ipfs/{song_file_cid}" if song_file_cid else ''
            audio_alt_gateway_url = f"https://gateway.pinata.cloud/ipfs/{song_file_cid}" if song_file_cid else ''

            title_for_lookup = (
                (song_metadata or {}).get("title")
                or purchase.get("song_title")
                or (matched_song_doc or {}).get("songTitle")
                or (matched_song_doc or {}).get("title")
                or ''
            )
            artist_for_lookup = (
                (song_metadata or {}).get("artist")
                or purchase.get("artist")
                or ((matched_song_doc or {}).get("artistNames") or [""])[0]
                if isinstance((matched_song_doc or {}).get("artistNames"), list)
                else (matched_song_doc or {}).get("artist")
                or ''
            )
            spotify_id = str(
                purchase.get("spotify_id")
                or (song_metadata or {}).get("spotifyId")
                or (matched_song_doc or {}).get("spotify_id")
                or (matched_song_doc or {}).get("spotifyId")
                or ''
            ).strip()

            album_image = str(
                purchase.get("album_image")
                or purchase.get("cover_art_url")
                or (matched_song_doc or {}).get("album_image")
                or (matched_song_doc or {}).get("cover_art_url")
                or (matched_song_doc or {}).get("coverArtUrl")
                or ''
            ).strip()
            if not album_image and ipfs_cid:
                album_image = str(resolve_cover_from_ipfs_metadata(ipfs_cid) or '').strip()

            if not album_image and song_file_cid:
                album_image = str(resolve_cover_from_ipfs_metadata(song_file_cid) or '').strip()

            duration_ms = (
                purchase.get("duration_ms")
                or purchase.get("durationMs")
                or (matched_song_doc or {}).get("duration_ms")
                or (matched_song_doc or {}).get("durationMs")
            )

            fetched_metadata = {}
            if (not album_image or not duration_ms) and (title_for_lookup or artist_for_lookup or spotify_id):
                fetched_metadata = fetch_spotify_cover_metadata(title_for_lookup, artist_for_lookup, spotify_id) or fetch_itunes_cover_metadata(title_for_lookup, artist_for_lookup) or {}
                if not album_image:
                    album_image = str(fetched_metadata.get("album_image") or '').strip()
                if not duration_ms:
                    duration_ms = fetched_metadata.get("duration_ms")
                if not preview_url:
                    preview_url = str(fetched_metadata.get("preview_url") or '').strip()

            purchase_title = str(purchase.get("song_title") or '').strip()
            matched_title = str((matched_song_doc or {}).get("songTitle") or (matched_song_doc or {}).get("title") or '').strip()
            metadata_title = str((song_metadata or {}).get("title") or '').strip()
            purchase_title_is_generic = purchase_title.lower().startswith('song ')
            title_value = matched_title if (purchase_title_is_generic and matched_title) else (purchase_title or matched_title or metadata_title or f"Song {purchase['song_id']}")

            metadata_artist = str((song_metadata or {}).get("artist") or '').strip()
            matched_artist_value = ''
            matched_artists = (matched_song_doc or {}).get("artistNames")
            if isinstance(matched_artists, list):
                matched_artist_value = ', '.join(str(a).strip() for a in matched_artists if str(a).strip())
            else:
                matched_artist_value = str((matched_song_doc or {}).get("artist") or '').strip()
            artist_value = str(purchase.get("artist") or '').strip() or matched_artist_value or metadata_artist or "Unknown Artist"

            purchase_album = str(purchase.get("album") or '').strip()
            matched_album = str((matched_song_doc or {}).get("album") or '').strip()
            fetched_album = str((fetched_metadata or {}).get("album") or '').strip()
            album_value = purchase_album if purchase_album and purchase_album.lower() != 'unknown album' else (matched_album or fetched_album or purchase_album or "Unknown Album")

            # Persist missing enrichment onto purchase rows to avoid repeated lookups.
            update_fields = {}
            if matched_song_file_cid and matched_song_file_cid != purchase_song_file_cid:
                update_fields["song_file_cid"] = matched_song_file_cid
            elif song_file_cid and not purchase_song_file_cid:
                update_fields["song_file_cid"] = song_file_cid
            if matched_ipfs_cid and matched_ipfs_cid != purchase_ipfs_cid:
                update_fields["ipfs_cid"] = matched_ipfs_cid
            elif ipfs_cid and not purchase_ipfs_cid:
                update_fields["ipfs_cid"] = ipfs_cid
            if purchase_song_file_cid and not song_file_cid:
                update_fields["song_file_cid"] = ''
            if preview_url and not str(purchase.get("preview_url") or '').strip():
                update_fields["preview_url"] = preview_url
            if audio_url and not str(purchase.get("audio_url") or '').strip():
                update_fields["audio_url"] = audio_url
            if album_image and not str(purchase.get("album_image") or '').strip():
                update_fields["album_image"] = album_image
            if album_image and not str(purchase.get("cover_art_url") or '').strip():
                update_fields["cover_art_url"] = album_image
            if duration_ms and not purchase.get("duration_ms"):
                update_fields["duration_ms"] = duration_ms
            if audio_fallback_url and not str(purchase.get("audio_fallback_url") or '').strip():
                update_fields["audio_fallback_url"] = audio_fallback_url
            if audio_alt_gateway_url and not str(purchase.get("audio_alt_gateway_url") or '').strip():
                update_fields["audio_alt_gateway_url"] = audio_alt_gateway_url
            if update_fields:
                try:
                    purchase_history_collection.update_one(
                        {"_id": purchase.get("_id")},
                        {"$set": update_fields}
                    )
                except Exception as update_error:
                    print(f"[WARN] Could not persist library enrichment for purchase {purchase.get('_id')}: {update_error}")

            library_items.append({
                "song_id": purchase["song_id"],
                "title": title_value,
                "artist": artist_value,
                "album": album_value,
                "type": purchase.get("type", "download"),
                "amount": purchase.get("amount", purchase.get("price", 0)),
                "tx_hash": purchase.get("tx_hash", "N/A"),
                "timestamp": purchase.get("timestamp", 0),
                "song_file_cid": song_file_cid,
                "ipfs_cid": ipfs_cid,
                "preview_url": preview_url,
                "audio_url": audio_url,
                "ipfs_gateway_url": f"https://ipfs.io/ipfs/{song_file_cid}" if song_file_cid else '',
                "audio_fallback_url": audio_fallback_url,
                "audio_alt_gateway_url": audio_alt_gateway_url,
                "album_image": album_image,
                "cover_art_url": album_image,
                "duration_ms": duration_ms
            })

    if not library_items:
        library_items = [
            {
                "song_id": 1,
                "title": "Song One",
                "artist": "DSignature",
                "album": "Fresh Album",
                "type": "download",
                "amount": "0.01",
                "tx_hash": "demo",
                "timestamp": 0,
                "status": "Preview"
            },
            {
                "song_id": 2,
                "title": "Song Two",
                "artist": "N Jay Dadz",
                "album": "Fine Line",
                "type": "rights",
                "amount": "0.03",
                "tx_hash": "demo",
                "timestamp": 0,
                "status": "Preview"
            }
        ]

    return jsonify(library_items)

# =========================
# CHECK WALLET BALANCE (READ ONLY)
# =========================
@app.route("/balance/<address>")
def balance(address):
    bal = w3.eth.get_balance(address)
    return jsonify({
        "address": address,
        "balance_AVAX": w3.from_wei(bal, "ether")
    })

# =========================
# METAMASK / WEB3 HEALTH CHECK
# =========================
@app.route("/health/metamask", methods=["GET"])
def health_metamask():
    """Diagnostic endpoint to verify MetaMask integration and blockchain connectivity."""
    return jsonify({
        "status": "ok",
        "blockchain": {
            "connected": w3.is_connected(),
            "rpc": RPC,
            "network_id": w3.net.version if w3.is_connected() else None
        },
        "smart_contract": {
            "address": SMART_MUSIC_ADDRESS if SMART_MUSIC_ADDRESS else "NOT_SET",
            "loaded": SMART_MUSIC_CONTRACT is not None,
            "abi_available": len(SMART_MUSIC_ABI) > 0
        },
        "features": {
            "artist_connection": "enabled (requires MetaMask signature)",
            "marketplace_invest": "enabled (requires MetaMask transaction)",
            "marketplace_buy": "enabled (requires MetaMask transaction)",
            "ipfs": "connected" if ipfs_client else "disconnected"
        },
        "message": "All systems operational" if (w3.is_connected() and SMART_MUSIC_CONTRACT) else "Some features may be unavailable"
    })


@app.route("/", methods=["GET"])
@app.route("/index.html", methods=["GET"])
def serve_index():
    resp = send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.route("/search_test.html", methods=["GET"])
def serve_search_test():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "search_test.html")

@app.route("/<path:filename>", methods=["GET"])
def serve_static_files(filename):
    """Serve static files (JS, CSS, HTML, images) from the project root."""
    root = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(root, filename)
    if os.path.isfile(filepath):
        return send_from_directory(root, filename)
    return jsonify({"error": "File not found"}), 404

@app.route("/health", methods=["GET"])
def health():
    """Lightweight health endpoint for frontend API base auto-detection."""
    runtime_port = None
    host_header = request.host or ""
    if ":" in host_header:
        try:
            runtime_port = int(host_header.rsplit(":", 1)[1])
        except ValueError:
            runtime_port = None

    return jsonify({
        "status": "ok",
        "service": "app",
        "port": runtime_port
    })

# ============================================================================
# DEBUG & TESTING ENDPOINTS
# ============================================================================

@app.route("/debug/status", methods=["GET"])
def debug_status():
    """System status check for testing and debugging"""
    try:
        # Check database
        try:
            db_status = "✓ Connected"
        except:
            db_status = "✗ Not Connected"
        
        # Check Spotify credentials
        spotify_status = "✓ Loaded" if client_id and client_secret else "✗ Missing"
        
        # Check blockchain
        blockchain_status = "✓ Connected" if w3.is_connected() else "✗ Disconnected"
        
        # Check IPFS
        try:
            ipfs_status = "✓ Connected" if ipfs_client else "✗ Not initialized"
        except:
            ipfs_status = "✗ Error"
        
        return jsonify({
            "server": "✓ Running",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "host": request.host,
            "database": db_status,
            "spotify": spotify_status,
            "blockchain": blockchain_status,
            "ipfs": ipfs_status,
            "environment": {
                "debug_mode": app.debug,
                "development": True
            }
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/debug/test-artist/<artist_name>", methods=["GET"])
def debug_test_artist(artist_name):
    """Test Spotify artist search - returns raw API response"""
    try:
        token = get_spotify_token()
        if not token:
            return jsonify({"error": "Failed to get Spotify token"}), 500
        
        headers = {'Authorization': f'Bearer {token}'}
        response = requests_get_with_retries(
            f'https://api.spotify.com/v1/search?q={artist_name}&type=artist&limit=5',
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            artists = data.get('artists', {}).get('items', [])
            return jsonify({
                "query": artist_name,
                "results_count": len(artists),
                "artists": [
                    {
                        "name": a.get('name'),
                        "id": a.get('id'),
                        "popularity": a.get('popularity'),
                        "followers": a.get('followers', {}).get('total', 0)
                    }
                    for a in artists
                ]
            }), 200
        else:
            return jsonify({"error": f"Spotify API returned {response.status_code}"}), response.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/debug/test-spotify-search/<query>", methods=["GET"])
def debug_spotify_search(query):
    """Full test of Spotify artist endpoint"""
    try:
        token = get_spotify_token()
        if not token:
            return jsonify({"error": "Failed to authenticate with Spotify"}), 500
        
        # Use the existing get_spotify_artist function
        result = requests_get_with_retries(
            f'http://127.0.0.1:5000/spotify/artist?artist={query}',
            timeout=15
        )
        
        if result.status_code == 200:
            data = result.json()
            return jsonify({
                "success": True,
                "artist": data.get('name'),
                "tracks_count": len(data.get('tracks', [])),
                "albums_count": len(data.get('albums', [])),
                "sample_tracks": [
                    {"name": t.get('name'), "album": t.get('album', {}).get('name')}
                    for t in data.get('tracks', [])[:3]
                ]
            }), 200
        else:
            return jsonify({"error": f"Request failed: {result.status_code}"}), result.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/debug/test-features", methods=["GET"])
def debug_test_features():
    """Test all major platform features"""
    try:
        return jsonify({
            "features": {
                "spotify_search": "/debug/test-artist/<artist_name>",
                "spotify_full": "/debug/test-spotify-search/<query>",
                "blockchain": {
                    "status": "✓ Connected" if w3.is_connected() else "✗ Disconnected",
                    "contract": "0x479Dc5252C08e70Ade655515eb6777Ed276552E6"
                },
                "ipfs": {
                    "status": "✓ Available" if ipfs_client is not None else "✗ Not initialized"
                },
                "database": {
                    "status": "✓ Connected" if True else "✗ Not connected",
                    "uri": MONGO_URI[:50] + "..." if len(MONGO_URI) > 50 else MONGO_URI
                }
            },
            "test_endpoints": [
                "GET /debug/status - System status check",
                "GET /debug/test-features - This endpoint",
                "GET /debug/test-artist/<name> - Test Spotify search",
                "GET /debug/test-spotify-search/<query> - Full artist search test",
                "GET /debug/network-info - Network and server info",
                "GET /debug - Interactive testing panel"
            ]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/debug/network-info", methods=["GET"])
def debug_network_info():
    """Get network and server information for debugging"""
    try:
        # Get local IP
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        
        return jsonify({
            "server": {
                "hostname": hostname,
                "local_ip": local_ip,
                "port": request.host.split(':')[1] if ':' in request.host else '5000',
                "request_host": request.host,
                "client_ip": request.remote_addr,
                "user_agent": request.user_agent.string
            },
            "access_urls": {
                "localhost": f"http://127.0.0.1:5000",
                "local_network": f"http://{local_ip}:5000",
                "from_client": f"http://{request.host}"
            }
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/debug", methods=["GET"])
def debug_panel():
    """Serve the debug testing panel"""
    try:
        with open('debug.html', 'r') as f:
            return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    except Exception as e:
        return jsonify({"error": f"Debug panel not found: {str(e)}"}), 404

if __name__ == "__main__":
    host = "0.0.0.0"
    try:
        port = int(os.getenv("PORT", "5004"))
    except ValueError:
        port = 5004

    # Keep debug mode on by default for development.
    debug_mode = os.getenv("FLASK_DEBUG", "1").strip() not in ("0", "false", "False", "no", "No")

    if _is_port_in_use(host, port):
        print(f"[ERROR] Port {port} is already in use on {host}. Stop the old process and restart.")
        sys.exit(1)
    try:
        run_one_off_song_audio_backfill_migration()
    except Exception as migration_error:
        print(f"[WARN] One-off song audio backfill migration failed: {migration_error}")

    use_waitress = os.getenv("USE_WAITRESS", "0").strip().lower() in ("1", "true", "yes", "on")
    if use_waitress:
        try:
            from waitress import serve
            threads = int(os.getenv("WAITRESS_THREADS", "8"))
            print(f"[INFO] Starting Waitress on {host}:{port} with {threads} threads")
            serve(app, host=host, port=port, threads=threads)
        except ImportError:
            print("[WARN] Waitress is not installed. Falling back to Flask development server.")
            app.run(host=host, port=port, debug=debug_mode, use_reloader=False)
    else:
        # Disable reloader to avoid duplicate startup processes and port races in VS Code terminals.
        app.run(host=host, port=port, debug=debug_mode, use_reloader=False)