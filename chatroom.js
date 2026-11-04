// Simple in-memory chatroom
const chatMessages = document.getElementById('chatMessages');
const chatForm = document.getElementById('chatForm');
const usernameInput = document.getElementById('username');
const messageInput = document.getElementById('messageInput');
const userRoleSelect = document.getElementById('userRole');
const chatError = document.getElementById('chatError');

// Pre-fill username and role from the account the user registered/logged in with
function seedChatroomUser() {
    const storedUsername = localStorage.getItem('user_username') || localStorage.getItem('user_name') || '';
    const storedAccountType = (localStorage.getItem('user_account_type') || 'user').toLowerCase();

    if (storedUsername) {
        // Auto-fill username field
        const usernameField = document.getElementById('username');
        if (usernameField) {
            usernameField.value = storedUsername;
            usernameField.readOnly = true;
            usernameField.style.background = '#f0f4ff';
            usernameField.style.cursor = 'not-allowed';
            usernameField.title = 'Logged in as ' + storedUsername + '. Visit Login page to switch account.';
        }

        // Auto-fill role selector
        const roleSelector = document.getElementById('userRole');
        if (roleSelector) {
            roleSelector.value = storedAccountType === 'artist' ? 'Artist' : 'Fan';
        }

        // Show banner
        const banner = document.getElementById('chatUserBanner');
        if (banner) {
            const displayName = document.getElementById('chatDisplayName');
            const roleBadge = document.getElementById('chatRoleBadge');
            if (displayName) displayName.textContent = storedUsername;
            if (roleBadge) roleBadge.textContent = storedAccountType === 'artist' ? 'Artist' : 'Fan';
            banner.style.display = 'block';
        }
    }
}

// Call immediately
seedChatroomUser();
// Call after DOM fully loads
setTimeout(seedChatroomUser, 100);
// Also call on window load
window.addEventListener('load', seedChatroomUser);

let messages = [];

function escapeHtml(str) {
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function renderMessages() {
    chatMessages.innerHTML = '';
    messages.forEach(msg => {
        const div = document.createElement('div');
        div.className = 'message';
        const roleLabel = msg.role ? ` <span style="font-size:0.78em;background:#0b3c5d;color:#fff;padding:1px 7px;border-radius:8px;margin-left:4px;">${escapeHtml(msg.role)}</span>` : '';
        div.innerHTML = `<span class="user">${escapeHtml(msg.user)}${roleLabel}</span><span class="time" style="color:#888;font-size:0.85em;margin-left:8px;">${escapeHtml(msg.time)}</span>: <span>${escapeHtml(msg.text)}</span>`;
        chatMessages.appendChild(div);
    });
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

chatForm.addEventListener('submit', function(e) {
    e.preventDefault();
    const user = usernameInput.value.trim();
    const text = messageInput.value.trim();
    const role = userRoleSelect ? userRoleSelect.value : '';

    if (!user) {
        if (chatError) chatError.textContent = 'Please enter your name before sending a message.';
        usernameInput.focus();
        return;
    }
    if (!text) {
        if (chatError) chatError.textContent = 'Message cannot be empty.';
        messageInput.focus();
        return;
    }
    if (chatError) chatError.textContent = '';

    const now = new Date();
    const time = now.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
    messages.push({ user, text, time, role });
    renderMessages();
    messageInput.value = '';
    messageInput.focus();
});

renderMessages();
