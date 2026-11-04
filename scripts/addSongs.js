const { ethers } = require("hardhat");

async function setupInvestmentTokens() {
  const contractAddress = "0x22F92058B7B531BDC7326f8d89980D742C6a1Df0";
  const SmartMusic = await ethers.getContractAt("SmartMusic", contractAddress);

  // Create investment tokens for songs
  const tokenNames = ["BLND", "WMS"]; // Blinding Lights, Watermelon Sugar
  const tokenSymbols = ["BLND", "WMS"];

  for (let i = 1; i <= 2; i++) {
    const tx = await SmartMusic.createInvestmentToken(i, tokenNames[i-1], tokenSymbols[i-1]);
    await tx.wait();
    console.log(`Created investment token for song ${i}`);
  }
}

setupInvestmentTokens().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});