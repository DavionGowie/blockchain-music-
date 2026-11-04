require("@nomicfoundation/hardhat-toolbox");

// Hardhat configuration for Avalanche mainnet and Fuji testnet
// Add your private key(s) to the accounts array for deployment (never commit private keys)
require("dotenv").config({ path: "spotify.env" });

const rawPrivateKey =
  process.env.DEPLOYER_PRIVATE_KEY || process.env.OWNER_PRIVATE_KEY || "";

const normalizedPrivateKey = rawPrivateKey
  ? rawPrivateKey.startsWith("0x")
    ? rawPrivateKey
    : `0x${rawPrivateKey}`
  : "";

module.exports = {
  solidity: "0.8.20",
  networks: {
    avalanche: {
      url: "https://api.avax.network/ext/bc/C/rpc",
      accounts: normalizedPrivateKey ? [normalizedPrivateKey] : []
    },
    fuji: {
      url: "https://api.avax-test.network/ext/bc/C/rpc",
      accounts: normalizedPrivateKey ? [normalizedPrivateKey] : []
    }
  }
};