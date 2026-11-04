// musicAction.js - Cleaned and deduplicated

// Assumes ethers.js is loaded and window.ethereum is available
let signer;

async function connectWallet() {
    if (window.ethereum) {
        const provider = new ethers.BrowserProvider(window.ethereum);
        signer = await provider.getSigner();
        return signer;
    } else {
        alert("MetaMask or Web3 wallet not found");
        return null;
    }
}

function playSong() {
    const audio = document.getElementById("audioPlayer");
    if (audio) {
        audio.play();
    } else {
        alert("Audio player not found");
    }
}

async function buySong(id) {
    if (!signer) {
        await connectWallet();
        if (!signer) {
            alert("Connect wallet first");
            return;
        }
    }

    try {
        const tx = await signer.sendTransaction({
            to: "0xYourContractAddressHere",
            value: ethers.parseEther("0.01")
        });
        console.log("TX:", tx.hash);
    } catch (error) {
        console.error("Transaction failed:", error);
        alert("Transaction failed: " + (error.message || error));
    }
}