// Auto-resolve backend base URL so frontend still works if Flask falls back to another port.
const DEFAULT_API_BASE = 'http://127.0.0.1:5000';
const LEGACY_API_BASES = ['http://127.0.0.1:5000', 'http://127.0.0.1:5004'];
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

    const candidatePorts = [5000, 5004, 5001, 5002, 5003, 5005, 5010];
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
        const matchedBase = (typeof target === 'string')
            ? LEGACY_API_BASES.find((base) => target.startsWith(base))
            : null;

        if (matchedBase) {
            if (!apiBasePromise) {
                apiBasePromise = resolveApiBase().catch(() => DEFAULT_API_BASE);
            }
            const base = await apiBasePromise;
            const rewritten = target.replace(matchedBase, base || DEFAULT_API_BASE);

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
                const retryUrl = target.replace(matchedBase, refreshedBase || DEFAULT_API_BASE);
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
    // Enforce login on all pages except login.html (bypass with URL param ?testmode=true)
    const isLoginPage = window.location.pathname.endsWith('login.html');
    const isLogoutPage = window.location.pathname.endsWith('logout.html');
    const testMode = new URLSearchParams(window.location.search).get('testmode') === 'true';
    
    if (!isLoginPage && !isLogoutPage && !testMode) {
        if (!localStorage.getItem('isLoggedIn')) {
            // Allow testing - auto-login in development
            if (window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost' || window.location.hostname === '192.168.1.177') {
                localStorage.setItem('isLoggedIn', 'true');
                localStorage.setItem('testUser', 'true');
            } else {
                window.location.href = 'login.html';
                return;
            }
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
                const statusEl = document.getElementById('status');
                if (statusEl) {
                    statusEl.innerText = 'Please switch MetaMask to Avalanche C-Chain (Fuji).';
                }
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
const walletConnectBtn = document.getElementById("connectBtn");
if (walletConnectBtn) {
    walletConnectBtn.addEventListener("click", connectWallet);
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
        <div class="track-result" data-track-id="${track.id || track.spotify_id}" data-track-name="${(track.title || track.name).replace(/"/g, '&quot;')}" data-track-artist="${(track.artist || '').replace(/"/g, '&quot;')}" data-track-duration-ms="${track.duration_ms || track.durationMs || ''}" style="display:flex; gap:12px; align-items:flex-start;">
            ${track.album_image ? `<img src="${track.album_image}" alt="cover" style="width:120px; height:120px; object-fit:cover; border-radius:6px;" />` : '<div style="width:120px; height:120px; background:#eee; border-radius:6px;"></div>'}
            <div style="flex:1;">
                <h3 style="margin:0 0 4px 0;">${track.title || track.name}</h3>
                <p style="margin:0 0 4px 0; font-size:0.9em; color:#666;">Artist: ${track.artist || ''}</p>
                <p style="margin:0 0 8px 0; font-size:0.9em; color:#666;">Album: ${track.album || (track.album && track.album.name) || ''}</p>
                ${formatDurationMs(track.duration_ms || track.durationMs) ? `<p style="margin:0 0 8px 0; font-size:0.9em; color:#666;">Length: ${formatDurationMs(track.duration_ms || track.durationMs)}</p>` : ''}
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
            const coverArtUrl = row.querySelector('img') ? row.querySelector('img').src : '';
            const durationMs = row.dataset.trackDurationMs || '';
            addSpotifySongToBlockchain(trackName, trackArtist, trackId, coverArtUrl, durationMs);
        });
    });
}

const INITIALISED_CARD_PREFIX = 'initialisedSongCard_';

function toSafeId(value) {
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '_');
}

function extractSpotifyTrackId(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';

    // Canonical Spotify track IDs are 22-char base62 strings.
    const idMatch = raw.match(/^[A-Za-z0-9]{22}$/);
    if (idMatch) return raw;

    // Handle Spotify URI: spotify:track:<id>
    const uriMatch = raw.match(/^spotify:track:([A-Za-z0-9]{22})$/i);
    if (uriMatch) return uriMatch[1];

    // Handle Spotify web/open URLs containing /track/<id>
    const urlMatch = raw.match(/\/track\/([A-Za-z0-9]{22})(?:\?|$|\/)/i);
    if (urlMatch) return urlMatch[1];

    return '';
}

function resolveSongKey(song) {
    if (!song) return null;
    const titlePart = String(song.title || '').trim();
    const artistPart = String(song.artist || '').trim();
    if (song.songId !== undefined && song.songId !== null && String(song.songId).trim() !== '') {
        // Composite key prevents unrelated songs from collapsing when backend reuses blockchain_song_id.
        if (titlePart || artistPart) {
            return `song_${String(song.songId).trim()}_${titlePart}_${artistPart}`;
        }
        return `song_${String(song.songId).trim()}`;
    }
    if (song.ipfsCid) {
        return `ipfs_${String(song.ipfsCid).trim()}`;
    }
    if (titlePart || artistPart) {
        return `name_${titlePart}_${artistPart}`;
    }
    return null;
}

function scoreApprovedSongCandidate(song) {
    if (!song) return -9999;
    let score = 0;
    if (String(song.song_file_cid || song.songFileCid || '').trim()) score += 100;
    if (String(song.source || '').toLowerCase() !== 'spotify') score += 40;
    if (String(song.user_address || song.requester_wallet || '').trim()) score += 20;
    if (String(song.preview_url || '').trim()) score -= 15;
    if (String(song.status || '').toLowerCase() === 'approved') score += 5;
    return score;
}

function pickBestApprovedSongs(approvedSongs) {
    if (!Array.isArray(approvedSongs)) return [];

    const byIdentity = new Map();
    for (const song of approvedSongs) {
        const title = String(song.songTitle || song.title || '').trim().toLowerCase();
        const artist = String(Array.isArray(song.artistNames) ? song.artistNames.join(', ') : (song.artistNames || song.artist || '')).trim().toLowerCase();
        const identity = `${title}::${artist}`;
        if (!title && !artist) continue;

        const existing = byIdentity.get(identity);
        if (!existing) {
            byIdentity.set(identity, { ...song });
            continue;
        }

        const preferred = scoreApprovedSongCandidate(song) > scoreApprovedSongCandidate(existing)
            ? { ...song }
            : existing;

        if (!preferred.song_file_cid && song.song_file_cid) {
            preferred.song_file_cid = song.song_file_cid;
        }
        if (!preferred.songFileCid && song.songFileCid) {
            preferred.songFileCid = song.songFileCid;
        }
        if (!preferred.spotify_id && song.spotify_id) {
            preferred.spotify_id = song.spotify_id;
        }
        if (!preferred.spotifyId && song.spotifyId) {
            preferred.spotifyId = song.spotifyId;
        }
        if (!preferred.preview_url && song.preview_url) {
            preferred.preview_url = song.preview_url;
        }
        if (!preferred.previewUrl && song.previewUrl) {
            preferred.previewUrl = song.previewUrl;
        }
        if (!preferred.album_image && song.album_image) {
            preferred.album_image = song.album_image;
        }
        if (!preferred.cover_art_url && song.cover_art_url) {
            preferred.cover_art_url = song.cover_art_url;
        }
        if (!preferred.coverArtUrl && song.coverArtUrl) {
            preferred.coverArtUrl = song.coverArtUrl;
        }

        byIdentity.set(identity, preferred);
    }

    return Array.from(byIdentity.values());
}

function setMainStatus(message) {
    const statusEl = document.getElementById('status');
    if (statusEl) {
        statusEl.innerText = message;
    }
}

function formatPlaybackTime(seconds) {
    const safe = Number(seconds);
    if (!Number.isFinite(safe) || safe < 0) {
        return '00:00';
    }
    const total = Math.floor(safe);
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function formatDurationMs(durationMs) {
    const safe = Number(durationMs);
    if (!Number.isFinite(safe) || safe <= 0) return '';
    return formatPlaybackTime(safe / 1000);
}

function getExpectedDurationSecondsFromCard(cardEl) {
    const raw = Number(cardEl?.dataset?.durationMs || 0);
    if (!Number.isFinite(raw) || raw <= 0) return 0;
    return raw / 1000;
}

function isLikelyPreviewClip(expectedDurationSeconds, actualDurationSeconds) {
    const expected = Number(expectedDurationSeconds || 0);
    const actual = Number(actualDurationSeconds || 0);
    if (!Number.isFinite(expected) || expected <= 60) return false;
    if (!Number.isFinite(actual) || actual <= 0) return false;
    return actual <= 35;
}

async function waitForAudioMetadata(audioEl, timeoutMs = 2500) {
    if (!audioEl) return;
    if (Number.isFinite(Number(audioEl.duration)) && Number(audioEl.duration) > 0) {
        return;
    }

    await new Promise((resolve) => {
        let finished = false;
        const done = () => {
            if (finished) return;
            finished = true;
            audioEl.removeEventListener('loadedmetadata', done);
            audioEl.removeEventListener('durationchange', done);
            clearTimeout(timer);
            resolve();
        };

        const timer = setTimeout(done, timeoutMs);
        audioEl.addEventListener('loadedmetadata', done, { once: true });
        audioEl.addEventListener('durationchange', done, { once: true });
    });
}

function updateNowPlayingWithTiming(audioEl, mode = 'playing') {
    const nowPlayingEl = document.getElementById('platformNowPlaying');
    if (!audioEl || !nowPlayingEl) return;

    const title = String(audioEl.dataset.nowTitle || 'Unknown Title');
    const artist = String(audioEl.dataset.nowArtist || 'Unknown Artist');
    const current = formatPlaybackTime(Number(audioEl.currentTime || 0));
    const total = Number.isFinite(Number(audioEl.duration)) && Number(audioEl.duration) > 0
        ? formatPlaybackTime(Number(audioEl.duration))
        : '--:--';

    const prefix = mode === 'paused'
        ? 'Now playing (Paused)'
        : mode === 'stopped'
            ? 'Now playing (Stopped)'
            : 'Now playing full track';

    nowPlayingEl.innerText = `${prefix}: ${title} by ${artist} (${current} / ${total})`;
}

const platformPlayerState = {
    queue: [],
    currentIndex: -1,
    shuffle: false,
    repeat: false
};

function hasActiveAudioSource(audioEl) {
    if (!audioEl) return false;
    const attrSrc = String(audioEl.getAttribute('src') || '').trim();
    const propSrc = String(audioEl.currentSrc || audioEl.src || '').trim();
    return Boolean(attrSrc || propSrc);
}

function isEmbedModeActive(iframe) {
    if (!iframe) return false;
    const src = String(iframe.src || '').trim();
    return Boolean(src) && src !== 'about:blank';
}

function getPlatformAudioEngine() {
    let audioEl = document.getElementById('platformPreviewAudio');
    if (audioEl) {
        return audioEl;
    }

    // Recover gracefully when markup was edited and the audio element is missing.
    audioEl = document.createElement('audio');
    audioEl.id = 'platformPreviewAudio';
    audioEl.style.display = 'none';
    audioEl.preload = 'auto';
    audioEl.crossOrigin = 'anonymous';
    document.body.appendChild(audioEl);
    return audioEl;
}

function getPlatformEmbedFrame() {
    let iframe = document.getElementById('platformPlayerIframe');
    if (iframe) {
        return iframe;
    }

    iframe = document.createElement('iframe');
    iframe.id = 'platformPlayerIframe';
    iframe.style.width = '100%';
    iframe.style.maxWidth = '640px';
    iframe.style.height = '152px';
    iframe.style.border = '0';
    iframe.style.borderRadius = '12px';
    iframe.style.display = 'none';
    iframe.allow = 'autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture';
    iframe.loading = 'lazy';

    const container = document.getElementById('initialisedSongsList');
    if (container && container.parentElement) {
        container.parentElement.insertBefore(iframe, container);
    } else {
        document.body.appendChild(iframe);
    }
    return iframe;
}

const IPFS_GATEWAYS = [
    'http://127.0.0.1:5000/ipfs/file/',
    'https://ipfs.io/ipfs/',
    'https://dweb.link/ipfs/',
    'https://cloudflare-ipfs.com/ipfs/',
    'https://gateway.pinata.cloud/ipfs/',
    'https://w3s.link/ipfs/',
    'https://nftstorage.link/ipfs/',
    'https://ipfs.filebase.io/ipfs/'
];

function buildIpfsAudioCandidates(cid) {
    const safeCid = String(cid || '').trim();
    if (!safeCid) return [];
    const candidates = IPFS_GATEWAYS.map((base) => `${base}${encodeURIComponent(safeCid)}`);
    return Array.from(new Set(candidates));
}

async function resolveCoverArtUrl(title, artist, spotifyId) {
    try {
        const query = encodeURIComponent(`${title || ''} ${artist || ''}`.trim());
        if (!query) return '';
        const response = await fetch(`http://127.0.0.1:5000/spotify/search?q=${query}&limit=1&offset=0`);
        if (!response.ok) return '';
        const items = await response.json();
        if (!Array.isArray(items) || items.length === 0) return '';
        return String(items[0]?.album_image || items[0]?.image || '').trim();
    } catch (_) {
        return '';
    }
}

async function resolvePreviewUrl(title, artist, spotifyId) {
    try {
        const id = extractSpotifyTrackId(spotifyId || '');
        const query = encodeURIComponent(id || `${title || ''} ${artist || ''}`.trim());
        if (!query) return '';

        const response = await fetch(`http://127.0.0.1:5000/spotify/search?q=${query}&limit=10&offset=0`);
        if (!response.ok) return '';

        const items = await response.json();
        if (!Array.isArray(items) || !items.length) return '';

        const normalizedTitle = String(title || '').trim().toLowerCase();
        const normalizedArtist = String(artist || '').trim().toLowerCase();

        let best = null;
        if (id) {
            best = items.find((it) => String(it.id || '').trim() === id && String(it.preview_url || '').trim());
        }
        if (!best) {
            best = items.find((it) => {
                const t = String(it.title || it.name || '').trim().toLowerCase();
                const a = String(it.artist || '').trim().toLowerCase();
                return t === normalizedTitle && a === normalizedArtist && String(it.preview_url || '').trim();
            });
        }
        if (!best) {
            best = items.find((it) => String(it.preview_url || '').trim());
        }

        return best ? String(best.preview_url || '').trim() : '';
    } catch (_) {
        return '';
    }
}

function applyCoverArtToPanel(coverArtUrl, title, artist) {
    const panel = document.getElementById('platformCoverArtPanel');
    const image = document.getElementById('platformCoverArtImage');
    if (!panel || !image) return;

    if (coverArtUrl) {
        image.src = coverArtUrl;
        image.alt = `${title || 'Song'} cover art`;
        panel.style.display = 'flex';
    } else {
        image.removeAttribute('src');
        image.alt = 'Current song cover art';
        panel.style.display = 'none';
    }
}

async function hydrateCardCoverArt(cardEl) {
    if (!cardEl) return;
    const existing = String(cardEl.dataset.coverArtUrl || '').trim();
    if (existing) {
        const img = cardEl.querySelector('.track-cover-art');
        if (img) img.src = existing;
        return;
    }

    if (cardEl.dataset.coverArtLoading === 'true') return;
    cardEl.dataset.coverArtLoading = 'true';

    const coverArtUrl = await resolveCoverArtUrl(
        cardEl.dataset.songTitle || '',
        cardEl.dataset.songArtist || '',
        cardEl.dataset.spotifyId || ''
    );

    cardEl.dataset.coverArtLoading = 'false';
    if (!coverArtUrl) return;

    cardEl.dataset.coverArtUrl = coverArtUrl;
    const img = cardEl.querySelector('.track-cover-art');
    if (img) {
        img.src = coverArtUrl;
        img.alt = `${cardEl.dataset.songTitle || 'Song'} cover art`;
        img.style.display = 'block';
    }
}

function getTrackFromCard(cardEl) {
    if (!cardEl) return null;
    return {
        key: cardEl.id || `${cardEl.dataset.songTitle || ''}::${cardEl.dataset.songArtist || ''}`,
        title: cardEl.dataset.songTitle || 'Unknown Title',
        artist: cardEl.dataset.songArtist || 'Unknown Artist',
        audioUrl: String(cardEl.dataset.audioUrl || '').trim(),
        audioFallbackUrl: String(cardEl.dataset.audioFallbackUrl || '').trim(),
        previewUrl: String(cardEl.dataset.previewUrl || '').trim(),
        spotifyId: String(cardEl.dataset.spotifyId || '').trim(),
        spotifyUrl: String(cardEl.dataset.spotifyUrl || '').trim(),
        cardEl
    };
}

async function ensureCardPreviewUrl(cardEl) {
    if (!cardEl) return '';
    const existingPreview = String(cardEl.dataset.previewUrl || '').trim();
    if (existingPreview) return existingPreview;

    const resolvedPreview = await resolvePreviewUrl(
        cardEl.dataset.songTitle || '',
        cardEl.dataset.songArtist || '',
        cardEl.dataset.spotifyId || ''
    );

    if (resolvedPreview) {
        cardEl.dataset.previewUrl = resolvedPreview;
    }
    return resolvedPreview;
}

function getSpotifyEmbedUrlForCard(cardEl) {
    if (!cardEl) return '';
    const rawSpotifyId = String(cardEl.dataset.spotifyId || '').trim();
    const rawSpotifyUrl = String(cardEl.dataset.spotifyUrl || '').trim();
    const trackId = extractSpotifyTrackId(rawSpotifyId || rawSpotifyUrl || '');
    if (!trackId) return '';
    return `https://open.spotify.com/embed/track/${encodeURIComponent(trackId)}`;
}

async function validateTrackAudio(cardEl) {
    if (!cardEl) return false;
    if (cardEl.dataset.audioValidated === 'true') {
        return cardEl.dataset.audioPlayable === 'true';
    }

    if (cardEl.dataset.audioChecking === 'true') {
        return cardEl.dataset.audioPlayable === 'true';
    }

    const fullCandidates = Array.from(new Set([
        String(cardEl.dataset.audioUrl || '').trim(),
        String(cardEl.dataset.audioFallbackUrl || '').trim(),
        ...buildIpfsAudioCandidates(cardEl.dataset.songFileCid || '')
    ].filter(Boolean)));

    const spotifyEmbedUrl = getSpotifyEmbedUrlForCard(cardEl);

    if (spotifyEmbedUrl) {
        cardEl.dataset.hasPlayableAudioSource = 'true';
        cardEl.dataset.audioValidated = 'true';
        cardEl.dataset.audioPlayable = 'true';
        cardEl.dataset.playMode = 'spotify-embed';
        return true;
    }

    if (!fullCandidates.length) {
        cardEl.dataset.hasPlayableAudioSource = 'false';
        cardEl.dataset.audioValidated = 'true';
        cardEl.dataset.audioPlayable = 'false';
        return false;
    }

    cardEl.dataset.audioChecking = 'true';
    for (const candidateUrl of fullCandidates) {
        try {
            const response = await fetch(candidateUrl, { method: 'HEAD' });
            if (response.ok) {
                cardEl.dataset.audioUrl = candidateUrl;
                cardEl.dataset.audioPlayable = 'true';
                cardEl.dataset.hasPlayableAudioSource = 'true';
                cardEl.dataset.audioValidated = 'true';
                cardEl.dataset.audioChecking = 'false';
                return true;
            }
        } catch (_) {
            // try next candidate
        }
    }

    // Some gateways reject HEAD/CORS while still allowing media playback via <audio>.
    // Keep the track in the queue and let the player attempt each candidate URL.
    cardEl.dataset.audioUrl = fullCandidates[0] || '';
    cardEl.dataset.audioPlayable = fullCandidates.length ? 'true' : 'false';
    cardEl.dataset.hasPlayableAudioSource = fullCandidates.length ? 'true' : 'false';
    cardEl.dataset.audioValidated = 'true';
    cardEl.dataset.audioChecking = 'false';
    return fullCandidates.length > 0;
}

async function primePlayableTracks() {
    const cards = Array.from(document.querySelectorAll('#initialisedSongsList .card'));
    // Build queue immediately so both players can start playback without waiting
    // for metadata/HEAD checks across multiple gateways.
    rebuildPlatformQueue();
    if (cards.length) {
        await Promise.all(cards.map((card) => validateTrackAudio(card)));
    }
    rebuildPlatformQueue();
}

function updatePlatformQueueMeta() {
    const meta = document.getElementById('platformQueueMeta');
    if (meta) {
        meta.innerText = `Queue: ${platformPlayerState.queue.length} tracks`;
    }
    const shuffleBtn = document.getElementById('platformShuffleBtn');
    if (shuffleBtn) {
        shuffleBtn.innerText = `Shuffle: ${platformPlayerState.shuffle ? 'On' : 'Off'}`;
    }
    const repeatBtn = document.getElementById('platformRepeatBtn');
    if (repeatBtn) {
        repeatBtn.innerText = `Repeat: ${platformPlayerState.repeat ? 'On' : 'Off'}`;
    }
}

function rebuildPlatformApprovedSongsPlayerList() {
    const approvedList = document.getElementById('platformApprovedSongsPlayerList');
    if (!approvedList) return;

    const cards = Array.from(document.querySelectorAll('#initialisedSongsList .card'));
    if (!cards.length) {
        approvedList.innerHTML = '<p style="opacity:0.8;">No approved/initialised tracks loaded yet.</p>';
        return;
    }

    approvedList.innerHTML = cards.map((card, index) => {
        const title = String(card.dataset.songTitle || 'Unknown Title');
        const artist = String(card.dataset.songArtist || 'Unknown Artist');
        const cover = String(card.dataset.coverArtUrl || '').trim();
        return `
            <div class="platform-approved-track" style="display:flex;align-items:center;gap:10px;padding:8px;border:1px solid rgba(0,191,255,0.25);border-radius:8px;margin:8px 0;">
                ${cover ? `<img src="${cover}" alt="${title.replace(/"/g, '&quot;')} cover" style="width:46px;height:46px;object-fit:cover;border-radius:6px;" />` : '<div style="width:46px;height:46px;border-radius:6px;background:linear-gradient(135deg,#d7dde8,#f2f4f8);"></div>'}
                <div style="flex:1;min-width:0;">
                    <p style="margin:0;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${index + 1}. ${title}</p>
                    <p style="margin:2px 0 0 0;opacity:0.85;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${artist}</p>
                </div>
                <span style="padding:6px 10px;border-radius:6px;background:#123c7a;color:#d8f4ff;font-size:0.85em;">Approved</span>
            </div>
        `;
    }).join('');
}

function rebuildPlatformQueue() {
    const cards = Array.from(document.querySelectorAll('#initialisedSongsList .card'));
    const currentKey = platformPlayerState.queue[platformPlayerState.currentIndex]?.key || null;
    platformPlayerState.queue = cards
        .map((card) => getTrackFromCard(card))
        .filter((item) => Boolean(item));
    if (currentKey) {
        const nextIndex = platformPlayerState.queue.findIndex((item) => item.key === currentKey);
        platformPlayerState.currentIndex = nextIndex >= 0 ? nextIndex : (platformPlayerState.queue.length ? 0 : -1);
    } else {
        platformPlayerState.currentIndex = platformPlayerState.queue.length ? 0 : -1;
    }
    updatePlatformQueueMeta();
    rebuildPlatformApprovedSongsPlayerList();
}

function getFirstPlayableTrackIndex() {
    rebuildPlatformQueue();
    return platformPlayerState.queue.length ? 0 : -1;
}

function setCurrentTrackFromCard(cardEl) {
    if (!cardEl) return;
    rebuildPlatformQueue();
    const key = cardEl.id || `${cardEl.dataset.songTitle || ''}::${cardEl.dataset.songArtist || ''}`;
    const idx = platformPlayerState.queue.findIndex((item) => item.key === key);
    if (idx >= 0) {
        platformPlayerState.currentIndex = idx;
    }
}

async function playTrackFromCard(cardEl) {
    const audioEl = getPlatformAudioEngine();
    const nowPlayingEl = document.getElementById('platformNowPlaying');
    const iframe = getPlatformEmbedFrame();
    if (!cardEl) {
        setMainStatus('Audio player is unavailable on this page.');
        return;
    }

    const title = cardEl.dataset.songTitle || 'Unknown Title';
    const artist = cardEl.dataset.songArtist || 'Unknown Artist';
    const coverArtUrl = String(cardEl.dataset.coverArtUrl || '').trim();
    if (coverArtUrl) {
        applyCoverArtToPanel(coverArtUrl, title, artist);
    } else {
        applyCoverArtToPanel('', title, artist);
    }

    const fullAudioCandidates = Array.from(new Set([
        String(cardEl.dataset.audioUrl || '').trim(),
        String(cardEl.dataset.audioFallbackUrl || '').trim(),
        ...buildIpfsAudioCandidates(cardEl.dataset.songFileCid || '')
    ].filter(Boolean)));

    const spotifyEmbedUrl = getSpotifyEmbedUrlForCard(cardEl);

    if (spotifyEmbedUrl && iframe) {
        try {
            audioEl.pause();
            audioEl.removeAttribute('src');
            audioEl.load();
        } catch (_) {}

        audioEl.style.display = 'none';
        iframe.style.display = 'block';
        iframe.src = `${spotifyEmbedUrl}?utm_source=generator&autoplay=1`;
        cardEl.dataset.playMode = 'spotify-embed';
        if (nowPlayingEl) {
            nowPlayingEl.innerText = `Now playing via Spotify: ${title} by ${artist}`;
        }
        setMainStatus(`Playing via Spotify embed: ${title} by ${artist}`);
        return true;
    }

    if (fullAudioCandidates.length) {
        if (iframe) iframe.src = 'about:blank';
        audioEl.style.display = 'block';
        cardEl.dataset.playMode = 'full-audio';
        audioEl.dataset.nowTitle = title;
        audioEl.dataset.nowArtist = artist;

        let lastAudioError = null;
        for (const candidateUrl of fullAudioCandidates) {
            try {
                audioEl.currentTime = 0;
                audioEl.volume = 1.0;
                audioEl.muted = false;
                if (audioEl.src !== candidateUrl) {
                    audioEl.src = candidateUrl;
                }
                const playPromise = audioEl.play();
                if (playPromise && typeof playPromise.then === 'function') {
                    await playPromise.then(() => {
                        audioEl.volume = 1.0;
                    });
                } else {
                    await playPromise;
                }
                cardEl.dataset.playMode = 'full-audio';
                updateNowPlayingWithTiming(audioEl, 'playing');
                setMainStatus(`Playing: ${title} by ${artist}`);
                const volumeSlider = cardEl?.querySelector('input[data-action="track-volume"]');
                if (volumeSlider) volumeSlider.value = 100;
                return true;
            } catch (audioErr) {
                lastAudioError = audioErr;
            }
        }

        setMainStatus(`Full-length audio source failed for this track. (${lastAudioError?.message || 'Unknown audio error'})`);
        if (spotifyEmbedUrl && iframe) {
            try {
                audioEl.pause();
                audioEl.removeAttribute('src');
                audioEl.load();
            } catch (_) {}

            iframe.style.display = 'block';
            iframe.src = `${spotifyEmbedUrl}?utm_source=generator&autoplay=1`;
            cardEl.dataset.playMode = 'spotify-embed';
            if (nowPlayingEl) {
                nowPlayingEl.innerText = `Now playing via Spotify: ${title} by ${artist}`;
            }
            setMainStatus(`Playing via Spotify embed: ${title} by ${artist}`);
            return true;
        }
        return false;
    }

    if (spotifyEmbedUrl && iframe) {
        iframe.style.display = 'block';
        iframe.src = `${spotifyEmbedUrl}?utm_source=generator&autoplay=1`;
        cardEl.dataset.playMode = 'spotify-embed';
        if (nowPlayingEl) {
            nowPlayingEl.innerText = `Now playing via Spotify: ${title} by ${artist}`;
        }
        setMainStatus(`Playing via Spotify embed: ${title} by ${artist}`);
        return true;
    }

    setMainStatus('No full-length audio source found for this song yet.');
    return false;
}

async function playRelativeTrackFromQueue(direction, anchorCardEl = null) {
    rebuildPlatformQueue();
    if (!platformPlayerState.queue.length) {
        setMainStatus('No songs are available in the queue yet.');
        return false;
    }

    if (platformPlayerState.currentIndex < 0) {
        if (anchorCardEl) {
            setCurrentTrackFromCard(anchorCardEl);
        }
        if (platformPlayerState.currentIndex < 0) {
            platformPlayerState.currentIndex = 0;
        }
    }

    let nextIndex = platformPlayerState.currentIndex;
    if (platformPlayerState.shuffle) {
        nextIndex = Math.floor(Math.random() * platformPlayerState.queue.length);
    } else {
        nextIndex += direction;
        if (nextIndex >= platformPlayerState.queue.length) {
            nextIndex = platformPlayerState.repeat ? 0 : platformPlayerState.queue.length - 1;
        }
        if (nextIndex < 0) {
            nextIndex = platformPlayerState.repeat ? platformPlayerState.queue.length - 1 : 0;
        }
    }

    const item = platformPlayerState.queue[nextIndex];
    if (!item?.cardEl) {
        setMainStatus('Selected track is not available in the queue.');
        return false;
    }

    const started = await playTrackFromCard(item.cardEl);
    if (started) {
        platformPlayerState.currentIndex = nextIndex;
        return true;
    }
    return false;
}

async function handleTrackMiniPlayerAction(action, cardEl) {
    if (!cardEl) return;

    const audioEl = getPlatformAudioEngine();
    const nowPlayingEl = document.getElementById('platformNowPlaying');

    if (action === 'track-play') {
        if (hasActiveAudioSource(audioEl) && audioEl.paused) {
            try {
                await audioEl.play();
                updateNowPlayingWithTiming(audioEl, 'playing');
                setMainStatus('Playback resumed.');
                return;
            } catch (_) {
                // fall through to card-based play flow
            }
        }
        cardEl.dataset.audioValidated = 'false';
        await validateTrackAudio(cardEl);
        setCurrentTrackFromCard(cardEl);
        const started = await playTrackFromCard(cardEl);
        if (!started) {
            setMainStatus('This track does not have a playable audio source yet.');
        }
        return;
    }

    if (action === 'track-pause') {
        if (!hasActiveAudioSource(audioEl)) {
            setMainStatus('Pause works for full/preview audio. Spotify embed pause must be controlled inside Spotify.');
            return;
        }
        audioEl.pause();
        updateNowPlayingWithTiming(audioEl, 'paused');
        setMainStatus('Playback paused.');
        return;
    }

    if (action === 'track-stop') {
        const iframe = getPlatformEmbedFrame();
        if (hasActiveAudioSource(audioEl)) {
            audioEl.pause();
            audioEl.currentTime = 0;
            audioEl.removeAttribute('src');
            audioEl.load();
        }
        if (iframe) {
            iframe.src = 'about:blank';
            iframe.style.display = 'none';
        }
        updateNowPlayingWithTiming(audioEl, 'stopped');
        setMainStatus('Playback stopped.');
        return;
    }

    if (action === 'track-prev') {
        await playRelativeTrackFromQueue(-1, cardEl);
        return;
    }

    if (action === 'track-next') {
        await playRelativeTrackFromQueue(1, cardEl);
        return;
    }

    if (action === 'track-rewind') {
        if (!hasActiveAudioSource(audioEl)) {
            setMainStatus('No active track to rewind.');
            return;
        }
        audioEl.currentTime = Math.max(0, Number(audioEl.currentTime || 0) - 10);
        setMainStatus('Rewound by 10 seconds.');
        return;
    }

    if (action === 'track-fastforward') {
        if (!hasActiveAudioSource(audioEl)) {
            setMainStatus('No active track to fast-forward.');
            return;
        }
        const duration = Number(audioEl.duration || 0);
        const nextTime = Number(audioEl.currentTime || 0) + 10;
        audioEl.currentTime = duration > 0 ? Math.min(duration, nextTime) : nextTime;
        setMainStatus('Fast-forwarded by 10 seconds.');
        return;
    }

    if (action === 'track-shuffle') {
        platformPlayerState.shuffle = !platformPlayerState.shuffle;
        updatePlatformQueueMeta();
        const btn = cardEl.querySelector('button[data-action="track-shuffle"]');
        if (btn) btn.innerText = `Shuffle: ${platformPlayerState.shuffle ? 'On' : 'Off'}`;
        return;
    }

    if (action === 'track-repeat') {
        platformPlayerState.repeat = !platformPlayerState.repeat;
        updatePlatformQueueMeta();
        const btn = cardEl.querySelector('button[data-action="track-repeat"]');
        if (btn) btn.innerText = `Repeat: ${platformPlayerState.repeat ? 'On' : 'Off'}`;
    }
}

function ensurePlatformPlayerControlsBound() {
    if (window.__platformPlayerControlsBound) return;
    window.__platformPlayerControlsBound = true;

    const audioEl = getPlatformAudioEngine();
    const nowPlayingEl = document.getElementById('platformNowPlaying');
    const iframe = getPlatformEmbedFrame();
    const playBtn = document.getElementById('platformPlayBtn');
    const pauseBtn = document.getElementById('platformPauseBtn');
    const stopBtn = document.getElementById('platformStopBtn');
    const prevBtn = document.getElementById('platformPrevBtn');
    const nextBtn = document.getElementById('platformNextBtn');
    const rewindBtn = document.getElementById('platformRewindBtn');
    const fastForwardBtn = document.getElementById('platformFastForwardBtn');
    const shuffleBtn = document.getElementById('platformShuffleBtn');
    const repeatBtn = document.getElementById('platformRepeatBtn');

    const playAt = async (index, direction = 1, allowWrap = true) => {
        rebuildPlatformQueue();
        if (!platformPlayerState.queue.length) {
            setMainStatus('No songs are available in the queue yet.');
            return false;
        }
        let idx = index;
        if (!Number.isInteger(idx) || idx < 0 || idx >= platformPlayerState.queue.length) {
            idx = 0;
        }

        const step = direction >= 0 ? 1 : -1;
        const total = platformPlayerState.queue.length;

        for (let attempt = 0; attempt < total; attempt += 1) {
            const rawIndex = idx + (attempt * step);
            if (!allowWrap && (rawIndex < 0 || rawIndex >= total)) {
                break;
            }
            const probeIndex = allowWrap
                ? ((rawIndex % total) + total) % total
                : rawIndex;
            const item = platformPlayerState.queue[probeIndex];
            if (!item?.cardEl) {
                continue;
            }

            const started = await playTrackFromCard(item.cardEl);
            if (started) {
                platformPlayerState.currentIndex = probeIndex;
                return true;
            }
        }

        setMainStatus('No playable audio source found across queued tracks.');
        return false;
    };

    const playNext = async (direction) => {
        rebuildPlatformQueue();
        if (!platformPlayerState.queue.length) {
            setMainStatus('No songs are available in the queue yet.');
            return;
        }
        if (platformPlayerState.shuffle) {
            const randomIndex = Math.floor(Math.random() * platformPlayerState.queue.length);
            await playAt(randomIndex);
            return;
        }
        let nextIndex = platformPlayerState.currentIndex + direction;
        if (nextIndex >= platformPlayerState.queue.length) {
            nextIndex = platformPlayerState.repeat ? 0 : platformPlayerState.queue.length - 1;
        }
        if (nextIndex < 0) {
            nextIndex = platformPlayerState.repeat ? platformPlayerState.queue.length - 1 : 0;
        }
        await playAt(nextIndex, direction, Boolean(platformPlayerState.repeat));
    };

    if (playBtn) {
        playBtn.addEventListener('click', async () => {
            if (hasActiveAudioSource(audioEl) && !audioEl.paused) {
                setMainStatus('Playback is already running.');
                return;
            }
            if (hasActiveAudioSource(audioEl) && audioEl.paused) {
                try {
                    await audioEl.play();
                    updateNowPlayingWithTiming(audioEl, 'playing');
                    setMainStatus('Playback resumed.');
                    return;
                } catch (_) {
                    // fall through to queue playback
                }
            }
            // If the page loads with the default Spotify iframe, prefer platform queue playback
            // whenever approved/initialised tracks are available.
            if (!hasActiveAudioSource(audioEl) && isEmbedModeActive(iframe) && platformPlayerState.queue.length) {
                iframe.src = 'about:blank';
                iframe.style.display = 'none';
            }
            const startIndex = platformPlayerState.currentIndex >= 0
                ? platformPlayerState.currentIndex
                : getFirstPlayableTrackIndex();
            await playAt(startIndex, 1);
        });
    }

    if (pauseBtn) {
        pauseBtn.addEventListener('click', () => {
            if (!audioEl) return;
            if (hasActiveAudioSource(audioEl)) {
                audioEl.pause();
                updateNowPlayingWithTiming(audioEl, 'paused');
                setMainStatus('Playback paused.');
            } else {
                setMainStatus('Pause works for full/preview audio. Spotify embed pause must be controlled inside Spotify.');
            }
        });
    }

    if (stopBtn) {
        stopBtn.addEventListener('click', () => {
            if (audioEl && hasActiveAudioSource(audioEl)) {
                audioEl.pause();
                audioEl.currentTime = 0;
                audioEl.removeAttribute('src');
                audioEl.load();
            }
            if (iframe) {
                iframe.src = 'about:blank';
                iframe.style.display = 'none';
            }
            updateNowPlayingWithTiming(audioEl, 'stopped');
            setMainStatus('Playback stopped.');
        });
    }

    if (prevBtn) {
        prevBtn.addEventListener('click', async () => {
            await playNext(-1);
        });
    }

    if (nextBtn) {
        nextBtn.addEventListener('click', async () => {
            await playNext(1);
        });
    }

    if (rewindBtn) {
        rewindBtn.addEventListener('click', () => {
            if (!audioEl || !hasActiveAudioSource(audioEl)) {
                setMainStatus('Rewind works only for full/preview audio playback.');
                return;
            }
            audioEl.currentTime = Math.max(0, Number(audioEl.currentTime || 0) - 10);
            setMainStatus('Rewound by 10 seconds.');
        });
    }

    if (fastForwardBtn) {
        fastForwardBtn.addEventListener('click', () => {
            if (!audioEl || !hasActiveAudioSource(audioEl)) {
                setMainStatus('Fast forward works only for full/preview audio playback.');
                return;
            }
            const duration = Number(audioEl.duration || 0);
            const nextTime = Number(audioEl.currentTime || 0) + 10;
            audioEl.currentTime = duration > 0 ? Math.min(duration, nextTime) : nextTime;
            setMainStatus('Fast-forwarded by 10 seconds.');
        });
    }

    if (shuffleBtn) {
        shuffleBtn.addEventListener('click', () => {
            platformPlayerState.shuffle = !platformPlayerState.shuffle;
            updatePlatformQueueMeta();
        });
    }

    if (repeatBtn) {
        repeatBtn.addEventListener('click', () => {
            platformPlayerState.repeat = !platformPlayerState.repeat;
            updatePlatformQueueMeta();
        });
    }

    if (audioEl) {
        audioEl.addEventListener('timeupdate', () => {
            if (!audioEl.paused) {
                updateNowPlayingWithTiming(audioEl, 'playing');
            }
        });

        audioEl.addEventListener('loadedmetadata', () => {
            updateNowPlayingWithTiming(audioEl, audioEl.paused ? 'paused' : 'playing');
        });

        audioEl.addEventListener('durationchange', () => {
            updateNowPlayingWithTiming(audioEl, audioEl.paused ? 'paused' : 'playing');
        });

        audioEl.addEventListener('ended', async () => {
            if (platformPlayerState.repeat) {
                await playNext(1);
                return;
            }
            if (platformPlayerState.currentIndex + 1 < platformPlayerState.queue.length) {
                await playNext(1);
            }
        });
    }

    rebuildPlatformQueue();
}

function ensureInitialisedSongsSection() {
    const container = document.getElementById('initialisedSongsList');
    if (!container) return null;

    if (!container.dataset.bound) {
        container.dataset.bound = 'true';
        container.addEventListener('input', (event) => {
            const volumeInput = event.target.closest('input[data-action="track-volume"]');
            if (!volumeInput) return;

            const audioEl = getPlatformAudioEngine();
            const volume = Math.max(0, Math.min(1, Number(volumeInput.value || 100) / 100));
            audioEl.volume = volume;

            const cardEl = volumeInput.closest('.card');
            if (!cardEl) return;
            const volumeLabel = cardEl.querySelector('[data-role="track-volume-value"]');
            if (volumeLabel) {
                volumeLabel.innerText = `${Math.round(volume * 100)}%`;
            }
            setMainStatus(`Volume set to ${Math.round(volume * 100)}%.`);
        });

        container.addEventListener('click', async (event) => {
            const actionBtn = event.target.closest('button[data-action]');
            if (!actionBtn) return;

            const action = actionBtn.dataset.action;
            const cardEl = actionBtn.closest('.card');

            if (String(action || '').startsWith('track-')) {
                await handleTrackMiniPlayerAction(action, cardEl);
                return;
            }

            const songId = actionBtn.dataset.songId;
            const featureEnabled = actionBtn.dataset.featureEnabled;

            if (action === 'invest' && featureEnabled === 'false') {
                setMainStatus('Investment is disabled by the artist for this song.');
                alert('Investment is disabled by the artist for this song.');
                return;
            }

            if (action === 'rights' && featureEnabled === 'false') {
                setMainStatus('Full rights purchase is disabled by the artist for this song.');
                alert('Full rights purchase is disabled by the artist for this song.');
                return;
            }

            if (!songId) return;

            if (action === 'download') {
                await performMarketplaceAction('/marketplace/buy_download', { song_id: Number(songId) }, `Downloaded rights for song #${songId}`);
                return;
            }

            if (action === 'rights') {
                await performMarketplaceAction('/marketplace/buy_rights', { song_id: Number(songId) }, `Bought full rights for song #${songId}`);
                return;
            }

            if (action === 'invest') {
                const input = cardEl?.querySelector('input[type="number"]');
                const rawAmount = Number(input?.value || 0);
                const cardPrice = Number(cardEl?.dataset.investmentPrice || 0);
                let tokenAmount = rawAmount;

                // Card UI shows AVAX pricing, so accept AVAX entry and convert it into
                // whole token units using the song's configured investment price.
                if (cardPrice > 0 && rawAmount > 0 && rawAmount < 1) {
                    tokenAmount = rawAmount / cardPrice;
                }

                tokenAmount = Math.round(tokenAmount);
                if (!tokenAmount || tokenAmount <= 0) {
                    alert('Enter a valid token amount to invest.');
                    return;
                }
                setMainStatus(`INVEST_FIX_20260710d: submitting invest for song #${songId}`);
                await performMarketplaceAction(
                    '/marketplace/invest',
                    { song_id: Number(songId), token_amount: tokenAmount },
                    `Invested ${tokenAmount} token(s) in song #${songId}`
                );
            }
        });
    }

    return container;
}

function addSongToListenInvestSection(song, options = {}) {
    const container = ensureInitialisedSongsSection();
    if (!container || !song) return;

    const songKey = resolveSongKey(song);
    if (!songKey) return;

    const numericSongId = Number(song.songId);
    const hasNumericSongId = Number.isFinite(numericSongId);
    const hasFullAudioSource = Boolean(
        String(song.songFileCid || song.song_file_cid || '').trim() ||
        String(song.audioUrl || '').trim() ||
        String(song.audioFallbackUrl || '').trim()
    );
    const hasSpotifyEmbedSource = Boolean(extractSpotifyTrackId(song.spotifyId || song.spotify_url || song.spotifyUrl || ''));
    const previewUrl = String(song.previewUrl || song.preview_url || '').trim();
    const hasPlayableAudioSource = hasFullAudioSource || hasSpotifyEmbedSource;
    const tokenInputId = `tokenAmount_${toSafeId(songKey)}`;
    const cardDomId = `${INITIALISED_CARD_PREFIX}${toSafeId(songKey)}`;
    const existingCard = document.getElementById(cardDomId);
    const songFingerprint = JSON.stringify({
        songId: hasNumericSongId ? numericSongId : null,
        title: String(song.title || 'Unknown Title'),
        artist: String(song.artist || 'Unknown Artist'),
        spotifyId: extractSpotifyTrackId(song.spotifyId || ''),
        spotifyUrl: String(song.spotifyUrl || song.externalUrl || ''),
        songFileCid: String(song.songFileCid || song.song_file_cid || ''),
        audioUrl: String(song.audioUrl || ''),
        audioFallbackUrl: String(song.audioFallbackUrl || ''),
        previewUrl,
        coverArtUrl: String(song.coverArtUrl || song.cover_art_url || ''),
        downloadPrice: song.downloadPrice ?? 'N/A',
        investmentPrice: song.investmentPrice ?? 'N/A',
        fullRightsPrice: song.fullRightsPrice ?? 'N/A',
        investmentEnabled: song.investmentEnabled !== false,
        fullRightsEnabled: song.fullRightsEnabled !== false,
        hasFullAudioSource,
        hasPlayableAudioSource,
        txHash: String(song.txHash || ''),
        ipfsCid: String(song.ipfsCid || '')
    });

    if (existingCard && existingCard.dataset.songFingerprint === songFingerprint) {
        if (!options.skipImmediateQueueRebuild) {
            validateTrackAudio(existingCard).then(() => rebuildPlatformQueue());
        }
        return existingCard;
    }

    const card = document.createElement('div');
    card.id = cardDomId;
    card.className = 'card';
    card.style.margin = '12px 0';
    card.dataset.songFingerprint = songFingerprint;
    card.dataset.songTitle = String(song.title || 'Unknown Title');
    card.dataset.songArtist = String(song.artist || 'Unknown Artist');
    card.dataset.spotifyId = extractSpotifyTrackId(song.spotifyId || '');
    card.dataset.spotifyUrl = String(song.spotifyUrl || song.externalUrl || '');
    card.dataset.songFileCid = String(song.songFileCid || song.song_file_cid || '');
    card.dataset.ipfsCid = String(song.ipfsCid || song.ipfs_cid || '');
    card.dataset.audioUrl = String(song.audioUrl || '');
    card.dataset.audioFallbackUrl = String(song.audioFallbackUrl || '');
    card.dataset.previewUrl = previewUrl;
    card.dataset.durationMs = String(song.durationMs || song.duration_ms || '');
    card.dataset.coverArtUrl = String(song.coverArtUrl || song.cover_art_url || '');
    card.dataset.investmentPrice = String(song.investmentPrice ?? song.investment_price ?? '');
    card.dataset.hasFullAudioSource = hasFullAudioSource ? 'true' : 'false';
    card.dataset.hasPlayableAudioSource = hasPlayableAudioSource ? 'true' : 'false';
    const durationText = formatDurationMs(song.durationMs || song.duration_ms);
    card.innerHTML = `
        <div style="display:flex; justify-content:center; margin-bottom:12px;">
            <img class="track-cover-art" src="${song.coverArtUrl || song.cover_art_url || ''}" alt="${song.title || 'Song'} cover art" style="width:160px; height:160px; object-fit:cover; border-radius:14px; border:2px solid #00bfff; display:${(song.coverArtUrl || song.cover_art_url) ? 'block' : 'none'};" />
        </div>
        <h3>${song.title || 'Unknown Title'}</h3>
        <p>${song.artist || 'Unknown Artist'} | Song ID: ${hasNumericSongId ? numericSongId : 'Pending'}</p>
        ${durationText ? `<p>Length: ${durationText}</p>` : ''}
        <p>Download: ${song.downloadPrice ?? 'N/A'} AVAX | Investment: ${song.investmentPrice ?? 'N/A'} AVAX | Full Rights: ${song.fullRightsPrice ?? 'N/A'} AVAX</p>
        <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
            <button type="button" data-action="download" data-song-id="${hasNumericSongId ? numericSongId : ''}" ${hasNumericSongId ? '' : 'disabled'}>${hasNumericSongId ? 'Buy Download Rights' : 'Download Unavailable'}</button>
            <input id="${tokenInputId}" type="number" min="0.00001" step="0.00001" value="${song.investmentPrice ?? '0.00001'}" style="width:110px;" ${hasNumericSongId ? '' : 'disabled'} />
            <button type="button" data-action="invest" data-song-id="${hasNumericSongId ? numericSongId : ''}" data-feature-enabled="${song.investmentEnabled === false ? 'false' : 'true'}" ${!hasNumericSongId ? 'disabled' : ''}>${song.investmentEnabled === false ? 'Investment Disabled (Why?)' : (hasNumericSongId ? 'Invest' : 'Investment Unavailable')}</button>
            <button type="button" data-action="rights" data-song-id="${hasNumericSongId ? numericSongId : ''}" data-feature-enabled="${song.fullRightsEnabled === false ? 'false' : 'true'}" ${!hasNumericSongId ? 'disabled' : ''}>${song.fullRightsEnabled === false ? 'Rights Disabled (Why?)' : (hasNumericSongId ? 'Buy Full Rights' : 'Rights Unavailable')}</button>
        </div>
        <div style="margin-top:10px; padding-top:10px; border-top:1px solid rgba(0,191,255,0.35);">
            <p style="margin:0 0 8px 0; font-size:0.9em; color:#d8f4ff;">Track Player</p>
            ${hasPlayableAudioSource ? (hasFullAudioSource ? '' : '<p style="margin:0 0 8px 0; font-size:0.85em; color:#ffcf9e;">No direct full-length file yet. Spotify playback will be used where available.</p>') : '<p style="margin:0 0 8px 0; font-size:0.85em; color:#ffcf9e;">No full-length audio source is available for this track yet.</p>'}
            <div style="display:grid; grid-template-columns:repeat(2, minmax(120px, 1fr)); gap:8px;">
                <button type="button" data-action="track-play">Play</button>
                <button type="button" data-action="track-pause">Pause</button>
                <button type="button" data-action="track-stop">Stop</button>
                <button type="button" data-action="track-prev">Previous</button>
                <button type="button" data-action="track-next">Next</button>
                <button type="button" data-action="track-rewind">Rewind 10s</button>
                <button type="button" data-action="track-fastforward">Fast Forward 10s</button>
                <button type="button" data-action="track-shuffle">Shuffle: ${platformPlayerState.shuffle ? 'On' : 'Off'}</button>
                <button type="button" data-action="track-repeat">Repeat: ${platformPlayerState.repeat ? 'On' : 'Off'}</button>
                <div style="display:flex; align-items:center; gap:8px; grid-column:1 / -1;">
                    <label style="white-space:nowrap; font-size:0.85em; color:#d8f4ff;">Volume</label>
                    <input type="range" min="0" max="100" step="1" value="100" data-action="track-volume" style="flex:1;" />
                    <span data-role="track-volume-value" style="min-width:38px; text-align:right; font-size:0.85em; color:#d8f4ff;">100%</span>
                </div>
            </div>
        </div>
        ${song.txHash ? `<p style="font-size:0.85em; color:#555; margin-top:8px;">Initialise TX: ${song.txHash}</p>` : ''}
        ${song.ipfsCid ? `<p style="font-size:0.85em; color:#555; margin-top:8px;">IPFS CID: ${song.ipfsCid}</p>` : ''}
    `;
    if (existingCard) {
        existingCard.replaceWith(card);
    } else {
        container.appendChild(card);
    }

    hydrateCardCoverArt(card);
    if (!options.skipImmediateQueueRebuild) {
        validateTrackAudio(card).then(() => rebuildPlatformQueue());
    }
    return card;
}

async function performMarketplaceAction(endpoint, payload, successMessage) {
    const runtimeTag = 'INVEST_FIX_20260710d';
    if (!window.userWallet) {
        setMainStatus('Connecting wallet...');
        try {
            await connectWallet();
        } catch (_) {
            // connectWallet already handles alerts/errors.
        }
    }

    if (!window.userWallet) {
        setMainStatus('Please connect your wallet first to continue.');
        alert('Please connect your wallet first to continue.');
        return;
    }

    if (!window.ethereum) {
        setMainStatus('No wallet provider found. Install MetaMask to continue.');
        alert('No wallet provider found. Install MetaMask to continue.');
        return;
    }

    try {
        const switched = await ensureAvalancheCChain();
        if (!switched) {
            setMainStatus('Please switch MetaMask to Avalanche C-Chain (Fuji).');
            return;
        }

        setMainStatus('Preparing transaction...');

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

        const normalizeTxRequest = (request) => {
            const normalized = { ...(request || {}) };
            const bigintFields = ['value', 'gas', 'gasPrice', 'maxFeePerGas', 'maxPriorityFeePerGas', 'nonce', 'chainId'];

            for (const field of bigintFields) {
                const raw = normalized[field];
                if (raw === undefined || raw === null || raw === '') continue;

                if (typeof raw === 'bigint') continue;

                if (typeof raw === 'string') {
                    const text = raw.trim();
                    if (!text) {
                        delete normalized[field];
                        continue;
                    }
                    if (/^0x[0-9a-f]+$/i.test(text)) {
                        normalized[field] = text;
                        continue;
                    }
                    if (/^[0-9]+$/.test(text)) {
                        normalized[field] = BigInt(text);
                    }
                    continue;
                }

                if (typeof raw === 'number') {
                    if (!Number.isFinite(raw) || raw < 0 || !Number.isInteger(raw)) {
                        throw new Error(`Invalid numeric transaction field: ${field}`);
                    }
                    normalized[field] = BigInt(String(Math.trunc(raw)));
                }
            }

            delete normalized.from;
            return normalized;
        };

        const txRequest = normalizeTxRequest(data.tx);

        const toHexQuantity = (raw) => {
            if (raw === undefined || raw === null || raw === '') return undefined;

            let value;
            if (typeof raw === 'bigint') {
                value = raw;
            } else if (typeof raw === 'string') {
                const text = raw.trim();
                if (!text) return undefined;
                if (/^0x[0-9a-f]+$/i.test(text) || /^[0-9]+$/.test(text)) {
                    value = BigInt(text);
                } else {
                    return undefined;
                }
            } else if (typeof raw === 'number') {
                if (!Number.isFinite(raw) || raw < 0 || !Number.isInteger(raw)) {
                    return undefined;
                }
                if (!Number.isSafeInteger(raw)) {
                    throw new Error('Unsafe numeric transaction field encountered. Retry after backend restart.');
                }
                value = BigInt(raw);
            } else {
                return undefined;
            }

            return `0x${value.toString(16)}`;
        };

        const sendViaRpc = async (request) => {
            const rpcTx = {
                to: request.to,
                from: window.userWallet,
                data: request.data,
                value: toHexQuantity(request.value),
                gas: toHexQuantity(request.gas),
                gasPrice: toHexQuantity(request.gasPrice),
                maxFeePerGas: toHexQuantity(request.maxFeePerGas),
                maxPriorityFeePerGas: toHexQuantity(request.maxPriorityFeePerGas),
                nonce: toHexQuantity(request.nonce),
                chainId: toHexQuantity(request.chainId)
            };

            Object.keys(rpcTx).forEach((key) => {
                if (rpcTx[key] === undefined || rpcTx[key] === null || rpcTx[key] === '') {
                    delete rpcTx[key];
                }
            });

            const txHash = await window.ethereum.request({
                method: 'eth_sendTransaction',
                params: [rpcTx]
            });

            const receipt = await provider.waitForTransaction(txHash);
            return { hash: txHash, wait: async () => receipt };
        };

        setMainStatus('Confirm the transaction in your wallet...');
        const sentTx = await sendViaRpc(txRequest);
        setMainStatus('Transaction submitted. Waiting for confirmation...');
        await sentTx.wait();

        const statusEl = document.getElementById('status');
        if (statusEl) {
            statusEl.innerText = `${successMessage}. Tx: ${sentTx.hash}`;
        }

        const purchaseType = endpoint.includes('/buy_download')
            ? 'download'
            : endpoint.includes('/buy_rights')
                ? 'rights'
                : endpoint.includes('/invest')
                    ? 'invest'
                    : '';

        if (purchaseType) {
            try {
                await fetch('http://127.0.0.1:5000/buy', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        song_id: Number(payload.song_id),
                        wallet: window.userWallet,
                        tx_hash: sentTx.hash,
                        type: purchaseType,
                        amount: purchaseType === 'invest' ? Number(payload.token_amount || 0) : undefined
                    })
                });
            } catch (recordErr) {
                console.warn('Purchase recording failed:', recordErr);
            }

            if (purchaseType === 'download') {
                try {
                    await loadLibrary();
                } catch (_) {
                    // Non-fatal refresh failure.
                }
            }
        }
    } catch (error) {
        console.error('Marketplace action failed:', error);
        const details = `${runtimeTag} | endpoint=${endpoint} | ${error.message || String(error)}`;
        const statusEl = document.getElementById('status');
        if (statusEl) {
            statusEl.innerText = `Transaction failed: ${details}`;
        }
        alert(`Transaction failed: ${details}`);
    }
}

// Upload a Spotify song's metadata to IPFS via the backend
async function addSpotifySongToBlockchain(title, artist, spotifyId, coverArtUrl = '', durationMs = '') {
    if (!window.userWallet) {
        alert("Please connect your wallet first");
        return;
    }

    const switched = await ensureAvalancheCChain();
    if (!switched) {
        const statusEl = document.getElementById('status');
        if (statusEl) {
            statusEl.innerText = 'Please switch MetaMask to Avalanche C-Chain (Fuji) to initialise songs.';
        }
        return;
    }

    const downloadPrice = prompt('Enter download price in AVAX:', '0.00001');
    if (downloadPrice === null) return;

    const investmentPrice = prompt('Enter investment price in AVAX:', '0.00001');
    if (investmentPrice === null) return;

    const fullRightsPrice = prompt('Enter full rights price in AVAX:', '0.00001');
    if (fullRightsPrice === null) return;

    const saleMode = prompt('Type one of: investment, rights, both. Type none to disable both investment and full rights.', 'both');
    if (saleMode === null) return;
    const normalizedMode = String(saleMode || '').trim().toLowerCase();
    const investmentEnabled = normalizedMode === 'investment' || normalizedMode === 'both';
    const fullRightsEnabled = normalizedMode === 'rights' || normalizedMode === 'both';

    try {
        const response = await fetch('http://127.0.0.1:5000/marketplace/upload_spotify_song', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title,
                artist,
                spotify_id: spotifyId,
                cover_art_url: coverArtUrl || '',
                duration_ms: durationMs || null,
                download_price: downloadPrice,
                investment_price: investmentPrice,
                full_rights_price: fullRightsPrice,
                investment_enabled: investmentEnabled,
                full_rights_enabled: fullRightsEnabled,
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
                investmentPrice,
                fullRightsPrice,
                investmentEnabled,
                fullRightsEnabled
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
                investmentPrice,
                fullRightsPrice,
                investmentEnabled,
                fullRightsEnabled,
                txHash
            });
        }

        showIPFSResult(title, artist, data.ipfs_cid, txHash, blockchainRegistered, blockchainError);
    } catch (err) {
        console.error('Upload error:', err);
        alert("Failed to upload song: " + (err.message || err.toString()));
    }
}

async function registerSongOnBlockchainViaWallet(title, artist, spotifyId, downloadPrice, investmentPrice, fullRightsPrice, investmentEnabled, fullRightsEnabled) {
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
                investment_price: investmentPrice,
                full_rights_price: fullRightsPrice,
                investment_enabled: investmentEnabled,
                full_rights_enabled: fullRightsEnabled,
                wallet: window.userWallet
            })
        });

        const buildData = await buildResp.json();
        if (!buildResp.ok || !buildData.success || !buildData.tx) {
            return { success: false, error: buildData.error || 'Failed to build blockchain transaction' };
        }

        const provider = new ethers.BrowserProvider(window.ethereum);

        // Signer controls sender address; normalize numeric fields to avoid unsafe JS number overflows.
        const txRequest = { ...buildData.tx };
        const bigintFields = ['value', 'gas', 'gasPrice', 'maxFeePerGas', 'maxPriorityFeePerGas', 'nonce', 'chainId'];
        for (const field of bigintFields) {
            const raw = txRequest[field];
            if (raw === undefined || raw === null || raw === '') continue;
            if (typeof raw === 'bigint') continue;
            if (typeof raw === 'string') {
                const text = raw.trim();
                if (/^[0-9]+$/.test(text)) txRequest[field] = BigInt(text);
                continue;
            }
            if (typeof raw === 'number' && Number.isFinite(raw) && Number.isInteger(raw) && raw >= 0) {
                txRequest[field] = BigInt(String(Math.trunc(raw)));
            }
        }
        delete txRequest.from;

        const toHexQuantity = (raw) => {
            if (raw === undefined || raw === null || raw === '') return undefined;

            let value;
            if (typeof raw === 'bigint') {
                value = raw;
            } else if (typeof raw === 'string') {
                const text = raw.trim();
                if (!text) return undefined;
                if (/^0x[0-9a-f]+$/i.test(text) || /^[0-9]+$/.test(text)) {
                    value = BigInt(text);
                } else {
                    return undefined;
                }
            } else if (typeof raw === 'number') {
                if (!Number.isFinite(raw) || raw < 0 || !Number.isInteger(raw)) {
                    return undefined;
                }
                if (!Number.isSafeInteger(raw)) {
                    throw new Error('Unsafe numeric transaction field encountered. Retry after backend restart.');
                }
                value = BigInt(raw);
            } else {
                return undefined;
            }

            return `0x${value.toString(16)}`;
        };

        const sendViaRpc = async (request) => {
            const rpcTx = {
                to: request.to,
                from: window.userWallet,
                data: request.data,
                value: toHexQuantity(request.value),
                gas: toHexQuantity(request.gas),
                gasPrice: toHexQuantity(request.gasPrice),
                maxFeePerGas: toHexQuantity(request.maxFeePerGas),
                maxPriorityFeePerGas: toHexQuantity(request.maxPriorityFeePerGas),
                nonce: toHexQuantity(request.nonce),
                chainId: toHexQuantity(request.chainId)
            };

            Object.keys(rpcTx).forEach((key) => {
                if (rpcTx[key] === undefined || rpcTx[key] === null || rpcTx[key] === '') {
                    delete rpcTx[key];
                }
            });

            const txHash = await window.ethereum.request({
                method: 'eth_sendTransaction',
                params: [rpcTx]
            });
            const receipt = await provider.waitForTransaction(txHash);
            return { hash: txHash, wait: async () => receipt };
        };

        const sentTx = await sendViaRpc(txRequest);
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

if (typeof window !== 'undefined') {
    window.addSpotifySongToBlockchain = addSpotifySongToBlockchain;
    window.searchSpotifyArtist = searchSpotifyArtist;
}

function showIPFSResult(title, artist, ipfsCid, txHash, blockchainRegistered, blockchainError) {
    let resultContainer = document.getElementById('ipfsResultContainer');
    if (!resultContainer) {
        const div = document.createElement('div');
        div.id = 'ipfsResultContainer';
        const searchResults = document.getElementById('searchResults');
        const initialisedSongsList = document.getElementById('initialisedSongsList');
        if (searchResults && searchResults.parentNode) {
            searchResults.parentNode.insertBefore(div, searchResults);
        } else if (initialisedSongsList && initialisedSongsList.parentNode) {
            initialisedSongsList.parentNode.insertBefore(div, initialisedSongsList);
        } else {
            document.body.appendChild(div);
        }
        resultContainer = div;
    }
    const container = resultContainer || document.getElementById('ipfsResultContainer');
    if (!container) {
        return;
    }
    
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
    const coverArtUrl = item.album_image || item.cover_art_url || '';
    const durationText = formatDurationMs(item.duration_ms || item.durationMs);
    const purchaseType = String(item.type || '').toLowerCase();
    const isPurchasedType = purchaseType === 'download' || purchaseType === 'invest' || purchaseType === 'rights';
    const audioUrl = String(item.audio_url || '').trim();
    const songFileCid = String(item.song_file_cid || '').trim();
    const previewUrl = String(item.preview_url || '').trim();
    const fallbackAudio = songFileCid ? `/ipfs/file/${encodeURIComponent(songFileCid)}` : '';
    const ipfsGatewayAudio = String(item.audio_fallback_url || (songFileCid ? `https://ipfs.io/ipfs/${encodeURIComponent(songFileCid)}` : '')).trim();
    const pinataGatewayAudio = String(item.audio_alt_gateway_url || (songFileCid ? `https://gateway.pinata.cloud/ipfs/${encodeURIComponent(songFileCid)}` : '')).trim();
    const localhostAudio = songFileCid ? `http://127.0.0.1:5000/ipfs/file/${encodeURIComponent(songFileCid)}` : '';
    const fullAudioCandidates = Array.from(new Set([
        audioUrl,
        fallbackAudio,
        localhostAudio,
        ...buildIpfsAudioCandidates(songFileCid),
        ipfsGatewayAudio,
        pinataGatewayAudio
    ].filter(Boolean)));
    const audioCandidates = Array.from(new Set([
        ...fullAudioCandidates,
        previewUrl
    ].filter(Boolean)));
    const hasPlayable = audioCandidates.length > 0;
    const spotifyTrackId = extractSpotifyTrackId(String(item.spotify_id || item.id || item.external_url || '').trim());
    const spotifyEmbedUrl = spotifyTrackId ? `https://open.spotify.com/embed/track/${encodeURIComponent(spotifyTrackId)}` : '';
    const txHash = String(item.tx_hash || '').trim();
    const txShort = txHash ? `${txHash.slice(0, 10)}...${txHash.slice(-8)}` : '';
    const cidShort = songFileCid ? `${songFileCid.slice(0, 12)}...${songFileCid.slice(-10)}` : '';
    const playerTitle = String(item.first_track_title || item.title || item.name || '').trim();
    const playerArtist = String(item.first_track_artist || item.artist || '').trim();

    const metaChips = [];
    if (item.artist) metaChips.push(`<span class="purchase-chip"><strong>Artist</strong> ${item.artist}</span>`);
    if (item.album) metaChips.push(`<span class="purchase-chip"><strong>Album</strong> ${item.album}</span>`);
    if (durationText) metaChips.push(`<span class="purchase-chip"><strong>Length</strong> ${durationText}</span>`);
    if (item.type) metaChips.push(`<span class="purchase-chip"><strong>Type</strong> ${item.type}</span>`);
    if (item.amount) metaChips.push(`<span class="purchase-chip"><strong>Amount</strong> ${item.amount} AVAX</span>`);
    if (item.status) metaChips.push(`<span class="purchase-chip"><strong>Status</strong> ${item.status}</span>`);

    return `
        <div class="track-result purchase-track-card" data-song-id="${String(item.song_id || '').replace(/"/g, '&quot;')}" data-song-title="${playerTitle.replace(/"/g, '&quot;')}" data-song-artist="${playerArtist.replace(/"/g, '&quot;')}" data-song-file-cid="${songFileCid.replace(/"/g, '&quot;')}" data-ipfs-cid="${String(item.ipfs_cid || '').replace(/"/g, '&quot;')}" data-preview-url="${previewUrl.replace(/"/g, '&quot;')}" data-spotify-id="${String(item.spotify_id || item.id || '').replace(/"/g, '&quot;')}" data-audio-url="${audioUrl.replace(/"/g, '&quot;')}" data-audio-fallback-url="${ipfsGatewayAudio.replace(/"/g, '&quot;')}" data-spotify-url="${String(item.external_url || '').replace(/"/g, '&quot;')}" data-cover-art-url="${coverArtUrl.replace(/"/g, '&quot;')}">
            ${coverArtUrl ? `<img class="purchase-track-cover" src="${coverArtUrl}" alt="${(item.title || item.name || 'Song').replace(/"/g, '&quot;')} cover" />` : ''}
            <div class="purchase-track-body">
                <h3 class="purchase-track-title">${item.title || item.name}</h3>
                ${item.description ? `<p class="purchase-track-meta">${item.description}</p>` : ''}
                ${metaChips.length ? `<div class="purchase-track-chips">${metaChips.join('')}</div>` : ''}
                ${txHash ? `<p class="purchase-track-meta purchase-track-hash" title="${txHash}"><strong>Tx:</strong> ${txShort}</p>` : ''}
                ${songFileCid ? `<p class="purchase-track-meta purchase-track-cid" title="${songFileCid}"><strong>IPFS Audio CID:</strong> ${cidShort}</p>` : ''}
                ${(isPurchasedType || item.type === 'album') ? `<button type="button" onclick="playLibraryTrackFromCard(this)" style="margin-top:10px;">Play</button>` : ''}
                ${((isPurchasedType || item.type === 'album') && !spotifyEmbedUrl) ? `<p class="purchase-track-meta" style="color:#ffcf9e;">This item will only play here once Spotify player metadata is available.</p>` : ''}
            </div>
            ${item.external_url ? `<button onclick="addSpotifySongToBlockchain('${(item.title||item.name).replace(/'/g, "\\'")}', '${(item.artist||'').replace(/'/g, "\\'")}', '${(item.spotify_id||item.id||'').replace(/'/g, "\\'")}', '${(item.album_image||item.cover_art_url||'').replace(/'/g, "\\'")}', '${String(item.duration_ms || item.durationMs || '').replace(/'/g, "\\'")}')">Initialise</button>` : ''}
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
    bindPurchaseTrackAudioGuards();
}

function bindPurchaseTrackAudioGuards() {
    if (window.__purchaseTrackAudioGuardsBound) return;
    window.__purchaseTrackAudioGuardsBound = true;

    const ensurePreviewWarning = (audioEl, text) => {
        const parent = audioEl.parentElement;
        if (!parent) return;
        if (parent.querySelector('.purchase-track-preview-warning')) return;
        const warning = document.createElement('p');
        warning.className = 'purchase-track-meta purchase-track-preview-warning';
        warning.style.color = '#ffcf9e';
        warning.textContent = text;
        parent.appendChild(warning);
    };

    document.addEventListener('loadedmetadata', (event) => {
        const audioEl = event.target;
        if (!(audioEl instanceof HTMLAudioElement)) return;
        if (!audioEl.classList.contains('purchase-track-audio')) return;

        const expectedDurationMs = Number(audioEl.dataset.expectedDurationMs || 0);
        const expectedDurationSeconds = expectedDurationMs > 0 ? expectedDurationMs / 1000 : 0;
        const actualDurationSeconds = Number(audioEl.duration || 0);
        if (!isLikelyPreviewClip(expectedDurationSeconds, actualDurationSeconds)) return;

        ensurePreviewWarning(audioEl, 'Detected preview-length audio. Full-length source is not available for this track yet.');
    }, true);

    document.addEventListener('error', (event) => {
        const audioEl = event.target;
        if (!(audioEl instanceof HTMLAudioElement)) return;
        if (!audioEl.classList.contains('purchase-track-audio')) return;

        const encodedCandidates = String(audioEl.dataset.audioCandidates || '').trim();
        let candidates = [];
        if (encodedCandidates) {
            try {
                const parsed = JSON.parse(decodeURIComponent(encodedCandidates));
                if (Array.isArray(parsed)) {
                    candidates = parsed.filter(Boolean);
                }
            } catch (_) {}
        }

        if (candidates.length) {
            const wasPlaying = !audioEl.paused && !audioEl.ended;
            const currentSrc = String(audioEl.currentSrc || audioEl.src || '').trim();
            const currentIndex = candidates.findIndex((url) => String(url).trim() === currentSrc);
            const nextIndex = currentIndex >= 0 ? currentIndex + 1 : 1;
            const nextSource = candidates[nextIndex];
            if (nextSource && String(audioEl.dataset.sourceFallbackExhausted || '') !== '1') {
                audioEl.src = nextSource;
                audioEl.load();
                if (wasPlaying) {
                    audioEl.play().catch(() => {});
                }
                ensurePreviewWarning(audioEl, 'Primary audio source failed. Switched to another source.');
                if (nextIndex >= candidates.length - 1) {
                    audioEl.dataset.sourceFallbackExhausted = '1';
                }
                return;
            }
        }

        const previewUrl = String(audioEl.dataset.previewUrl || '').trim();
        if (!previewUrl) return;
        if (audioEl.dataset.previewActivated === '1') return;

        const wasPlaying = !audioEl.paused && !audioEl.ended;
        audioEl.dataset.previewActivated = '1';
        audioEl.src = previewUrl;
        audioEl.load();
        if (wasPlaying) {
            audioEl.play().catch(() => {});
        }
        ensurePreviewWarning(audioEl, 'Primary audio source failed. Playing preview instead.');
    }, true);
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

async function playLibraryTrackFromCard(triggerEl) {
    const cardEl = triggerEl?.closest?.('.purchase-track-card');
    if (!cardEl) return;

    cardEl.dataset.audioValidated = 'false';
    await validateTrackAudio(cardEl);
    setCurrentTrackFromCard(cardEl);
    const started = await playTrackFromCard(cardEl);
    if (!started) {
        setMainStatus('Spotify player is not available for this item yet.');
    }
}

if (typeof window !== 'undefined') {
    window.playLibraryTrackFromCard = playLibraryTrackFromCard;
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
        const downloadedItems = libraryItems.filter((item) => {
            const t = String(item?.type || '').trim().toLowerCase();
            return t === 'download' || t === 'rights' || t === 'full_rights' || t === 'full rights';
        });

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
    // Delegate to the enhanced artistSearch function defined in index.html (has Show More support)
    if (typeof window.artistSearch === 'function') {
        return window.artistSearch();
    }
    const query = document.getElementById("spotifyArtistSearch").value.trim();
    if (!query) {
        alert("Enter an artist name or Spotify artist ID to connect.");
        return;
    }

    async function loadLocalArtistTracks(artistQuery) {
        try {
            const approvedResp = await fetch('http://127.0.0.1:5000/song/registered?status=approved');
            if (!approvedResp.ok) return [];
            const approvedSongs = await approvedResp.json();
            if (!Array.isArray(approvedSongs)) return [];

            const normalize = (v) => String(v || '').trim().toLowerCase();
            const wanted = normalize(artistQuery);
            if (!wanted) return [];

            return approvedSongs
                .filter((song) => {
                    const artistValue = Array.isArray(song.artistNames)
                        ? song.artistNames.join(', ')
                        : (song.artistNames || song.artist || '');
                    return normalize(artistValue).includes(wanted) || wanted.includes(normalize(artistValue));
                })
                .map((song) => ({
                    id: song.spotify_id || song.id || '',
                    name: song.songTitle || song.title || 'Unknown',
                    album: {
                        name: song.album || 'Platform Registered Song',
                        type: 'registered',
                        image: song.album_image || ''
                    },
                    source: 'platform'
                }));
        } catch (_) {
            return [];
        }
    }

    try {
        const artistLibrary = document.getElementById('artistLibrary');
        if (!artistLibrary) return;

        artistLibrary.innerHTML = '<p>Loading artist data...</p>';

        let apiBase = DEFAULT_API_BASE;
        try {
            apiBase = await resolveApiBase();
        } catch (_) {
            apiBase = 'http://127.0.0.1:5000';
        }

        let response = await fetch(`${apiBase}/spotify/artist?artist=${encodeURIComponent(query)}`);
        if (!response.ok && apiBase !== 'http://127.0.0.1:5000') {
            // Retry once against the canonical local Flask port used in this project.
            response = await fetch(`http://127.0.0.1:5000/spotify/artist?artist=${encodeURIComponent(query)}`);
        }
        let artistData;
        if (!response.ok) {
            artistData = {
                id: query,
                name: query,
                tracks: await loadLocalArtistTracks(query),
                albums: [],
                playlists: []
            };
        } else {
            artistData = await response.json();
        }

        if (artistData.error) {
            artistData = {
                id: query,
                name: query,
                tracks: await loadLocalArtistTracks(query),
                albums: [],
                playlists: []
            };
        }

        if (!artistData.tracks || !Array.isArray(artistData.tracks) || artistData.tracks.length === 0) {
            artistLibrary.innerHTML = `
                <p>No tracks were found for "${artistData.name || query}".</p>
                <p>Try another artist name.</p>
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
                        <p style="margin:0 0 8px 0; color:#555;">${(track.album?.type || 'album').toLowerCase() === 'single' ? 'Single' : ((track.album?.type || 'album').toLowerCase() === 'registered' ? 'Registered' : 'Album')}: ${track.album?.name || 'Unknown'}${track.source === 'platform' ? ' | Source: Platform' : ' | Source: Spotify'}</p>
                        <button type="button" class="artist-initialise-btn" style="background:#1db954; color:#fff; border:none; padding:8px 12px; border-radius:6px; cursor:pointer; font-weight:600;">Initialise</button>
                    </div>
                </div>
            `).join('')}
            ${artistData.tracks.length > 10 ? `<div style="text-align:center; margin-top:8px;"><button type="button" class="toggle-artist-btn" data-toggle-id="${artistId}" data-expanded="false" id="toggleArtistBtn_${artistId}">Show more</button></div>` : ''}
        `;
    } catch (error) {
        console.error('Error loading Spotify artist library:', error);
        const artistLibrary = document.getElementById('artistLibrary');
        if (!artistLibrary) return;
        const fallbackTracks = await loadLocalArtistTracks(query);
        if (!fallbackTracks.length) {
            artistLibrary.innerHTML = `<p>No tracks were found for "${query}".</p>`;
            return;
        }

        artistLibrary.innerHTML = `
            <h3>${query}</h3>
            ${fallbackTracks.map((track, idx) => `
                <div class="artist-result${idx >= 10 ? ' extra-artist-track' : ''}" data-artist="${query}" data-track-id="${track.id || ''}" data-track-name="${(track.name || '').replace(/"/g, '&quot;')}" data-artist-name="${query.replace(/"/g, '&quot;')}" style="display:flex; gap:12px; align-items:flex-start; margin-bottom:16px; padding:12px; border:1px solid #ddd; border-radius:10px; background:#fafafa; ${idx >= 10 ? 'display:none;' : ''}">
                    ${track.album?.image ? `<img src="${track.album.image}" alt="${track.album.name || 'album'} cover" style="width:120px; height:120px; object-fit:cover; border-radius:10px;" />` : '<div style="width:120px;height:120px;border-radius:10px;background:linear-gradient(135deg,#d7dde8,#f2f4f8);display:flex;align-items:center;justify-content:center;color:#556;font-weight:700;">No Cover</div>'}
                    <div class="artist-result-info" style="flex:1;">
                        <h4 style="margin:0 0 8px 0;">${track.name || 'Unknown'}</h4>
                        <p style="margin:0 0 8px 0; color:#555;">Registered: ${track.album?.name || 'Platform Registered Song'} | Source: Platform</p>
                        <button type="button" class="artist-initialise-btn" style="background:#1db954; color:#fff; border:none; padding:8px 12px; border-radius:6px; cursor:pointer; font-weight:600;">Initialise</button>
                    </div>
                </div>
            `).join('')}
            ${fallbackTracks.length > 10 ? `<div style="text-align:center; margin-top:8px;"><button type="button" class="toggle-artist-btn" data-toggle-id="${query}" data-expanded="false" id="toggleArtistBtn_${query}">Show more</button></div>` : ''}
        `;
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

function normalizeLibraryPurchaseType(value) {
    return String(value || '').trim().toLowerCase();
}

function isInvestmentLibraryType(value) {
    const t = normalizeLibraryPurchaseType(value);
    return t === 'invest' || t === 'investment';
}

function isDownloadedLibraryType(value) {
    const t = normalizeLibraryPurchaseType(value);
    return t === 'download' || t === 'rights' || t === 'full_rights' || t === 'full rights' || t === 'download_rights';
}

function dedupeLibraryItemsBySong(items, includeTypeInKey = true) {
    const source = Array.isArray(items) ? items : [];
    const byKey = new Map();

    source.forEach((item) => {
        const title = String(item?.title || item?.name || '').trim().toLowerCase();
        const artist = String(item?.artist || '').trim().toLowerCase();
        const songId = String(item?.song_id || item?.songId || '').trim().toLowerCase();
        const type = normalizeLibraryPurchaseType(item?.type);
        const keyParts = [songId || '-', title || '-', artist || '-'];
        if (includeTypeInKey) keyParts.push(type || '-');
        const key = keyParts.join('::');

        if (!byKey.has(key)) {
            byKey.set(key, item);
            return;
        }

        const existing = byKey.get(key);
        const existingTs = Number(existing?.timestamp || 0);
        const currentTs = Number(item?.timestamp || 0);
        if (currentTs >= existingTs) {
            byKey.set(key, item);
        }
    });

    return Array.from(byKey.values());
}

function buildAlbumSummaryItems(items) {
    const grouped = new Map();
    const uniqueItems = dedupeLibraryItemsBySong(items, false);
    uniqueItems.forEach((item) => {
        if (!isDownloadedLibraryType(item?.type)) return;
        const albumName = String(item?.album || 'Unknown Album').trim() || 'Unknown Album';
        const key = albumName.toLowerCase();
        if (!grouped.has(key)) {
            grouped.set(key, {
                album: albumName,
                tracks: [],
                artists: new Set(),
                cover: '',
                types: new Set()
            });
        }
        const group = grouped.get(key);
        group.tracks.push(item);
        group.types.add(String(item?.type || '').trim() || 'download');
        const artistValue = String(item?.artist || '').trim();
        if (artistValue) group.artists.add(artistValue);
        if (!group.cover) {
            group.cover = String(item?.album_image || item?.cover_art_url || '').trim();
        }
    });

    return Array.from(grouped.values())
        .sort((a, b) => a.album.localeCompare(b.album))
        .map((group) => {
            const firstTrack = group.tracks[0] || {};
            const trackTitles = group.tracks
                .map((track) => String(track?.title || track?.name || '').trim())
                .filter(Boolean)
                .slice(0, 6);
            const moreCount = Math.max(0, group.tracks.length - trackTitles.length);
            const description = `${group.tracks.length} purchased track(s)${trackTitles.length ? `: ${trackTitles.join(', ')}${moreCount ? ` +${moreCount} more` : ''}` : ''}`;
            return {
                title: group.album,
                artist: Array.from(group.artists).join(', ') || 'Unknown Artist',
                type: 'album',
                status: Array.from(group.types).join(', '),
                description,
                album_image: group.cover,
                cover_art_url: group.cover,
                song_file_cid: firstTrack.song_file_cid || '',
                audio_url: firstTrack.audio_url || '',
                audio_fallback_url: firstTrack.audio_fallback_url || '',
                preview_url: firstTrack.preview_url || '',
                spotify_id: firstTrack.spotify_id || firstTrack.id || '',
                song_id: firstTrack.song_id || firstTrack.songId || '',
                first_track_title: firstTrack.title || firstTrack.name || group.album,
                first_track_artist: firstTrack.artist || (Array.from(group.artists).join(', ') || 'Unknown Artist')
            };
        });
}

function renderLibrarySubsections(libraryItems) {
    const uniqueItems = dedupeLibraryItemsBySong(libraryItems, true);
    const investments = dedupeLibraryItemsBySong(uniqueItems.filter((item) => isInvestmentLibraryType(item?.type)), false);
    const downloadedSongs = dedupeLibraryItemsBySong(uniqueItems.filter((item) => isDownloadedLibraryType(item?.type)), false);
    const downloadedAlbums = buildAlbumSummaryItems(downloadedSongs);

    displayItems('investmentList', investments, 'No investments found yet.');
    displayItems('downloadedSongsList', downloadedSongs, 'No downloaded songs found yet.');
    displayItems('downloadedAlbumsList', downloadedAlbums, 'No downloaded albums found yet.');
}

function renderArtistGroupedLibrary(libraryItems, containerId = 'library') {
    const container = document.getElementById(containerId);
    if (!container) return;

    const items = dedupeLibraryItemsBySong(
        (Array.isArray(libraryItems) ? libraryItems : []).filter((item) => isDownloadedLibraryType(item?.type)),
        false
    );
    if (!items.length) {
        container.innerHTML = '<p>No artists found in your library yet.</p>';
        return;
    }

    const grouped = new Map();
    items.forEach((item) => {
        const artistName = String(item?.artist || 'Unknown Artist').trim() || 'Unknown Artist';
        const key = artistName.toLowerCase();
        if (!grouped.has(key)) {
            grouped.set(key, { artist: artistName, tracks: [] });
        }
        grouped.get(key).tracks.push(item);
    });

    const html = Array.from(grouped.values())
        .sort((a, b) => a.artist.localeCompare(b.artist))
        .map((group) => {
            const tracks = group.tracks
                .sort((a, b) => Number(b?.timestamp || 0) - Number(a?.timestamp || 0));
            return `
                <section class="artist-library-group" style="margin-bottom:22px;">
                    <h3 style="margin:0 0 10px 0;">${group.artist} (${tracks.length})</h3>
                    ${tracks.map(formatTrackCard).join('')}
                </section>
            `;
        })
        .join('');

    container.innerHTML = html;
    bindPurchaseTrackAudioGuards();
}

async function ensureLibraryCacheLoaded() {
    const cached = Array.isArray(window.__cachedLibraryItems) ? window.__cachedLibraryItems : [];
    if (cached.length > 0) return cached;
    await loadLibrary();
    return Array.isArray(window.__cachedLibraryItems) ? window.__cachedLibraryItems : [];
}

async function loadInvestmentsView() {
    const items = await ensureLibraryCacheLoaded();
    const investments = items.filter((item) => isInvestmentLibraryType(item?.type));
    displayItems('library', investments, 'No investments found yet.');
}

async function viewDownloadedSongs() {
    const items = await ensureLibraryCacheLoaded();
    const downloadedSongs = items.filter((item) => isDownloadedLibraryType(item?.type));
    displayItems('library', downloadedSongs, 'No downloaded songs found yet.');
}

async function viewDownloadedAlbums() {
    const items = await ensureLibraryCacheLoaded();
    const downloadedAlbums = buildAlbumSummaryItems(items);
    displayItems('library', downloadedAlbums, 'No downloaded albums found yet.');
}

async function loadLibrary() {
    const libraryContainer = document.getElementById("library");
    const fanListContainer = document.getElementById('initialisedSongsList');
    if (!libraryContainer && !fanListContainer) return;

    const wallet = window.userWallet || "";
    const url = wallet ? `http://127.0.0.1:5000/library?wallet=${encodeURIComponent(wallet)}` : "http://127.0.0.1:5000/library";

    try {
        const response = await fetch(url);
        let libraryItems = await response.json();

        // Merge richer metadata from approved/initialised songs so old purchase rows
        // still show cover art, duration and full audio sources.
        try {
            const approvedResponse = await fetch('http://127.0.0.1:5000/song/registered?status=approved').catch(() => null);
            const approvedPayload = (approvedResponse && approvedResponse.ok) ? await approvedResponse.json() : [];
            const approvedSongs = Array.isArray(approvedPayload)
                ? approvedPayload
                : (Array.isArray(approvedPayload && approvedPayload.songs) ? approvedPayload.songs : []);

            const bySongId = new Map();
            const byTitleArtist = new Map();
            approvedSongs.forEach((song) => {
                const sid = String(song.blockchain_song_id || song.song_id || song.songId || '').trim();
                const title = String(song.songTitle || song.title || '').trim().toLowerCase();
                const artist = String(Array.isArray(song.artistNames) ? song.artistNames[0] : (song.artistNames || song.artist || '')).trim().toLowerCase();
                if (sid && !bySongId.has(sid)) bySongId.set(sid, song);
                if (title && artist && !byTitleArtist.has(`${title}::${artist}`)) {
                    byTitleArtist.set(`${title}::${artist}`, song);
                }
            });

            libraryItems = (Array.isArray(libraryItems) ? libraryItems : []).map((item) => {
                const sid = String(item.song_id || item.songId || '').trim();
                const key = `${String(item.title || '').trim().toLowerCase()}::${String(item.artist || '').trim().toLowerCase()}`;
                const approved = (sid && bySongId.get(sid)) || byTitleArtist.get(key);
                if (!approved) return item;

                const mergedSongFileCid = String(item.song_file_cid || approved.song_file_cid || approved.songFileCid || '').trim();
                return {
                    ...item,
                    title: item.title || approved.songTitle || approved.title,
                    artist: item.artist || (Array.isArray(approved.artistNames) ? approved.artistNames.join(', ') : (approved.artistNames || approved.artist || '')),
                    album: item.album || approved.album || 'Unknown Album',
                    album_image: item.album_image || approved.album_image || approved.cover_art_url || approved.coverArtUrl || '',
                    cover_art_url: item.cover_art_url || item.album_image || approved.cover_art_url || approved.album_image || approved.coverArtUrl || '',
                    duration_ms: item.duration_ms || item.durationMs || approved.duration_ms || approved.durationMs || null,
                    song_file_cid: mergedSongFileCid,
                    preview_url: item.preview_url || approved.preview_url || approved.previewUrl || '',
                    audio_url: item.audio_url || (mergedSongFileCid ? `/ipfs/file/${encodeURIComponent(mergedSongFileCid)}` : ''),
                    audio_fallback_url: item.audio_fallback_url || (mergedSongFileCid ? `https://ipfs.io/ipfs/${encodeURIComponent(mergedSongFileCid)}` : ''),
                    audio_alt_gateway_url: item.audio_alt_gateway_url || (mergedSongFileCid ? `https://gateway.pinata.cloud/ipfs/${encodeURIComponent(mergedSongFileCid)}` : '')
                };
            });
        } catch (_) {
            // Keep original library data if approved-song enrichment fails.
        }

        const normalizedLibraryItems = dedupeLibraryItemsBySong(Array.isArray(libraryItems) ? libraryItems : [], true);
        window.__cachedLibraryItems = normalizedLibraryItems;
        renderLibrarySubsections(normalizedLibraryItems);
        displayItems('library', [], 'Use View Artists to see downloaded tracks grouped by artist.');
    } catch (error) {
        console.error("Error loading library:", error);
        window.__cachedLibraryItems = [];
        renderLibrarySubsections([]);
        displayItems('library', [], 'Failed to load library.');
    }

    if (!fanListContainer) {
        return;
    }

    try {
        const approvedResponse = await fetch('http://127.0.0.1:5000/song/registered?status=approved').catch(() => null);

        if (approvedResponse && approvedResponse.ok) {
            const approvedPayload = await approvedResponse.json();
            const approvedSongsRaw = Array.isArray(approvedPayload)
                ? approvedPayload
                : (Array.isArray(approvedPayload && approvedPayload.songs) ? approvedPayload.songs : []);
            const approvedSongs = pickBestApprovedSongs(approvedSongsRaw);
            const activeCardIds = new Set();
            const contractResponse = await fetch('http://127.0.0.1:5000/songs').catch(() => null);
            const contractSongs = (contractResponse && contractResponse.ok)
                ? (await contractResponse.json())
                : [];

            const bySpotifyId = new Map();
            const byTitleArtist = new Map();
            if (Array.isArray(contractSongs)) {
                contractSongs.forEach((cSong) => {
                    const cId = cSong.songId ?? cSong.song_id ?? cSong.id;
                    const cSpotifyId = String(cSong.spotify_id || cSong.spotifyId || '').trim();
                    const key = `${String(cSong.title || '').trim().toLowerCase()}::${String(cSong.artist || '').trim().toLowerCase()}`;
                    if (cSpotifyId) bySpotifyId.set(cSpotifyId, cSong);
                    if (key && !byTitleArtist.has(key)) byTitleArtist.set(key, cSong);
                    if (cId !== undefined && cId !== null) {
                        const idKey = `id_${String(cId)}`;
                        if (!byTitleArtist.has(idKey)) byTitleArtist.set(idKey, cSong);
                    }
                });
            }

            approvedSongs.forEach(song => {
                const songSpotifyId = String(song.spotify_id || song.spotifyId || '').trim();
                const titleArtistKey = `${String(song.songTitle || song.title || '').trim().toLowerCase()}::${String(Array.isArray(song.artistNames) ? song.artistNames[0] : (song.artistNames || song.artist || '')).trim().toLowerCase()}`;
                const contractMatch = (songSpotifyId && bySpotifyId.get(songSpotifyId)) || byTitleArtist.get(titleArtistKey);
                const resolvedSongId =
                    song.blockchain_song_id ??
                    song.song_id ??
                    song.songId ??
                    (contractMatch ? (contractMatch.songId ?? contractMatch.song_id ?? contractMatch.id) : null);

                const resolvedSongFileCid =
                    song.song_file_cid ||
                    song.songFileCid ||
                    (contractMatch ? (contractMatch.song_file_cid || contractMatch.songFileCid || '') : '');

                const resolvedAudioUrl = resolvedSongFileCid
                    ? `/ipfs/file/${encodeURIComponent(String(resolvedSongFileCid))}`
                    : '';
                const resolvedAudioFallbackUrl = resolvedSongFileCid
                    ? `https://ipfs.io/ipfs/${encodeURIComponent(String(resolvedSongFileCid))}`
                    : '';

                const resolvedCoverArtUrl =
                    song.album_image ||
                    song.cover_art_url ||
                    song.coverArtUrl ||
                    (contractMatch ? (contractMatch.album_image || contractMatch.cover_art_url || contractMatch.coverArtUrl || '') : '');

                const cardEl = addSongToListenInvestSection({
                    songId: resolvedSongId,
                    title: song.songTitle || song.title || 'Unknown Title',
                    artist: Array.isArray(song.artistNames) ? song.artistNames.join(', ') : (song.artistNames || song.artist || 'Unknown Artist'),
                    spotifyId: songSpotifyId || (contractMatch ? (contractMatch.spotify_id || contractMatch.spotifyId || '') : ''),
                    spotifyUrl: song.spotify_url || song.spotifyUrl || song.external_url || song.open_url || '',
                    songFileCid: resolvedSongFileCid,
                    audioUrl: resolvedAudioUrl,
                    audioFallbackUrl: resolvedAudioFallbackUrl,
                    coverArtUrl: resolvedCoverArtUrl,
                    previewUrl: song.preview_url || song.previewUrl || (contractMatch ? (contractMatch.preview_url || contractMatch.previewUrl || '') : ''),
                    downloadPrice: song.download_price ?? song.downloadPrice ?? (contractMatch ? contractMatch.downloadPrice : undefined) ?? '0.00001',
                    investmentPrice: song.investment_price ?? song.investmentPrice ?? (contractMatch ? contractMatch.investmentPrice : undefined) ?? '0.00001',
                    fullRightsPrice: song.full_rights_price ?? song.fullRightsPrice ?? (contractMatch ? contractMatch.fullRightsPrice : undefined) ?? '0.00001',
                    investmentEnabled: song.investment_enabled ?? song.investmentEnabled ?? (contractMatch ? contractMatch.investmentEnabled : undefined) ?? true,
                    fullRightsEnabled: song.full_rights_enabled ?? song.fullRightsEnabled ?? (contractMatch ? contractMatch.fullRightsEnabled : undefined) ?? true,
                    txHash: song.blockchain_tx_hash || song.tx_hash || '',
                    ipfsCid: song.ipfs_cid || song.ipfsCid || ''
                }, { skipImmediateQueueRebuild: true });

                if (cardEl?.id) {
                    activeCardIds.add(cardEl.id);
                }
            });

            const existingCards = Array.from(fanListContainer.querySelectorAll('.card'));
            existingCards.forEach((card) => {
                if (!activeCardIds.has(card.id)) {
                    card.remove();
                }
            });

            primePlayableTracks();
        } else {
            fanListContainer.innerHTML = '<p>No approved/initialised tracks were returned by the server.</p>';
            rebuildPlatformApprovedSongsPlayerList();
        }
    } catch (error) {
        console.error('Error loading approved/initialised tracks:', error);
        fanListContainer.innerHTML = '<p>Failed to load approved/initialised tracks.</p>';
        rebuildPlatformApprovedSongsPlayerList();
    }
}

async function loadArtists() {
    const libraryContainer = document.getElementById("library");
    if (!libraryContainer) return;

    try {
        const items = await ensureLibraryCacheLoaded();
        renderArtistGroupedLibrary(items, 'library');
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

if (document.getElementById('initialisedSongsList')) {
    ensurePlatformPlayerControlsBound();
    loadLibrary();
    if (!window.__fanListAutoRefreshTimer) {
        window.__fanListAutoRefreshTimer = setInterval(() => {
            if (!document.hidden) {
                loadLibrary();
            }
        }, 30000);
    }
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

