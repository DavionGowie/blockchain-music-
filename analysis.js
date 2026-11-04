
// Analysis page functionality
document.addEventListener('DOMContentLoaded', function() {
    loadSalesAnalysis();
    loadStreamingAnalysis();
    loadBlockchainAnalysis();
});

async function loadSalesAnalysis() {
    try {
        const response = await fetch('http://127.0.0.1:5000/analysis/sales');
        const data = await response.json();

        // Update total revenue
        document.querySelector('#sales-analysis .card:nth-child(1) p').textContent =
            `💰 ${data.total_revenue} ${data.currency}`;

        // Update top selling song
        const topSongCard = document.querySelector('#sales-analysis .card:nth-child(2)');
        topSongCard.querySelector('p:nth-child(1)').textContent = `🎵 ${data.top_selling_song.title}`;
        topSongCard.querySelector('p:nth-child(2)').textContent = `Sales: ${data.top_selling_song.revenue} ${data.currency}`;

        // Update top album
        const topAlbumCard = document.querySelector('#sales-analysis .card:nth-child(3)');
        topAlbumCard.querySelector('p:nth-child(1)').textContent = `📀 ${data.top_album.title}`;
        topAlbumCard.querySelector('p:nth-child(2)').textContent = `Sales: ${data.top_album.revenue} ${data.currency}`;

        // Update recent transactions
        const transactionsList = document.querySelector('#sales-analysis .card:nth-child(4) ul');
        transactionsList.innerHTML = '';
        data.recent_transactions.forEach(tx => {
            const li = document.createElement('li');
            li.textContent = `${tx.item} - ${tx.amount} ${data.currency} (${tx.type})`;
            transactionsList.appendChild(li);
        });

    } catch (error) {
        console.error('Error loading sales analysis:', error);
    }
}

async function loadStreamingAnalysis() {
    try {
        const response = await fetch('http://127.0.0.1:5000/analysis/streaming');
        const data = await response.json();

        // Update artist cards
        const artistCards = document.querySelectorAll('#streaming-trends .card');
        data.artists.forEach((artist, index) => {
            if (artistCards[index]) {
                const card = artistCards[index];
                card.querySelector('h3').textContent = artist.name;
                card.querySelector('p:nth-child(2)').textContent = `🎧 Streams: ${artist.streams.toLocaleString()}`;

                const trendElement = card.querySelector('p:nth-child(3)');
                const trendSymbol = artist.trend >= 0 ? '🔥' : '📉';
                trendElement.textContent = `${trendSymbol} Trending: ${artist.trend >= 0 ? '+' : ''}${artist.trend}%`;
            }
        });

        // Update top performing track
        const topTrackCard = document.querySelector('#streaming-trends .card:last-child');
        if (topTrackCard) {
            topTrackCard.querySelector('p:nth-child(1)').textContent = `🎵 ${data.top_performing_track.title}`;
            topTrackCard.querySelector('p:nth-child(2)').textContent = `Streams: ${data.top_performing_track.streams.toLocaleString()}+`;
        }

    } catch (error) {
        console.error('Error loading streaming analysis:', error);
    }
}

async function loadBlockchainAnalysis() {
    try {
        const response = await fetch('http://127.0.0.1:5000/analysis/blockchain');
        const data = await response.json();

        // Create or update blockchain analysis section
        let blockchainSection = document.getElementById('blockchain-analysis');
        if (!blockchainSection) {
            blockchainSection = document.createElement('section');
            blockchainSection.id = 'blockchain-analysis';
            blockchainSection.innerHTML = `
                <h2>⛓️ Blockchain Analytics</h2>
                <div class="card">
                    <h3>Network Statistics</h3>
                    <p id="total-tx">Total Transactions: --</p>
                    <p id="unique-buyers">Unique Buyers: --</p>
                    <p id="avg-tx">Average Transaction: -- AVAX</p>
                </div>
                <div class="card">
                    <h3>Transaction Types</h3>
                    <div id="tx-types"></div>
                </div>
                <div class="card">
                    <h3>Network Benefits</h3>
                    <p id="gas-saved">Gas Savings vs AVAX: --</p>
                    <p>⚡ Fast Transactions</p>
                    <p>🔒 Secure & Transparent</p>
                </div>
            `;
            document.querySelector('footer').before(blockchainSection);
        }

        // Update blockchain stats
        document.getElementById('total-tx').textContent = `Total Transactions: ${data.total_transactions}`;
        document.getElementById('unique-buyers').textContent = `Unique Buyers: ${data.unique_buyers}`;
        document.getElementById('avg-tx').textContent = `Average Transaction: ${data.average_transaction_value} ${data.currency}`;
        document.getElementById('gas-saved').textContent = `Gas Savings vs AVAX: ${data.gas_saved_vs_ethereum}`;

        // Update transaction types with robust fallback and readable chips.
        const txTypesDiv = document.getElementById('tx-types');
        const transactionTypes = (data && data.transaction_types && typeof data.transaction_types === 'object')
            ? data.transaction_types
            : {};

        txTypesDiv.innerHTML = '';
        const entries = Object.entries(transactionTypes);

        if (entries.length === 0) {
            const empty = document.createElement('p');
            empty.className = 'tx-type-empty';
            empty.textContent = 'No transactions recorded yet.';
            txTypesDiv.appendChild(empty);
        } else {
            entries.forEach(([type, count]) => {
                const chip = document.createElement('span');
                chip.className = 'tx-type-chip';

                const safeType = String(type || 'unknown')
                    .replace(/_/g, ' ')
                    .replace(/\b\w/g, (c) => c.toUpperCase());
                chip.textContent = `${safeType}: ${Number(count) || 0}`;

                txTypesDiv.appendChild(chip);
            });
        }

    } catch (error) {
        console.error('Error loading blockchain analysis:', error);
    }
}

// Function to view artist details (called from buttons)
async function viewArtistDetails(artistName) {
    try {
        const response = await fetch(`http://127.0.0.1:5000/analysis/artist/${encodeURIComponent(artistName)}`);
        const data = await response.json();

        // Create modal or detailed view
        alert(`Artist: ${data.name}\nStreams: ${data.total_streams}\nTrend: ${data.monthly_trend}%\nTop Track: ${data.top_track}\nRevenue: ${data.revenue_breakdown.streaming} AVAX`);

    } catch (error) {
        console.error('Error loading artist details:', error);
    }
}


// Auto-refresh data every 30 seconds (avoid duplicate intervals)
if (!window.analysisAutoRefresh) {
    window.analysisAutoRefresh = setInterval(() => {
        loadSalesAnalysis();
        loadStreamingAnalysis();
        loadBlockchainAnalysis();
    }, 30000);
}