await window.ethereum.request({
  method: "wallet_addEthereumChain",
  params: [{
    chainId: "0xa869",
    chainName: "Avalanche Fuji Testnet",
    rpcUrls: ["https://api.avax-test.network/ext/bc/C/rpc"],
    nativeCurrency: {
      name: "AVAX",
      symbol: "AVAX",
      decimals: 18
    },
    blockExplorerUrls: ["https://testnet.snowtrace.io"]
  }]
});