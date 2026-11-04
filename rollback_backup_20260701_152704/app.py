import os
import secrets
import time
import hashlib
import socket
import sys
from flask import Flask, session, request, jsonify, make_response
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
        # 1 AVAX per token (customize as needed)
        value_wei = Web3.to_wei(float(token_amount), 'ether')
        tx = SMART_MUSIC_CONTRACT.functions.investInSong(int(song_id), int(token_amount)).build_transaction({
            'from': wallet,
            'nonce': w3.eth.get_transaction_count(wallet),
            'value': value_wei,
            'gas': 300000,
            'gasPrice': w3.eth.gas_price
        })
        return jsonify({'success': True, 'tx': tx})
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
        song = SMART_MUSIC_CONTRACT.functions.getSong(int(song_id)).call()
        download_price_wei = song[5]
        tx = SMART_MUSIC_CONTRACT.functions.buyDownloadRights(int(song_id)).build_transaction({
            'from': wallet,
            'nonce': w3.eth.get_transaction_count(wallet),
            'value': download_price_wei,
            'gas': 200000,
            'gasPrice': w3.eth.gas_price
        })
        return jsonify({'success': True, 'tx': tx})
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
        song = SMART_MUSIC_CONTRACT.functions.getSong(int(song_id)).call()
        full_rights_price_wei = song[6]
        tx = SMART_MUSIC_CONTRACT.functions.buyFullRights(int(song_id)).build_transaction({
            'from': wallet,
            'nonce': w3.eth.get_transaction_count(wallet),
            'value': full_rights_price_wei,
            'gas': 200000,
            'gasPrice': w3.eth.gas_price
        })
        return jsonify({'success': True, 'tx': tx})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/marketplace/add_spotify_song', methods=['POST'])
def add_spotify_song():
    data = request.get_json()
    title = data.get('title')
    artist = data.get('artist')
    spotify_id = data.get('spotify_id')
    download_price = data.get('download_price', 0.01)  # Default 0.01 AVAX
    full_rights_price = data.get('full_rights_price', 1000)  # Default 1000 AVAX
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
        return jsonify({'success': True, 'tx': tx})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/marketplace/upload_spotify_song', methods=['POST'])
def upload_spotify_song():
    data = request.get_json()
    title = data.get('title')
    artist = data.get('artist')
    spotify_id = data.get('spotify_id')
    download_price = data.get('download_price', 0.01)
    full_rights_price = data.get('full_rights_price', 1000)
    requester_wallet = data.get('requester_wallet')

    if not (title and artist and spotify_id):
        return jsonify({'success': False, 'error': 'Missing required fields.'}), 400

    if not ipfs_client:
        return jsonify({'success': False, 'error': 'IPFS client is not available.'}), 500

    try:
        metadata = {
            'title': title,
            'artist': artist,
            'spotify_id': spotify_id,
            'download_price': download_price,
            'full_rights_price': full_rights_price,
            'requester_wallet': requester_wallet,
            'source': 'spotify',
            'storage': 'ipfs',
            'uploaded_at': int(time.time())
        }

        ipfs_result = upload_json_to_ipfs(metadata)
        ipfs_cid = ipfs_result.get('Hash') if isinstance(ipfs_result, dict) else ipfs_result

        registered_songs_collection.insert_one({
            'songTitle': title,
            'artistNames': [artist],
            'spotify_id': spotify_id,
            'download_price': download_price,
            'full_rights_price': full_rights_price,
            'requester_wallet': requester_wallet,
            'ipfs_cid': ipfs_cid,
            'storage': 'ipfs',
            'source': 'spotify',
            'registered_at': int(time.time())
        })

        print(f"Spotify song metadata uploaded to IPFS: {title} by {artist} -> {ipfs_cid}")

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

        response = {'success': True, 'ipfs_cid': ipfs_cid, 'storage': 'ipfs'}
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
try:
    ipfs_client = ipfshttpclient.connect(IPFS_API)
    print('Connected to IPFS node:', IPFS_API)
except Exception as e:
    ipfs_client = None
    print('Failed to connect to IPFS:', e)

# Utility: Upload file to IPFS
def upload_file_to_ipfs(filepath):
    if not ipfs_client:
        raise Exception('IPFS client not available')
    res = ipfs_client.add(filepath)
    return res['Hash']


# Utility: Upload metadata (dict) to IPFS
def upload_json_to_ipfs(data):
    if not ipfs_client:
        raise Exception('IPFS client not available')
    res = ipfs_client.add_json(data)
    return res

# Collections
admin_contacts_collection = db['admin_contacts']
songs_collection = db['songs']
purchase_history_collection = db['purchase_history']
registered_songs_collection = db['registered_songs']
admin_sessions_collection = db['admin_sessions']

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
    # Accepts form-data: idNumber (string), userAddress (optional), idDocument (file)
    if 'idDocument' not in request.files:
        return jsonify({'success': False, 'error': 'No idDocument file provided.'}), 400
    id_doc = request.files['idDocument']
    id_number = request.form.get('idNumber', '').strip()
    user_address = request.form.get('userAddress', '').strip()

    if not id_number:
        return jsonify({'success': False, 'error': 'idNumber is required.'}), 400

    if id_doc.filename == '':
        return jsonify({'success': False, 'error': 'No selected file.'}), 400

    filename = secure_filename(id_doc.filename)
    stored_name = f"{int(time.time())}_{secrets.token_hex(8)}_{filename}"
    filepath = os.path.join(UPLOAD_DIR, stored_name)

    # retention/expiry
    retention_days = int(os.getenv('ID_RETENTION_DAYS', '30'))
    expires_at = time.time() + retention_days * 86400

    try:
        id_doc.save(filepath)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to save file: {e}'}), 500

    record = {
        'id_number': id_number,
        'user_address': user_address if user_address else None,
        'filename': stored_name,
        'original_filename': filename,
        'filepath': filepath,
        's3_key': None,
        's3_bucket': None,
        'status': 'pending',
        'submitted_at': time.time(),
        'submitted_at_iso': datetime.datetime.utcnow().isoformat(),
        'expires_at': expires_at
    }

    # If S3 configured, upload and remove local copy (keep local until uploaded)
    if s3_client and S3_BUCKET:
        try:
            s3_key = f"id-uploads/{stored_name}"
            s3_client.upload_file(filepath, S3_BUCKET, s3_key)
            record['s3_key'] = s3_key
            record['s3_bucket'] = S3_BUCKET
            # remove local file to harden storage
            try:
                os.remove(filepath)
                record['filepath'] = None
            except Exception:
                pass
        except Exception as e:
            # keep local file; log and continue
            print('S3 upload failed:', e)

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
        # If stored in S3, redirect to a presigned URL (valid briefly)
        if rec.get('s3_key') and s3_client and S3_BUCKET:
            try:
                url = s3_client.generate_presigned_url('get_object', Params={'Bucket': rec.get('s3_bucket'), 'Key': rec.get('s3_key')}, ExpiresIn=300)
                return jsonify({'status': 'success', 'url': url})
            except Exception as e:
                print('Presign failed', e)
                return jsonify({'status': 'error', 'message': 'Failed to generate download link'}), 500
        path = rec.get('filepath')
        if not path or not os.path.exists(path):
            return jsonify({'status': 'error', 'message': 'File not available'}), 404
        from flask import send_file
        return send_file(path, as_attachment=True, download_name=rec.get('original_filename'))
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
w3 = Web3(Web3.HTTPProvider(RPC))

print("Blockchain connected:", w3.is_connected())

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

if SMART_MUSIC_ADDRESS_CHECKSUM and w3.is_connected():
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


def from_wei(wei_value):
    try:
        return float(w3.from_wei(wei_value, "ether"))
    except Exception:
        return 0.0



def get_song_metadata(song_id):
    if not SMART_MUSIC_CONTRACT:
        return None
    try:
        song_id = int(song_id)
        song = SMART_MUSIC_CONTRACT.functions.getSong(song_id).call()
        return {
            "id": song[0],
            "title": song[1],
            "artist": song[2],
            "spotifyId": song[3],
            "owner": song[4],
            "downloadPrice": song[5],
            "fullRightsPrice": song[6],
            "investmentToken": song[7],
            "exists": song[8]
        }
    except Exception as e:
        print(f"Error fetching song metadata for song {song_id}:", e)
        return None


def get_contract_songs():
    if not SMART_MUSIC_CONTRACT:
        return []
    try:
        next_id = SMART_MUSIC_CONTRACT.functions.nextSongId().call()
        songs = []
        for song_id in range(1, int(next_id)):
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
    data = request.json

    song_id = data["song_id"]
    wallet = data["wallet"]
    tx_hash = data.get("tx_hash", "N/A")
    purchase_type = data.get("type", "download")  # download, invest, rights
    amount = data.get("amount", 0)

    metadata = get_song_metadata(song_id)
    song_title = metadata["title"] if metadata and metadata.get("exists") else f"Song {song_id}"

    if not amount:
        if purchase_type == "download" and metadata:
            amount = from_wei(metadata.get("downloadPrice", 0))
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
        "wallet": wallet,
        "tx_hash": tx_hash,
        "type": purchase_type,
        "amount": amount,
        "price": amount,
        "timestamp": int(time.time())
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
        "amount": amount
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
            "registered_at": int(time.time())
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

        registered_songs_collection.insert_one(registration_record)

        print(f"Registered song: {title}, Society={society}, CAE/IPI={cae_ipi}, ISRC={isrc}, IPFS CID={ipfs_metadata_cid}, Song CID={ipfs_song_cid}")

        return jsonify({
            "status": "success",
            "message": f"Song registered successfully with {society}.",
            "song": registration_record
        }), 201
    except Exception as e:
        import traceback
        print("Song registration failed:", e)
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Song registration failed: {e}"}), 500


@app.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(force=True)
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"status": "error", "message": "Username and password required."}), 400
    user = users_collection.find_one({'username': username, 'is_admin': True})
    if not user or not check_password_hash(user['password'], password):
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
    if not validate_admin_token(token):
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    return jsonify({
        "status": "success",
        "count": registered_songs_collection.count_documents({}),
        "songs": list(registered_songs_collection.find())
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
                'preview_url': 'https://p.scdn.co/mp3-preview/example1',
                'external_url': 'https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh'
            },
            {
                'id': '11dFghVXANMlKmJXsNCbNl',
                'title': 'Watermelon Sugar',
                'artist': 'Harry Styles',
                'album': 'Fine Line',
                'preview_url': 'https://p.scdn.co/mp3-preview/example2',
                'external_url': 'https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl'
            },
            {
                'id': '0VjIjW4GlUZAMYd2vXMi3b',
                'title': 'Levitating',
                'artist': 'Dua Lipa',
                'album': 'Future Nostalgia',
                'preview_url': 'https://p.scdn.co/mp3-preview/example3',
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
                'external_url': item['external_urls']['spotify']
            })
        return jsonify(tracks)
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

        return jsonify({
            "status": "connected",
            "message": "Spotify API connected successfully"
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500







@app.route("/spotify/artist", methods=["GET"])
def get_spotify_artist():
    query = request.args.get('artist', '')
    if not query:
        return jsonify({"error": "Artist query parameter required"}), 400

    if USE_MOCK_DATA:
        mock_artist_info = {
            'The Weeknd': {
                'id': '1Xyo4u8uXC1ZmMpatF05PJ',
                'name': 'The Weeknd',
                'tracks': [
                    {'name': 'Blinding Lights', 'album': {'name': 'After Hours'}, 'external_urls': {'spotify': 'https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh'}},
                    {'name': 'Save Your Tears', 'album': {'name': 'After Hours'}, 'external_urls': {'spotify': 'https://open.spotify.com/track/4kQ0Y4xW7OsNxaGQDSNM2e'}}
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
                    {'name': 'Watermelon Sugar', 'album': {'name': 'Fine Line'}, 'external_urls': {'spotify': 'https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl'}},
                    {'name': 'Adore You', 'album': {'name': 'Fine Line'}, 'external_urls': {'spotify': 'https://open.spotify.com/track/6KImCVD70vtIoJWnq6nGn3'}}
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
        print("[ERROR] Failed to get Spotify access token")
        return jsonify({"error": "Failed to get Spotify access token"}), 500

    headers = {'Authorization': f'Bearer {token}'}
    
    query_clean = " ".join(query.strip().split())
    is_probable_artist_id = len(query_clean) == 22 and query_clean.isalnum()

    artist = None
    if is_probable_artist_id:
        try:
            artist_resp = requests_get_with_retries(
                f'https://api.spotify.com/v1/artists/{query_clean}',
                headers=headers,
                timeout=10,
                retries=3
            )
            if artist_resp.status_code == 200:
                artist = artist_resp.json()
        except requests.Timeout:
            return jsonify({"error": "Spotify API request timed out"}), 504
        except Exception as e:
            return jsonify({"error": f"Failed to load artist by ID: {str(e)}"}), 500

    if artist is None:
        try:
            artist_search = requests_get_with_retries(
                f'https://api.spotify.com/v1/search?q={query_clean}&type=artist&limit=50',
                headers=headers,
                timeout=10,
                retries=3
            )
        except requests.Timeout:
            return jsonify({"error": "Spotify API request timed out"}), 504
        except Exception as e:
            return jsonify({"error": f"Failed to search artist: {str(e)}"}), 500

        print("[DEBUG] Artist search status:", artist_search.status_code)
        if artist_search.status_code != 200:
            print("[ERROR] Failed to search artist on Spotify")
            return jsonify({"error": "Failed to search artist on Spotify"}), artist_search.status_code

        data = artist_search.json()
        artists = data.get('artists', {}).get('items', [])
        if not artists:
            print("[ERROR] Artist not found in Spotify response")
            return jsonify({"error": "Artist not found"}), 404

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
            f'https://api.spotify.com/v1/artists/{artist_id}/top-tracks?market=US',
            headers=headers,
            timeout=8,
            retries=2
        )
        print("[DEBUG] Top tracks status:", top_tracks_resp.status_code)
        if top_tracks_resp.status_code == 200:
            top_tracks = top_tracks_resp.json().get('tracks', [])
    except (requests.Timeout, requests.RequestException):
        top_tracks = []

    try:
        albums_resp = requests_get_with_retries(
            f'https://api.spotify.com/v1/artists/{artist_id}/albums?include_groups=album,single&market=US&limit=30',
            headers=headers,
            timeout=8,
            retries=2
        )
        print("[DEBUG] Albums status:", albums_resp.status_code)
        if albums_resp.status_code == 200:
            albums = albums_resp.json().get('items', [])
    except (requests.Timeout, requests.RequestException):
        albums = []

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
            'album': {
                'id': album_obj.get('id'),
                'name': album_obj.get('name'),
                'image': images[0].get('url') if images else album_image_map.get(album_obj.get('id')),
                'type': album_obj.get('album_type') or album_obj.get('type') or 'album'
            },
            'external_urls': track.get('external_urls', {})
        }

    # Limit total tracks collected
    MAX_ARTIST_TRACKS = 80
    MAX_ALBUMS_TO_EXPAND = 8
    for album in albums[:MAX_ALBUMS_TO_EXPAND]:
        album_id = album.get('id')
        if not album_id:
            continue
        try:
            album_tracks_resp = requests_get_with_retries(
                f'https://api.spotify.com/v1/albums/{album_id}/tracks?market=US&limit=30',
                headers=headers,
                timeout=5,
                retries=2
            )
            if album_tracks_resp.status_code == 200:
                for t in album_tracks_resp.json().get('items', []):
                    if t['id'] not in track_map:
                        # attach album info from parent album
                        track_map[t['id']] = {
                            'id': t['id'],
                            'name': t.get('name'),
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
        except (requests.Timeout, requests.RequestException):
            # Skip this album if timeout, continue with others
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
            'external_urls': t.get('external_urls', {}),
            'id': t.get('id')
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
    return jsonify(get_contract_songs())

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
            library_items.append({
                "song_id": purchase["song_id"],
                "title": song_metadata["title"] if song_metadata and song_metadata.get("exists") else purchase.get("song_title", f"Song {purchase['song_id']}"),
                "artist": song_metadata["artist"] if song_metadata and song_metadata.get("exists") else purchase.get("artist", "Unknown Artist"),
                "album": purchase.get("album", "Unknown Album"),
                "type": purchase.get("type", "download"),
                "amount": purchase.get("amount", purchase.get("price", 0)),
                "tx_hash": purchase.get("tx_hash", "N/A"),
                "timestamp": purchase.get("timestamp", 0)
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

if __name__ == "__main__":
    host = "127.0.0.1"
    try:
        port = int(os.getenv("PORT", "5004"))
    except ValueError:
        port = 5004

    # Keep debug mode on by default for development.
    debug_mode = os.getenv("FLASK_DEBUG", "1").strip() not in ("0", "false", "False", "no", "No")

    if _is_port_in_use(host, port):
        print(f"[ERROR] Port {port} is already in use on {host}. Stop the old process and restart.")
        sys.exit(1)
    # Disable reloader to avoid duplicate startup processes and port races in VS Code terminals.
    app.run(host=host, port=port, debug=debug_mode, use_reloader=False)