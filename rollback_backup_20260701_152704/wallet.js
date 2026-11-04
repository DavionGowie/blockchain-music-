
// Contract address - replace with deployed address
const CONTRACT_ADDRESS = "0x479Dc5252C08e70Ade655515eb6777Ed276552E6";

// ABI
const CONTRACT_ABI = [
    "function addSongFromSpotify(string memory _title, string memory _artist, string memory _spotifyId, uint _downloadPrice, uint _fullRightsPrice) public",
    "function createInvestmentToken(uint _songId, string memory _tokenName, string memory _tokenSymbol) public",
    "function buyDownloadRights(uint _songId) public payable",
    "function investInSong(uint _songId, uint _tokenAmount) public payable",
    "function buyFullRights(uint _songId) public payable",
    "function getSong(uint _songId) public view returns (uint, string memory, string memory, string memory, address, uint, uint, address, bool)",
    "function hasDownloadRights(address _user, uint _songId) public view returns (bool)"
];

async function buyDownloadRights(songId) {
    if (!window.userWallet) {
        alert("Please connect your wallet first");
        return;
    }

    try {
        const provider = new ethers.BrowserProvider(window.ethereum);
        const signer = await provider.getSigner();
        const contract = new ethers.Contract(CONTRACT_ADDRESS, CONTRACT_ABI, signer);

        // Get song price from contract
        const song = await contract.getSong(songId);
        const price = song[5]; // downloadPrice

        // Send transaction
        const tx = await contract.buyDownloadRights(songId, { value: price });
        await tx.wait();

        // Record purchase in backend
        await fetch("http://127.0.0.1:5000/buy", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
            song_id: songId,
            wallet: window.userWallet,
            tx_hash: tx.hash,
            type: "download"
            })
        });

        document.getElementById("status").innerText = `Download rights purchased for song ${songId}. Tx: ${tx.hash}`;
    } catch (error) {
        console.error("Error buying download rights:", error);
        document.getElementById("status").innerText = "Purchase failed: " + error.message;
    }
}

async function investInSong(songId, tokenAmount) {
    if (!window.userWallet) {
        alert("Please connect your wallet first");
        return;
    }

    try {
        const provider = new ethers.BrowserProvider(window.ethereum);
        const signer = await provider.getSigner();
        const contract = new ethers.Contract(CONTRACT_ADDRESS, CONTRACT_ABI, signer);

        // Calculate investment cost (1 AVAX per token)
        const cost = ethers.parseEther(tokenAmount.toString());

        // Send transaction
        const tx = await contract.investInSong(songId, tokenAmount, { value: cost });
        await tx.wait();

        // Record purchase in backend
        await fetch("http://127.0.0.1:5000/buy", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
            song_id: songId,
            wallet: window.userWallet,
            tx_hash: tx.hash,
            type: "invest",
            amount: tokenAmount
            })
        });

        document.getElementById("status").innerText = `Invested ${tokenAmount} tokens in song ${songId}. Tx: ${tx.hash}`;
    } catch (error) {
        console.error("Error investing:", error);
        document.getElementById("status").innerText = "Investment failed: " + error.message;
    }
}

async function buyFullRights(songId) {
    if (!window.userWallet) {
        alert("Please connect your wallet first");
        return;
    }

    try {
        const provider = new ethers.BrowserProvider(window.ethereum);
        const signer = await provider.getSigner();
        const contract = new ethers.Contract(CONTRACT_ADDRESS, CONTRACT_ABI, signer);

        // Get song price from contract
        const song = await contract.getSong(songId);
        const price = song[6]; // fullRightsPrice

        // Send transaction
        const tx = await contract.buyFullRights(songId, { value: price });
        await tx.wait();

        // Record purchase in backend
        await fetch("http://127.0.0.1:5000/buy", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
            song_id: songId,
            wallet: window.userWallet,
            tx_hash: tx.hash,
            type: "rights"
            })
        });

        document.getElementById("status").innerText = `Full rights purchased for song ${songId}. Tx: ${tx.hash}`;
    } catch (error) {
        console.error("Error buying rights:", error);
        document.getElementById("status").innerText = "Purchase failed: " + error.message;
    }
}
