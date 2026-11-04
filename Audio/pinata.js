const formData = new FormData();
formData.append("file", file);

await fetch("https://api.pinata.cloud/pinning/pinFileToIPFS", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${PINATA_JWT}`
  },
  body: formData
});

await window.ethereum.request({
  method: "eth_requestAccounts"
});