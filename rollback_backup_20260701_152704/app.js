// Auto-resolve backend base URL so frontend still works if Flask falls back to another port.
const DEFAULT_API_BASE = 'http://127.0.0.1:5004';
const API_BASE_STORAGE_KEY = 'API_BASE';
let apiBasePromise = null;
const nativeFetch = (typeof window !== 'undefined' && typeof window.fetch === 'function')
    ? window.fetch.bind(window)
    : null;

function withTimeout(ms = 1200) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);
    return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function isApiHealthy(baseUrl) {
    if (!nativeFetch) return false;
    const timeout = withTimeout();
    try {
        const response = await nativeFetch(`${baseUrl}/health`, {
            method: 'GET',
            signal: timeout.signal
        });
        return response.ok;
    } catch (_) {
        return false;
    } finally {
        timeout.clear();
    }
}

async function resolveApiBase(options = {}) {
    const forceRefresh = Boolean(options.forceRefresh);
    const saved = localStorage.getItem(API_BASE_STORAGE_KEY);
    if (!forceRefresh && saved && await isApiHealthy(saved)) {
        return saved;
    }

    const origin = window.location.origin;
    if (origin && origin.startsWith('http') && await isApiHealthy(origin)) {
        localStorage.setItem(API_BASE_STORAGE_KEY, origin);
        return origin;
    }

    const candidatePorts = [5004, 5000, 5001, 5002, 5003, 5005, 5010];
    for (const port of candidatePorts) {
        const candidate = `http://127.0.0.1:${port}`;
        if (await isApiHealthy(candidate)) {
            localStorage.setItem(API_BASE_STORAGE_KEY, candidate);
            return candidate;
        }
    }

    // Do not keep routing to a stale saved port when all health checks fail.
    localStorage.removeItem(API_BASE_STORAGE_KEY);
    return DEFAULT_API_BASE;
}

if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
    const originalFetch = nativeFetch;

    window.fetch = async function (input, init) {
        let target = typeof input === 'string' ? input : input?.url;
        if (typeof target === 'string' && target.startsWith(DEFAULT_API_BASE)) {
            if (!apiBasePromise) {
                apiBasePromise = resolveApiBase().catch(() => DEFAULT_API_BASE);
            }
            const base = await apiBasePromise;
            const rewritten = target.replace(DEFAULT_API_BASE, base || DEFAULT_API_BASE);

            try {
                if (typeof input === 'string') {
                    input = rewritten;
                } else {
                    input = new Request(rewritten, input);
                }
                return await originalFetch(input, init);
            } catch (error) {
                // One retry with forced API base refresh if the cached base is no longer alive.
                apiBasePromise = resolveApiBase({ forceRefresh: true }).catch(() => DEFAULT_API_BASE);
                const refreshedBase = await apiBasePromise;
                const retryUrl = target.replace(DEFAULT_API_BASE, refreshedBase || DEFAULT_API_BASE);
                if (typeof input === 'string') {
                    input = retryUrl;
                } else {
                    input = new Request(retryUrl, input);
                }
                return originalFetch(input, init);
            }
        }
        return originalFetch(input, init);
    };
}

// Admin Contact Form Submission
document.addEventListener('DOMContentLoaded', function () {
    // Enforce login on all pages except login.html
    const isLoginPage = window.location.pathname.endsWith('login.html');
    const isLogoutPage = window.location.pathname.endsWith('logout.html');
    if (!isLoginPage && !isLogoutPage) {
        if (!localStorage.getItem('isLoggedIn')) {
            window.location.href = 'login.html';
            return;
        }
    }

    // Admin Contact Form Submission
    const adminContactForm = document.getElementById('adminContactForm');
    if (adminContactForm) {
        adminContactForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            const name = document.getElementById('adminName').value.trim();
            const email = document.getElementById('adminEmail').value.trim();
            const message = document.getElementById('adminMessage').value.trim();
            const status = document.getElementById('adminContactStatus');
            status.textContent = 'Sending...';
            try {
                const response = await fetch('http://127.0.0.1:5000/admin/contact', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, email, message })
                });
                const data = await response.json();
                if (response.ok && data.success) {
                    status.textContent = 'Message sent successfully!';
                    adminContactForm.reset();
                } else {
                    status.textContent = data.error || 'Failed to send message.';
                }
            } catch (err) {
                status.textContent = 'Failed to send message.';
            }
        });
    }

    // Login form logic (only on login.html)
    if (isLoginPage) {
        const loginForm = document.querySelector('.login-card form');
        if (loginForm) {
            loginForm.addEventListener('submit', function (e) {
                e.preventDefault();
                // You can add real authentication here
                localStorage.setItem('isLoggedIn', 'true');
                window.location.href = 'index.html';
            });
        }
        // Register form logic (optional, for demo)
        const registerForm = document.querySelector('.register-card form');
        if (registerForm) {
            registerForm.addEventListener('submit', function (e) {
                e.preventDefault();
                // You can add real registration here
                localStorage.setItem('isLoggedIn', 'true');
                window.location.href = 'index.html';
            });
        }
    }

    // Logout logic (only on logout.html)
    if (isLogoutPage) {
        localStorage.removeItem('isLoggedIn');
        localStorage.removeItem('userWallet');
        window.userWallet = undefined;
    }
});
// Wallet connection logic
let provider;
let signer;
let accounts = [];

// Avalanche C-Chain (Fuji testnet) parameters.
const TARGET_CHAIN_ID = '0xA869';
const AVALANCHE_C_CHAIN_PARAMS = {
    chainId: TARGET_CHAIN_ID,
    chainName: 'Avalanche Fuji C-Chain',
    nativeCurrency: { name: 'Avalanche', symbol: 'AVAX', decimals: 18 },
    rpcUrls: ['https://api.avax-test.network/ext/bc/C/rpc'],
    blockExplorerUrls: ['https://testnet.snowtrace.io']
};

async function ensureAvalancheCChain() {
    if (!window.ethereum) return false;
    try {
        const currentChainId = await window.ethereum.request({ method: 'eth_chainId' });
        if (currentChainId === TARGET_CHAIN_ID) {
            return true;
        }

        try {
            await window.ethereum.request({
                method: 'wallet_switchEthereumChain',
                params: [{ chainId: TARGET_CHAIN_ID }]
            });
            return true;
        } catch (switchError) {
            if (switchError && switchError.code === 4902) {
                await window.ethereum.request({
                    method: 'wallet_addEthereumChain',
                    params: [AVALANCHE_C_CHAIN_PARAMS]
                });
                return true;
            }
            throw switchError;
        }
    } catch (error) {
        console.error('Failed to switch to Avalanche C-Chain:', error);
        return false;
    }
}

async function connectWallet() {
    // Require login before wallet connect
    if (!localStorage.getItem('isLoggedIn')) {
        alert('You must log in before connecting your wallet.');
        window.location.href = 'login.html';
        return;
    }
    if (typeof window.ethereum !== 'undefined') {
        try {
            const switched = await ensureAvalancheCChain();
            if (!switched) {
                alert('Please switch MetaMask to Avalanche C-Chain (Fuji).');
                return;
            }

            provider = new ethers.BrowserProvider(window.ethereum);
            await provider.send("eth_requestAccounts", []);
            signer = await provider.getSigner();
            accounts = await provider.listAccounts();

            const address = accounts[0].address;
            document.getElementById("walletAddress").innerText = `Connected: ${address.slice(0, 6)}...${address.slice(-4)}`;
            document.getElementById("connectBtn").innerText = "Connected";

            window.userWallet = address;
            localStorage.setItem('userWallet', address);
        } catch (error) {
            console.error("Error connecting wallet:", error);
            alert("Failed to connect wallet");
        }
    } else {
        alert("Please install MetaMask or another Web3 wallet");
    }
}

// Event listener for connect button
const connectBtn = document.getElementById("connectBtn");
if (connectBtn) {
    connectBtn.addEventListener("click", connectWallet);
    // On page load, if logged in and wallet previously connected, restore wallet address
    if (localStorage.getItem('isLoggedIn') && localStorage.getItem('userWallet')) {
        window.userWallet = localStorage.getItem('userWallet');
        document.getElementById("walletAddress").innerText = `Connected: ${window.userWallet.slice(0, 6)}...${window.userWallet.slice(-4)}`;
        document.getElementById("connectBtn").innerText = "Connected";
    }
}

// Spotify search functionality
async function initializeSpotify() {
    const statusEl = document.getElementById("spotifyStatus");
    statusEl.textContent = "Spotify status: initializing...";

    try {
        const response = await fetch(`http://127.0.0.1:5000/spotify/init`);
        const data = await response.json();
        if (response.ok) {
            statusEl.textContent = `Spotify status: ${data.status} - ${data.message}`;
        } else {
            statusEl.textContent = `Spotify status: error - ${data.message || 'Initialization failed'}`;
        }
    } catch (error) {
        console.error("Error initializing Spotify:", error);
        statusEl.textContent = "Spotify status: failed to initialize";
    }
}

let currentSearch = '';
let currentOffset = 0;
const SEARCH_PAGE_SIZE = 10;

async function searchSpotify(reset = true) {
    const query = document.getElementById("spotifySearch").value;
    if (!query) {
        alert("Please enter a search term");
        return;
    }

    if (reset) {
        currentSearch = query;
        currentOffset = 0;
        document.getElementById('searchResults').innerHTML = '';
    }

    const spinner = getOrCreateSpinner();
    spinner.style.display = 'block';

    try {
        const response = await fetch(`http://127.0.0.1:5000/spotify/search?q=${encodeURIComponent(currentSearch)}&limit=${SEARCH_PAGE_SIZE}&offset=${currentOffset}`);
        const tracks = await response.json();

        appendSearchResults(tracks || []);

        // If we received a full page, show Load more button
        const loadMoreBtn = document.getElementById('loadMoreSearchBtn');
        if (tracks && tracks.length === SEARCH_PAGE_SIZE) {
            if (!loadMoreBtn) {
                const btn = document.createElement('button');
                btn.id = 'loadMoreSearchBtn';
                btn.textContent = 'Load more';
                btn.onclick = () => { currentOffset += SEARCH_PAGE_SIZE; searchSpotify(false); };
                document.getElementById('searchResults').after(btn);
            }
        } else {
            if (loadMoreBtn) loadMoreBtn.remove();
        }
    } catch (error) {
        console.error("Error searching Spotify:", error);
        document.getElementById("searchResults").innerHTML = "Search failed";
    } finally {
        spinner.style.display = 'none';
    }
}

function getOrCreateSpinner() {
    let spinner = document.getElementById('searchSpinner');
    if (!spinner) {
        spinner = document.createElement('div');
        spinner.id = 'searchSpinner';
        spinner.style.display = 'none';
        spinner.style.margin = '8px 0';
        spinner.textContent = 'Loading...';
        const container = document.getElementById('searchResults');
        container.parentNode.insertBefore(spinner, container);
    }
    return spinner;
}

function appendSearchResults(tracks) {
    const container = document.getElementById("searchResults");
    if (!container) return;
    if (!tracks || tracks.length === 0) {
        if (currentOffset === 0) container.innerHTML = '<p>No results found.</p>';
        return;
    }

    const html = tracks.map(track => `
        <div class="track-result" data-track-id="${track.id || track.spotify_id}" data-track-name="${(track.title || track.name).replace(/"/g, '&quot;')}" data-track-artist="${(track.artist || '').replace(/"/g, '&quot;')}" style="display:flex; gap:12px; align-items:flex-start;">
            ${track.album_image ? `<img src="${track.album_image}" alt="cover" style="width:120px; height:120px; object-fit:cover; border-radius:6px;" />` : '<div style="width:120px; height:120px; background:#eee; border-radius:6px;"></div>'}
            <div style="flex:1;">
                <h3 style="margin:0 0 4px 0;">${track.title || track.name}</h3>
                <p style="margin:0 0 4px 0; font-size:0.9em; color:#666;">Artist: ${track.artist || ''}</p>
                <p style="margin:0 0 8px 0; font-size:0.9em; color:#666;">Album: ${track.album || (track.album && track.album.name) || ''}</p>
                <button class="search-initialise-btn">Initialise</button>
            </div>
        </div>
    `).join("");

    if (currentOffset === 0) container.innerHTML = html; else container.insertAdjacentHTML('beforeend', html);
    
    // Attach event listeners for search Initialise buttons
    document.querySelectorAll('.search-initialise-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const row = this.closest('.track-result');
            const trackName = row.dataset.trackName;
            const trackArtist = row.dataset.trackArtist;
            const trackId = row.dataset.trackId;
            addSpotifySongToBlockchain(trackName, trackArtist, trackId);
        });
    });
}

const ACTIVE_INITIALISED_CARD_ID = 'activeInitialisedSongCard';

function toSafeId(value) {
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '_');
}

function ensureInitialisedSongsSection() {
    const container = document.getElementById('initialisedSongsList');
    if (!container) return null;

    if (!container.dataset.bound) {
        container.dataset.bound = 'true';
        container.addEventListener('click', async (event) => {
            const actionBtn = event.target.closest('button[data-action]');
            if (!actionBtn) return;

            const songId = actionBtn.dataset.songId;
            if (!songId) return;

            if (actionBtn.dataset.action === 'download') {
                await performMarketplaceAction('/marketplace/buy_download', { song_id: Number(songId) }, `Downloaded rights for song #${songId}`);
                return;
            }

            if (actionBtn.dataset.action === 'rights') {
                await performMarketplaceAction('/marketplace/buy_rights', { song_id: Number(songId) }, `Bought full rights for song #${songId}`);
                return;
            }

            if (actionBtn.dataset.action === 'invest') {
                const input = document.getElementById(`tokenAmount_${toSafeId(songId)}`);
                const amount = Number(input?.value || 0);
                if (!amount || amount <= 0) {
                    alert('Enter a valid token amount to invest.');
                    return;
                }
                await performMarketplaceAction(
                    '/marketplace/invest',
                    { song_id: Number(songId), token_amount: amount },
                    `Invested ${amount} token(s) in song #${songId}`
                );
            }
        });
    }

    return container;
}

function addSongToListenInvestSection(song) {
    const container = ensureInitialisedSongsSection();
    if (!container || !song || song.songId === undefined || song.songId === null) return;

    const songId = Number(song.songId);
    if (!Number.isFinite(songId)) return;

    const tokenInputId = `tokenAmount_${toSafeId(songId)}`;
    const existingCard = document.getElementById(ACTIVE_INITIALISED_CARD_ID);
    if (existingCard) {
        existingCard.remove();
    }

    const card = document.createElement('div');
    card.id = ACTIVE_INITIALISED_CARD_ID;
    card.className = 'card';
    card.style.margin = '12px 0';
    card.innerHTML = `
        <h3>${song.title || 'Unknown Title'}</h3>
        <p>${song.artist || 'Unknown Artist'} | Song ID: ${songId}</p>
        <p>Download: ${song.downloadPrice ?? 'N/A'} AVAX | Full Rights: ${song.fullRightsPrice ?? 'N/A'} AVAX</p>
        <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
            <button type="button" data-action="download" data-song-id="${songId}">Buy Download Rights</button>
            <input id="${tokenInputId}" type="number" min="1" step="1" value="100" style="width:110px;" />
            <button type="button" data-action="invest" data-song-id="${songId}">Invest</button>
            <button type="button" data-action="rights" data-song-id="${songId}">Buy Full Rights</button>
        </div>
        ${song.txHash ? `<p style="font-size:0.85em; color:#555; margin-top:8px;">Initialise TX: ${song.txHash}</p>` : ''}
    `;
    container.prepend(card);
}

async function performMarketplaceAction(endpoint, payload, successMessage) {
    if (!window.userWallet) {
        alert('Please connect your wallet first');
        return;
    }

    try {
        const switched = await ensureAvalancheCChain();
        if (!switched) {
            alert('Please switch MetaMask to Avalanche C-Chain (Fuji).');
            return;
        }

        const response = await fetch(`http://127.0.0.1:5000${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...payload, wallet: window.userWallet })
        });

        const data = await response.json();
        if (!response.ok || !data.success || !data.tx) {
            throw new Error(data.error || 'Failed to build transaction');
        }

        const provider = new ethers.BrowserProvider(window.ethereum);
        const signer = await provider.getSigner();
        const txRequest = { ...data.tx };
        delete txRequest.from;

        const sentTx = await signer.sendTransaction(txRequest);
        await sentTx.wait();

        const statusEl = document.getElementById('status');
        if (statusEl) {
            statusEl.innerText = `${successMessage}. Tx: ${sentTx.hash}`;
        }
    } catch (error) {
        console.error('Marketplace action failed:', error);
        const statusEl = document.getElementById('status');
        if (statusEl) {
            statusEl.innerText = `Transaction failed: ${error.message || String(error)}`;
        }
        alert(`Transaction failed: ${error.message || String(error)}`);
    }
}

// Upload a Spotify song's metadata to IPFS via the backend
async function addSpotifySongToBlockchain(title, artist, spotifyId) {
    if (!window.userWallet) {
        alert("Please connect your wallet first");
        return;
    }

    const switched = await ensureAvalancheCChain();
    if (!switched) {
        alert('Please switch MetaMask to Avalanche C-Chain (Fuji) to initialise songs.');
        return;
    }

    const downloadPrice = prompt("Enter download price in AVAX (e.g. 0.01):", "0.01");
    const fullRightsPrice = prompt("Enter full rights price in AVAX (e.g. 1):", "1");
    if (!downloadPrice || !fullRightsPrice) {
        alert("Prices are required.");
        return;
    }

    try {
        const response = await fetch('http://127.0.0.1:5000/marketplace/upload_spotify_song', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title,
                artist,
                spotify_id: spotifyId,
                download_price: downloadPrice,
                full_rights_price: fullRightsPrice,
                requester_wallet: window.userWallet
            })
        });

        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Upload failed');
        }

        let txHash = data.tx_hash || null;
        let songId = (typeof data.song_id !== 'undefined' && data.song_id !== null) ? Number(data.song_id) : null;
        let blockchainRegistered = !!data.blockchain_registered;
        let blockchainError = data.blockchain_error || null;

        // Fallback: if backend owner key is not available, build tx via backend and sign with connected wallet.
        if (!blockchainRegistered) {
            const walletResult = await registerSongOnBlockchainViaWallet(
                title,
                artist,
                spotifyId,
                downloadPrice,
                fullRightsPrice
            );
            if (walletResult && walletResult.success) {
                txHash = walletResult.tx_hash;
                if (typeof walletResult.song_id !== 'undefined' && walletResult.song_id !== null) {
                    songId = Number(walletResult.song_id);
                }
                blockchainRegistered = true;
            } else if (walletResult && walletResult.error) {
                blockchainError = blockchainError || walletResult.error;
            }
        }

        if (blockchainRegistered && songId !== null && !Number.isNaN(songId)) {
            addSongToListenInvestSection({
                songId,
                title,
                artist,
                downloadPrice,
                fullRightsPrice,
                txHash
            });
        }

        showIPFSResult(title, artist, data.ipfs_cid, txHash, blockchainRegistered, blockchainError);
    } catch (err) {
        console.error('Upload error:', err);
        alert("Failed to upload song: " + (err.message || err.toString()));
    }
}

async function registerSongOnBlockchainViaWallet(title, artist, spotifyId, downloadPrice, fullRightsPrice) {
    try {
        if (!window.ethereum) {
            return { success: false, error: 'Wallet provider not found' };
        }

        const buildResp = await fetch('http://127.0.0.1:5000/marketplace/add_spotify_song', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title,
                artist,
                spotify_id: spotifyId,
                download_price: downloadPrice,
                full_rights_price: fullRightsPrice,
                wallet: window.userWallet
            })
        });

        const buildData = await buildResp.json();
        if (!buildResp.ok || !buildData.success || !buildData.tx) {
            return { success: false, error: buildData.error || 'Failed to build blockchain transaction' };
        }

        const provider = new ethers.BrowserProvider(window.ethereum);
        const signer = await provider.getSigner();

        // Signer controls the sender address; remove backend-specified from to avoid conflicts.
        const txRequest = { ...buildData.tx };
        delete txRequest.from;

        const sentTx = await signer.sendTransaction(txRequest);
        const receipt = await sentTx.wait();

        let songId = null;
        try {
            const songAddedAbi = ["event SongAdded(uint256 indexed songId, string title, address indexed owner)"];
            const iface = new ethers.Interface(songAddedAbi);
            for (const log of (receipt.logs || [])) {
                if (!log || !log.topics || log.topics.length === 0) continue;
                let parsed;
                try {
                    parsed = iface.parseLog(log);
                } catch (_) {
                    parsed = null;
                }
                if (parsed && parsed.name === 'SongAdded') {
                    songId = Number(parsed.args.songId);
                    break;
                }
            }
        } catch (parseError) {
            console.warn('Could not decode SongAdded event from receipt:', parseError);
        }

        return { success: true, tx_hash: sentTx.hash, song_id: songId };
    } catch (error) {
        console.error('Wallet blockchain registration failed:', error);
        return { success: false, error: error.message || String(error) };
    }
}

function showIPFSResult(title, artist, ipfsCid, txHash, blockchainRegistered, blockchainError) {
    const resultContainer = document.getElementById('ipfsResultContainer');
    if (!resultContainer) {
        const div = document.createElement('div');
        div.id = 'ipfsResultContainer';
        const searchResults = document.getElementById('searchResults');
        if (searchResults) searchResults.parentNode.insertBefore(div, searchResults);
    }
    const container = document.getElementById('ipfsResultContainer');
    
    let blockchainStatus = '';
    if (blockchainRegistered) {
        blockchainStatus = `<p style="margin:0 0 8px 0; font-size:0.85em; word-break:break-all;">✓ Blockchain TX: <code style="background:#fff; padding:2px 4px; border-radius:3px;">${txHash}</code></p>`;
    } else {
        const reason = blockchainError ? ` Reason: ${blockchainError}` : '';
        blockchainStatus = `<p style="margin:0 0 8px 0; font-size:0.85em; color:#666;">Blockchain: Not registered.${reason}</p>`;
    }
    
    container.innerHTML = `
        <div style="background:#d4edda; border:1px solid #c3e6cb; border-radius:6px; padding:12px; margin:12px 0; color:#155724;">
            <h4 style="margin:0 0 8px 0;">✓ Song Initialised!</h4>
            <p style="margin:0 0 4px 0;"><strong>${title}</strong> by ${artist}</p>
            <p style="margin:0 0 8px 0; font-size:0.85em; word-break:break-all;">IPFS CID: <code style="background:#fff; padding:2px 4px; border-radius:3px;">${ipfsCid}</code></p>
            ${blockchainStatus}
            <button onclick="copyToClipboard('${ipfsCid}')" style="background:#155724; color:#fff; border:none; padding:6px 12px; border-radius:4px; cursor:pointer;">Copy CID</button>
        </div>
    `;
}

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        alert('CID copied to clipboard!');
    }).catch(() => {
        alert('Failed to copy');
    });
}

async function addToBlockchain(spotifyId, title, artist) {
    // This would typically be done by the artist/admin, but for demo purposes
    alert(`Song "${title}" by ${artist} would be added to the blockchain marketplace`);
    // In a real implementation, this would call a backend endpoint to add the song
}

function formatTrackCard(item) {
    return `
        <div class="track-result">
            <h3>${item.title || item.name}</h3>
            ${item.description ? `<p>${item.description}</p>` : ''}
            ${item.artist ? `<p>Artist: ${item.artist}</p>` : ''}
            ${item.album ? `<p>Album: ${item.album}</p>` : ''}
            ${item.type ? `<p>Type: ${item.type}</p>` : ''}
            ${item.amount ? `<p>Amount: ${item.amount} AVAX</p>` : ''}
            ${item.status ? `<p>Status: ${item.status}</p>` : ''}
            ${item.tx_hash ? `<p>Tx: ${item.tx_hash}</p>` : ''}
            ${item.external_url ? `<button onclick="addSpotifySongToBlockchain('${(item.title||item.name).replace(/'/g, "\\'")}', '${(item.artist||'').replace(/'/g, "\\'")}', '${item.spotify_id||item.id||''}')">Initialise</button>` : ''}
        </div>
    `;
}

function displayItems(containerId, items, emptyMessage) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!items || items.length === 0) {
        container.innerHTML = `<p>${emptyMessage}</p>`;
        return;
    }
    // Only render dynamic content, no demo/hardcoded content
    container.innerHTML = items.map(formatTrackCard).join("");
}

function downloadItem(name, type) {
    const status = document.getElementById('status');
    if (status) {
        status.innerText = `${type === 'album' ? 'Album' : 'Song'} "${name}" downloaded to your library.`;
    }
    alert(`${type === 'album' ? 'Album' : 'Song'} "${name}" download started.`);
}

function toggleArtistTracks(artistId) {
    const extraTracks = document.querySelectorAll(`.artist-result.extra-artist-track[data-artist="${artistId}"]`);
    if (!extraTracks || extraTracks.length === 0) return;
    const btn = document.getElementById(`toggleArtistBtn_${artistId}`);
    const expanded = btn ? btn.dataset.expanded === 'true' : false;

    extraTracks.forEach(el => {
        el.style.display = expanded ? 'none' : '';
    });

    if (btn) {
        btn.dataset.expanded = expanded ? 'false' : 'true';
        btn.textContent = expanded ? 'Show more' : 'Show less';
    }
}

function playAlbum(name) {
    const status = document.getElementById('status');
    if (status) {
        status.innerText = `Playing album "${name}" from your library.`;
    }
}

async function createDownloadPlaylist(nameInputId = 'playlistName', outputId = 'playlistOutput') {
    const name = document.getElementById(nameInputId)?.value.trim() || 'My Downloaded Playlist';
    const output = document.getElementById(outputId);

    if (!output) return;

    try {
        const wallet = window.userWallet || "";
        const url = wallet ? `http://127.0.0.1:5000/library?wallet=${encodeURIComponent(wallet)}` : "http://127.0.0.1:5000/library";
        const response = await fetch(url);
        const libraryItems = await response.json();

        if (!Array.isArray(libraryItems) || libraryItems.length === 0) {
            output.innerHTML = '<p>No downloaded songs or albums available to add.</p>';
            return;
        }

        // Only use dynamic downloaded items
        const downloadedItems = libraryItems.filter(item => item.type === 'download');

        if (downloadedItems.length === 0) {
            output.innerHTML = '<p>No downloaded songs or albums available to add.</p>';
            return;
        }

        const items = downloadedItems.map(item => item.title || item.name || 'Unknown Item');

        output.innerHTML = `
            <div class="card playlist-card">
                <h4>${name}</h4>
                <p>${items.join(', ')}</p>
                <p>Playlist created from your downloaded songs and albums.</p>
            </div>
        `;
    } catch (error) {
        console.error('Error creating playlist:', error);
        output.innerHTML = '<p>Failed to create playlist. Please try again.</p>';
    }
}

async function searchSpotifyArtist() {
    const query = document.getElementById("spotifyArtistSearch").value.trim();
    if (!query) {
        alert("Enter an artist name or Spotify artist ID to connect.");
        return;
    }

    try {
        const artistLibrary = document.getElementById('artistLibrary');
        if (!artistLibrary) return;

        artistLibrary.innerHTML = '<p>Loading artist data...</p>';

        const response = await fetch(`http://127.0.0.1:5000/spotify/artist?artist=${encodeURIComponent(query)}`);
        if (!response.ok) {
            const errorText = await response.text();
            let message = `HTTP Error: ${response.status}`;
            try {
                const parsed = JSON.parse(errorText);
                message = parsed.error || parsed.message || message;
            } catch (_) {
                if (errorText) {
                    message = errorText.slice(0, 180);
                }
            }
            throw new Error(message);
        }

        const artistData = await response.json();

        if (artistData.error) {
            artistLibrary.innerHTML = `<p>${artistData.error}</p>`;
            return;
        }

        if (!artistData.tracks || !Array.isArray(artistData.tracks) || artistData.tracks.length === 0) {
            artistLibrary.innerHTML = `
                <p>No tracks were found for "${artistData.name || query}".</p>
                <p>Try another artist name or verify your Spotify connection.</p>
            `;
            return;
        }

        const artistName = artistData.name || query;
        const artistId = artistData.id || query;

        artistLibrary.innerHTML = `
            <h3>${artistName}</h3>
            ${artistData.tracks.map((track, idx) => `
                <div class="artist-result${idx >= 10 ? ' extra-artist-track' : ''}" data-artist="${artistId}" data-track-id="${track.id || ''}" data-track-name="${(track.name || '').replace(/"/g, '&quot;')}" data-artist-name="${artistName.replace(/"/g, '&quot;')}" style="display:flex; gap:12px; align-items:flex-start; margin-bottom:16px; padding:12px; border:1px solid #ddd; border-radius:10px; background:#fafafa; ${idx >= 10 ? 'display:none;' : ''}">
                    ${track.album?.image ? `<img src="${track.album.image}" alt="${track.album.name || 'album'} cover" style="width:120px; height:120px; object-fit:cover; border-radius:10px;" />` : ''}
                    <div class="artist-result-info" style="flex:1;">
                        <h4 style="margin:0 0 8px 0;">${track.name || 'Unknown'}</h4>
                        <p style="margin:0 0 8px 0; color:#555;">${(track.album?.type || 'album').toLowerCase() === 'single' ? 'Single' : 'Album'}: ${track.album?.name || 'Unknown'}</p>
                        <button type="button" class="artist-initialise-btn" style="background:#1db954; color:#fff; border:none; padding:8px 12px; border-radius:6px; cursor:pointer; font-weight:600;">Initialise</button>
                    </div>
                </div>
            `).join('')}
            ${artistData.tracks.length > 10 ? `<div style="text-align:center; margin-top:8px;"><button type="button" class="toggle-artist-btn" data-toggle-id="${artistId}" data-expanded="false" id="toggleArtistBtn_${artistId}">Show more</button></div>` : ''}
        `;
    } catch (error) {
        console.error('Error loading Spotify artist library:', error);
        const artistLibrary = document.getElementById('artistLibrary');
        if (artistLibrary) artistLibrary.innerHTML = `<p>Failed to load Spotify artist library. ${error.message || ''}</p>`;
    }
}

async function connectSpotifyChannel(artistId, artistName) {
    if (!window.userWallet) {
        alert("Please connect your wallet first");
        return;
    }

    try {
        const response = await fetch('http://127.0.0.1:5000/artist/connect_spotify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                artist_id: artistId,
                artist_name: artistName,
                wallet: window.userWallet
            })
        });

        const data = await response.json();
        if (response.ok && data.success) {
            alert(`Artist '${artistName}' connected to your platform!`);
        } else {
            alert(data.error || 'Failed to connect artist.');
        }
    } catch (err) {
        alert('Failed to connect artist: ' + err.message);
    }
}

async function loadLibrary() {
    const libraryContainer = document.getElementById("library");
    if (!libraryContainer) return;

    const wallet = window.userWallet || "";
    const url = wallet ? `http://127.0.0.1:5000/library?wallet=${encodeURIComponent(wallet)}` : "http://127.0.0.1:5000/library";

    try {
        const response = await fetch(url);
        const libraryItems = await response.json();
        displayItems('library', libraryItems, 'No songs found in your library yet.');
    } catch (error) {
        console.error("Error loading library:", error);
        displayItems('library', [], 'Failed to load library.');
    }
}

async function loadArtists() {
    const libraryContainer = document.getElementById("library");
    if (!libraryContainer) return;

    const wallet = window.userWallet || "";
    const url = wallet ? `http://127.0.0.1:5000/library?wallet=${encodeURIComponent(wallet)}` : "http://127.0.0.1:5000/library";

    try {
        const response = await fetch(url);
        const libraryItems = await response.json();

        if (!Array.isArray(libraryItems) || libraryItems.length === 0) {
            displayItems('library', [], 'No artists found in your library yet.');
            return;
        }

        // Extract unique artists
        const artists = {};
        libraryItems.forEach(item => {
            const artist = item.artist || 'Unknown Artist';
            if (!artists[artist]) {
                artists[artist] = { name: artist, count: 0, items: [] };
            }
            artists[artist].count++;
            artists[artist].items.push(item);
        });

        const artistCards = Object.values(artists).map(artist => ({
            title: artist.name,
            description: `${artist.count} item(s) in your library`,
            type: 'Artist',
            count: artist.count
        }));

        displayItems('library', artistCards, 'No artists found in your library yet.');
    } catch (error) {
        console.error("Error loading artists:", error);
        displayItems('library', [], 'Failed to load artists.');
    }
}

const debounce = (fn, delay) => {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
};

const loadLibraryBtn = document.getElementById("loadLibraryBtn");
if (loadLibraryBtn) {
    loadLibraryBtn.addEventListener("click", loadLibrary);
}

const loadArtistsBtn = document.getElementById("loadArtistsBtn");
if (loadArtistsBtn) {
    loadArtistsBtn.addEventListener("click", loadArtists);
}

const initSpotifyBtn = document.getElementById("initSpotifyBtn");
if (initSpotifyBtn) {
    initSpotifyBtn.addEventListener("click", initializeSpotify);
    initializeSpotify();
}


// Fix: Correct ID for Spotify artist connect button
const connectSpotifyArtistBtn = document.getElementById("connectSpotifyArtistBtn");
if (connectSpotifyArtistBtn) {
    connectSpotifyArtistBtn.addEventListener("click", searchSpotifyArtist);
}

const spotifyArtistSearchInput = document.getElementById('spotifyArtistSearch');
if (spotifyArtistSearchInput) {
    spotifyArtistSearchInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            searchSpotifyArtist();
        }
    });
}

const artistLibraryContainer = document.getElementById('artistLibrary');
if (artistLibraryContainer) {
    artistLibraryContainer.addEventListener('click', function (event) {
        const initBtn = event.target.closest('.artist-initialise-btn');
        if (initBtn) {
            const row = initBtn.closest('.artist-result');
            if (!row) return;
            addSpotifySongToBlockchain(
                row.dataset.trackName || '',
                row.dataset.artistName || '',
                row.dataset.trackId || ''
            );
            return;
        }

        const toggleBtn = event.target.closest('.toggle-artist-btn');
        if (toggleBtn) {
            toggleArtistTracks(toggleBtn.dataset.toggleId);
        }
    });
}

