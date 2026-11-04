# Blockchain Music Platform Setup Guide

## Overview
This platform lets fans discover Spotify songs and purchase rights using Avalanche blockchain.

## Features
- Spotify song search and discovery
- Download rights purchase (0.01 AVAX)
- Song investment via ERC20 tokens (1 AVAX per token)
- Full rights purchase (1000+ AVAX)
- Decentralized ownership tracking

## Prerequisites
- Node.js and npm
- Python 3.x with Flask
- MetaMask or compatible Web3 wallet
- Avalanche wallet with AVAX

## Setup Steps

### 1. Spotify API Setup
1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and create a new app.
2. Note your Client ID and Client Secret.
3. Set environment variables:
   ```bash
   export SPOTIFY_CLIENT_ID="your_client_id"
   export SPOTIFY_CLIENT_SECRET="your_client_secret"
   ```

### 2. Smart Contract Deployment
1. Add your Avalanche private key to `hardhat.config.js`:
   ```javascript
   avalanche: {
     url: "https://api.avax.network/ext/bc/C/rpc",
     accounts: ["0xYOUR_PRIVATE_KEY"]
   }
   ```
2. Deploy the contract:
   ```bash
   npx hardhat run scripts/deploy.js --network avalanche
   ```
3. Note the deployed contract address and update it in `wallet.js`.
4. (Optional) Create investment tokens for songs:
   ```bash
   npx hardhat run scripts/addSongs.js --network avalanche
   ```

### 3. Backend Setup
1. Install Python dependencies:
   ```bash
   pip install flask flask-cors web3 requests pymongo python-dotenv ipfshttpclient
   ```
2. Set Spotify environment variables as above.
3. Set the backend owner private key to allow song uploads from Spotify search results:
   ```bash
   export OWNER_PRIVATE_KEY="0xYOUR_OWNER_PRIVATE_KEY"
   ```
4. Run the backend:
   ```bash
   python app.py
   ```

### 4. Frontend Setup
1. Open `index.html` in a web browser.
2. Connect your MetaMask wallet.
3. Search for songs using the Spotify search.
4. Purchase rights using the available buttons.

## Contract Functions

### Song Management
- `addSongFromSpotify()` - Add songs from Spotify metadata
- `createInvestmentToken()` - Create ERC20 tokens for song investment

### Purchase Types
- `buyDownloadRights()` - One-time download access (0.01 AVAX)
- `investInSong()` - Buy investment tokens (1 AVAX per token)
- `buyFullRights()` - Purchase complete ownership (1000+ AVAX)

### Queries
- `getSong()` - Get song details
- `hasDownloadRights()` - Check if user owns download rights

## Security Notes
- Never commit private keys to version control
- Use environment variables for API keys
- Use `OWNER_PRIVATE_KEY` in the backend to allow Spotify songs to be uploaded to the contract from the UI
- Test on Avalanche Fuji testnet before mainnet
- Backend handles Spotify API calls securely

## Future Enhancements
- IPFS integration for music file storage
- Royalty distribution automation
- Multi-signature governance for rights changes
- NFT integration for collectible song ownership