async function loadArtistSongs(artist) {
    try {
        const response = await fetch(
            `http://127.0.0.1:5000/spotify/artist?artist=${encodeURIComponent(artist)}`
        );

        console.log("Response status:", response.status);
        const data = await response.json();
        console.log("Data:", data);

        const songsDiv = document.getElementById("songs");
        console.log("songsDiv:", songsDiv);

        if (!songsDiv) {
            console.error("Element with id='songs' not found");
            return;
        }

        if (!data.artists || !data.artists.items || data.artists.items.length === 0) {
            songsDiv.innerHTML = "<p>No artists found.</p>";
            return;
        }

        songsDiv.innerHTML = data.artists.items.map(artist => `
            <div class="song-card">
                <h3>${artist.name}</h3>
                <p>Popularity: ${artist.popularity}</p>
                <a href="${artist.external_urls.spotify}" target="_blank">
                    Open in Spotify
                </a>
            </div>
        `).join("");

    } catch (err) {
        console.error("FULL ERROR:", err);
    }
}

fetch('/spotify/init');

document.getElementById("connectSpotifyArtistBtn")
.addEventListener("click", () => {
    const artist = document.getElementById("spotifyArtistSearch").value;
    loadArtistSongs(artist);
});