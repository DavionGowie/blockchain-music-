// Wallet/session check and auto-connect logic
// Include this script in all main HTML pages (Profile, Analysis, Chatroom, Index, etc.)


(function() {
    // Only enforce admin session on admin pages
    const adminPages = [
        'admin_panel.html',
        'admin_login.html',
        '/admin_panel.html',
        '/admin_login.html'
    ];
    const currentPage = window.location.pathname.split('/').pop();

    async function checkAdminSession() {
        try {
            const res = await fetch('/admin/validate', { credentials: 'include' });
            const data = await res.json();
            if (!res.ok || !data.username) {
                window.location.href = 'admin_login.html';
            }
        } catch (e) {
            window.location.href = 'admin_login.html';
        }
    }

    async function checkWallet() {
        if (typeof window.ethereum !== 'undefined') {
            let accounts = [];
            try {
                accounts = await window.ethereum.request({ method: 'eth_accounts' });
            } catch (e) {}
            if (accounts.length) {
                window.userWallet = accounts[0];
            }
        } else {
            window.userWallet = undefined;
            const walletAddressEl = document.getElementById('walletAddress');
            if (walletAddressEl) {
                walletAddressEl.textContent = 'Wallet not connected';
            }
        }
    }

    // Only check admin session on admin pages
    if (adminPages.includes(currentPage)) {
        checkAdminSession();
    }
    // Always check wallet for all pages
    checkWallet();
})();
