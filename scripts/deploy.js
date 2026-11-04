const { ethers } = require("hardhat");

async function main() {
  const SmartMusic = await ethers.getContractFactory("SmartMusic");
  const smartMusic = await SmartMusic.deploy();

  await smartMusic.waitForDeployment();

  console.log("SmartMusic deployed to:", await smartMusic.getAddress());

  // Add some sample songs from Spotify
  const songs = [
    {
      title: "Blinding Lights",
      artist: "The Weeknd",
      spotifyId: "0VjIjW4GlUZAMYd2vXMi3b",
      downloadPrice: ethers.parseEther("0.00001"),
      investmentPrice: ethers.parseEther("0.00001"),
      fullRightsPrice: ethers.parseEther("0.00001"),
      investmentEnabled: true,
      fullRightsEnabled: true
    },
    {
      title: "Watermelon Sugar",
      artist: "Harry Styles",
      spotifyId: "6UelLqGlWMcVH1E5cEiUk",
      downloadPrice: ethers.parseEther("0.00001"),
      investmentPrice: ethers.parseEther("0.00001"),
      fullRightsPrice: ethers.parseEther("0.00001"),
      investmentEnabled: true,
      fullRightsEnabled: true
    }
  ];

  for (const song of songs) {
    const tx = await smartMusic.addSongFromSpotify(
      song.title,
      song.artist,
      song.spotifyId,
      song.downloadPrice,
      song.investmentPrice,
      song.fullRightsPrice,
      song.investmentEnabled,
      song.fullRightsEnabled
    );
    await tx.wait();
    console.log(`Added song: ${song.title}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});