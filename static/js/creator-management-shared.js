// creator-management-shared.js - Universal Controller (SSR + SPA)
// Single source of truth for Creator Management page

// ============================================================================
// GLOBAL UTILITIES
// ============================================================================

/**
 * Global notification function
 */
function showNotification(message, isError = false) {
    const notification = document.getElementById('notification');
    if (!notification) return;
    notification.classList.remove('show');
    void notification.offsetWidth;
    notification.textContent = message;
    notification.className = `notification${isError ? ' error' : ''} show`;
    setTimeout(() => notification.classList.remove('show'), 3000);
}

// ============================================================================
// MAIN CONTROLLER
// ============================================================================

export class CreatorManagementController {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.userId = null;
        this.data = null;
        
        // Manager instances
        this.broadcastManager = null;
        this.pinManager = null;
        this.discordManager = null;
        this.kofiManager = null;
        this.guestTrialManager = null;
        this.patreonManager = null;
        
        console.log(`🎛️ Creator Management Controller initialized in ${mode.toUpperCase()} mode`);
    }

    /**
     * Returns required stylesheets for this page
     */
    getRequiredStyles() {
        return ['/static/css/creator-management.css'];
    }

    /**
     * SSR Mode: Returns empty string (HTML already rendered by server)
     * SPA Mode: Redirects to SSR (page is too complex for dynamic rendering)
     */
    async render() {
        if (this.mode === 'ssr') {
            console.log('🎛️ SSR Mode: Skipping render, using server-generated HTML');
            return '';
        }

        // SPA: Redirect to SSR for this complex page
        console.log('🎛️ SPA Mode: Redirecting to SSR for complex page');
        window.location.href = '/api/creator/pin/manage';
        return '';
    }

    /**
     * Mount/Hydrate - Works the same for both SSR and SPA modes
     */
    async mount() {
        console.log(`🎛️ Mounting Creator Management (${this.mode} mode)`);
        
        // Load bootstrapped data in SSR mode
        if (this.mode === 'ssr') {
            this.loadBootstrappedData();
        }

        // Initialize all managers
        this.initializeManagers();
        
        // Setup password visibility toggles
        this.setupPasswordToggles();
        
        console.log('✅ Creator Management mounted successfully');
    }

    /**
     * Load bootstrapped data from SSR
     */
    loadBootstrappedData() {
        const bootstrapScript = document.getElementById('creator-management-bootstrap-data');
        if (bootstrapScript) {
            try {
                this.data = JSON.parse(bootstrapScript.textContent);
                this.userId = this.data.user_id;
                console.log('📦 Loaded bootstrapped data:', this.data);
            } catch (error) {
                console.error('Error parsing bootstrapped data:', error);
            }
        }
    }

    /**
     * Initialize all section managers
     */
    initializeManagers() {
        // Get user ID from data or DOM
        if (!this.userId && this.data) {
            this.userId = this.data.user_id;
        }
        
        // Initialize all managers
        this.broadcastManager = new EnhancedBroadcastManager(this.userId);
        this.pinManager = new PINManager();
        this.discordManager = new DiscordManager();
        this.kofiManager = new KofiManager();
        this.guestTrialManager = new GuestTrialManager();
        this.patreonManager = new PatreonManager();
        
        console.log('✅ All managers initialized');
    }

    /**
     * Setup password visibility toggles
     */
    setupPasswordToggles() {
        document.querySelectorAll('.toggle-visibility').forEach(toggle => {
            toggle.addEventListener('click', () => {
                const inputId = toggle.getAttribute('data-for');
                const input = document.getElementById(inputId);
                if (input) {
                    input.type = (input.type === 'password') ? 'text' : 'password';
                    toggle.classList.toggle('fa-eye');
                    toggle.classList.toggle('fa-eye-slash');
                }
            });
        });
    }

    /**
     * Cleanup - called when navigating away
     */
    async destroy() {
        console.log('🎛️ Destroying Creator Management controller');
        
        if (this.broadcastManager) this.broadcastManager.destroy();
        if (this.pinManager) this.pinManager.destroy();
        if (this.discordManager) this.discordManager.destroy();
        if (this.kofiManager) this.kofiManager.destroy();
        if (this.guestTrialManager) this.guestTrialManager.destroy();
        if (this.patreonManager) this.patreonManager.destroy();
        
        this.data = null;
    }
}

// ============================================================================
// BROADCAST MANAGER
// ============================================================================

class EnhancedBroadcastManager {
    constructor(userId) {
        this.userId = userId;
        this.config = {
            maxCharacters: 280,
            warningThreshold: 200,
            dangerThreshold: 260,
            wsReconnectInterval: 5000
        };
        
        this.state = {
            selectedType: 'info',
            isConnected: false,
            connectedUsers: 0,
            lastBroadcastId: null,
            websocket: null,
            reconnectTimer: null
        };
        
        this.cacheElements();
        if (this.elements.form) {
            this.init();
        }
    }
    
    cacheElements() {
        this.elements = {
            form: document.getElementById('broadcast-form'),
            textarea: document.getElementById('broadcast_message'),
            charCounter: document.getElementById('charCounter'),
            charCount: document.getElementById('charCount'),
            charLimit: document.getElementById('charLimit'),
            typeOptions: document.querySelectorAll('.type-option'),
            sendBtn: document.getElementById('sendBroadcastBtn'),
            clearBtn: document.getElementById('clearBroadcastBtn'),
            previewBtn: document.getElementById('previewBtn'),
            preview: document.getElementById('broadcast-preview'),
            previewText: document.getElementById('preview-text'),
            status: document.getElementById('broadcastStatus'),
            statusTitle: document.getElementById('statusTitle'),
            statusMessage: document.getElementById('statusMessage'),
            wsConnectionDot: document.getElementById('wsConnectionDot'),
            wsConnectionStatus: document.getElementById('wsConnectionStatus'),
            connectedUsersSpan: document.getElementById('connectedUsers')
        };
    }
    
    init() {
        this.setupEventListeners();
        this.updateCharacterCounter();
        this.updateCharacterLimit();
        this.initWebSocket();
        
        // Load stats periodically
        setInterval(() => this.loadConnectionStats(), 30000);
    }
    
    initWebSocket() {
        if (!this.userId) return;
        
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/creator/broadcast/ws?user_id=${this.userId}`;
        
        try {
            this.state.websocket = new WebSocket(wsUrl);
            
            this.state.websocket.onopen = () => {
                console.log('🔌 Broadcast WebSocket connected');
                this.updateConnectionStatus(true);
                if (this.state.reconnectTimer) {
                    clearTimeout(this.state.reconnectTimer);
                    this.state.reconnectTimer = null;
                }
            };
            
            this.state.websocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (error) {
                    console.error('Error parsing WebSocket message:', error);
                }
            };
            
            this.state.websocket.onclose = () => {
                console.log('🔌 Broadcast WebSocket disconnected');
                this.updateConnectionStatus(false);
                this.scheduleReconnect();
            };
            
            this.state.websocket.onerror = (error) => {
                console.error('🔌 Broadcast WebSocket error:', error);
                this.updateConnectionStatus(false);
            };
        } catch (error) {
            console.error('Error initializing WebSocket:', error);
            this.updateConnectionStatus(false);
            this.scheduleReconnect();
        }
    }
    
    scheduleReconnect() {
        if (this.state.reconnectTimer) return;
        this.state.reconnectTimer = setTimeout(() => {
            console.log('🔌 Reconnecting WebSocket...');
            this.initWebSocket();
        }, this.config.wsReconnectInterval);
    }
    
    handleWebSocketMessage(data) {
        switch (data.type) {
            case 'connected':
                console.log('🔌 WebSocket connected');
                break;
            case 'stats_update':
                if (data.stats && typeof data.stats.connected_users === 'number') {
                    this.state.connectedUsers = data.stats.connected_users;
                    if (this.elements.connectedUsersSpan) {
                        this.elements.connectedUsersSpan.textContent = this.state.connectedUsers;
                    }
                }
                break;
            case 'broadcast_sent':
                this.showStatus('success', 'Broadcast Sent', 
                    `Delivered to ${data.sent_to_users || 0} users`);
                break;
            case 'broadcast_cleared':
                this.showStatus('success', 'Broadcast Cleared', 'Active broadcast cleared');
                break;
        }
    }
    
    setupEventListeners() {
        if (this.elements.textarea) {
            this.elements.textarea.addEventListener('input', () => {
                this.updateCharacterCounter();
                this.updatePreview();
            });
        }
        
        this.elements.typeOptions.forEach(option => {
            option.addEventListener('click', () => this.selectType(option.dataset.type));
        });
        
        if (this.elements.form) {
            this.elements.form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.sendBroadcast();
            });
        }
        
        if (this.elements.clearBtn) {
            this.elements.clearBtn.addEventListener('click', () => this.clearBroadcast());
        }
        
        if (this.elements.previewBtn) {
            this.elements.previewBtn.addEventListener('click', () => this.togglePreview());
        }
    }
    
    updateCharacterCounter() {
        if (!this.elements.textarea || !this.elements.charCount) return;
        
        const length = this.elements.textarea.value.length;
        const limit = this.config.maxCharacters;
        
        this.elements.charCount.textContent = length;
        this.elements.charCounter.classList.remove('safe', 'warning', 'danger');
        
        if (length >= this.config.dangerThreshold) {
            this.elements.charCounter.classList.add('danger');
        } else if (length >= this.config.warningThreshold) {
            this.elements.charCounter.classList.add('warning');
        } else {
            this.elements.charCounter.classList.add('safe');
        }
        
        if (this.elements.sendBtn) {
            this.elements.sendBtn.disabled = length > limit || length === 0;
        }
    }
    
    updateCharacterLimit() {
        if (this.elements.charLimit) {
            this.elements.charLimit.textContent = this.config.maxCharacters;
        }
        if (this.elements.textarea) {
            this.elements.textarea.setAttribute('maxlength', this.config.maxCharacters);
        }
    }
    
    selectType(type) {
        this.state.selectedType = type;
        this.elements.typeOptions.forEach(option => {
            option.classList.toggle('selected', option.dataset.type === type);
        });
        this.updatePreview();
    }
    
    updatePreview() {
        if (!this.elements.textarea || !this.elements.previewText) return;
        const message = this.elements.textarea.value.trim();
        if (message) {
            this.elements.previewText.innerHTML = `
                <div style="display: flex; align-items: center; gap: 0.5rem;">
                    <i class="fas ${this.getTypeIcon(this.state.selectedType)}"></i>
                    <span>${message}</span>
                </div>
            `;
        }
    }
    
    togglePreview() {
        if (!this.elements.preview || !this.elements.previewBtn) return;
        const isVisible = this.elements.preview.style.display !== 'none';
        this.elements.preview.style.display = isVisible ? 'none' : 'block';
        if (!isVisible) this.updatePreview();
        this.elements.previewBtn.innerHTML = isVisible ? 
            '<i class="fas fa-eye"></i> Preview' : 
            '<i class="fas fa-eye-slash"></i> Hide Preview';
    }
    
    getTypeIcon(type) {
        const icons = { info: 'fa-info-circle', warning: 'fa-exclamation-triangle', alert: 'fa-exclamation-circle' };
        return icons[type] || icons.info;
    }
    
    async sendBroadcast() {
        const message = this.elements.textarea.value.trim();
        if (!message || message.length > this.config.maxCharacters) return;
        
        this.elements.sendBtn.disabled = true;
        this.elements.sendBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending...';
        this.hideStatus();
        
        try {
            const response = await fetch('/api/creator/broadcast', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message, type: this.state.selectedType })
            });
            
            const data = await response.json();
            
            if (data.status === 'success') {
                this.showStatus('success', 'Broadcast Sent Successfully', 
                    `Sent to ${data.sent_to_users || 0} users`);
                this.elements.textarea.value = '';
                this.updateCharacterCounter();
                if (this.elements.preview) this.elements.preview.style.display = 'none';
            } else {
                this.showStatus('error', 'Broadcast Failed', data.message || 'Failed to send');
            }
        } catch (error) {
            console.error('Error sending broadcast:', error);
            this.showStatus('error', 'Network Error', 'Failed to send broadcast');
        } finally {
            this.elements.sendBtn.disabled = false;
            this.elements.sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Send Broadcast';
        }
    }
    
    async clearBroadcast() {
        if (!confirm('Clear the active broadcast?')) return;
        
        this.elements.clearBtn.disabled = true;
        this.elements.clearBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Clearing...';
        
        try {
            const response = await fetch('/api/creator/broadcast/clear', { method: 'POST' });
            const data = await response.json();
            
            if (data.status === 'success') {
                this.showStatus('success', 'Broadcast Cleared', data.message || 'Cleared');
            } else {
                this.showStatus('error', 'Clear Failed', data.message || 'Failed');
            }
        } catch (error) {
            console.error('Error clearing broadcast:', error);
            this.showStatus('error', 'Network Error', 'Failed to clear');
        } finally {
            this.elements.clearBtn.disabled = false;
            this.elements.clearBtn.innerHTML = '<i class="fas fa-trash"></i> Clear Active';
        }
    }
    
    showStatus(type, title, message) {
        if (!this.elements.status) return;
        this.elements.status.className = `broadcast-status ${type}`;
        if (this.elements.statusTitle) this.elements.statusTitle.textContent = title;
        if (this.elements.statusMessage) this.elements.statusMessage.textContent = message;
        this.elements.status.style.display = 'block';
        if (type === 'success') setTimeout(() => this.hideStatus(), 5000);
    }
    
    hideStatus() {
        if (this.elements.status) this.elements.status.style.display = 'none';
    }
    
    updateConnectionStatus(connected) {
        this.state.isConnected = connected;
        if (this.elements.wsConnectionDot) {
            this.elements.wsConnectionDot.classList.toggle('disconnected', !connected);
        }
        if (this.elements.wsConnectionStatus) {
            this.elements.wsConnectionStatus.textContent = connected ? 
                'WebSocket Connected' : 'WebSocket Disconnected';
        }
    }
    
    async loadConnectionStats() {
        try {
            const response = await fetch('/api/creator/broadcast/stats');
            const data = await response.json();
            if (data.status === 'success' && data.stats && this.elements.connectedUsersSpan) {
                this.elements.connectedUsersSpan.textContent = data.stats.connected_users || 0;
            }
        } catch (error) {
            console.error('Error loading stats:', error);
        }
    }
    
    destroy() {
        if (this.state.websocket) {
            this.state.websocket.close();
            this.state.websocket = null;
        }
        if (this.state.reconnectTimer) {
            clearTimeout(this.state.reconnectTimer);
            this.state.reconnectTimer = null;
        }
    }
}

// ============================================================================
// PIN MANAGER
// ============================================================================

class PINManager {
    constructor() {
        this.cacheElements();
        if (this.elements.generateBtn) {
            this.init();
        }
    }
    
    cacheElements() {
        this.elements = {
            generateBtn: document.getElementById('generatePin'),
            scheduleBtn: document.getElementById('scheduleRotation'),
            copyBtn: document.getElementById('copyPin'),
            currentPin: document.getElementById('currentPin'),
            pinHistory: document.getElementById('pinHistory'),
            rotationSchedule: document.getElementById('rotationSchedule'),
            nextRotation: document.getElementById('nextRotation')
        };
    }
    
    init() {
        this.setupEventListeners();
        this.loadPinHistory();
    }
    
    setupEventListeners() {
        if (this.elements.generateBtn) {
            this.elements.generateBtn.addEventListener('click', () => this.generatePin());
        }
        if (this.elements.scheduleBtn) {
            this.elements.scheduleBtn.addEventListener('click', () => this.scheduleRotation());
        }
        if (this.elements.copyBtn) {
            this.elements.copyBtn.addEventListener('click', () => this.copyPin());
        }
    }
    
    async loadPinHistory() {
        if (!this.elements.pinHistory) return;
        try {
            const response = await fetch('/api/creator/pin/history');
            const data = await response.json();
            if (data.status === 'success' && data.history) {
                this.elements.pinHistory.innerHTML = data.history.map(item => `
                    <div class="history-item">
                        <span>${item.description}</span>
                        <span class="history-date">${new Date(item.date).toLocaleDateString()}</span>
                    </div>
                `).join('');
            } else {
                this.elements.pinHistory.innerHTML = '<div class="history-item">No history available</div>';
            }
        } catch (error) {
            console.error('Error loading PIN history:', error);
            this.elements.pinHistory.innerHTML = '<div class="history-item">Failed to load history</div>';
        }
    }
    
    async generatePin() {
        try {
            const response = await fetch('/api/creator/pin/update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await response.json();
            if (data.status === 'success') {
                if (this.elements.currentPin) {
                    this.elements.currentPin.textContent = data.new_pin;
                }
                showNotification('PIN updated successfully');
                this.loadPinHistory();
            } else {
                showNotification(data.message || 'Error updating PIN', true);
            }
        } catch (error) {
            showNotification('Error updating PIN', true);
        }
    }
    
    async scheduleRotation() {
        try {
            const response = await fetch('/api/creator/pin/schedule-rotation', { method: 'POST' });
            const data = await response.json();
            if (data.status === 'success') {
                if (this.elements.rotationSchedule) {
                    this.elements.rotationSchedule.style.display = 'block';
                }
                if (this.elements.nextRotation) {
                    this.elements.nextRotation.textContent = `Next rotation: ${data.message}`;
                }
                showNotification('PIN rotation scheduled');
            } else {
                showNotification(data.message || 'Error scheduling rotation', true);
            }
        } catch (error) {
            showNotification('Error scheduling rotation', true);
        }
    }
    
    copyPin() {
        if (this.elements.currentPin) {
            const pin = this.elements.currentPin.textContent.trim();
            navigator.clipboard.writeText(pin)
                .then(() => showNotification('PIN copied to clipboard'))
                .catch(() => showNotification('Failed to copy PIN', true));
        }
    }
    
    destroy() {
        // Cleanup if needed
    }
}

// ============================================================================
// DISCORD MANAGER  
// ============================================================================

class DiscordManager {
    constructor() {
        this.cacheElements();
        if (this.elements.form) {
            this.init();
        }
    }
    
    cacheElements() {
        this.elements = {
            form: document.getElementById('discord-settings-form'),
            syncBtn: document.getElementById('syncDiscordButton'),
            cleanupBtn: document.getElementById('cleanupDiscordButton'),
            webhookInput: document.getElementById('discord_webhook_url'),
            botTokenInput: document.getElementById('discord_bot_token'),
            baseUrlInput: document.getElementById('discord_base_url'),
            syncStatus: document.getElementById('discordSyncStatus'),
            syncResult: document.getElementById('discordSyncResult'),
            syncMessage: document.getElementById('discordSyncMessage')
        };
    }
    
    init() {
        this.setupEventListeners();
        this.loadSettings();
    }
    
    setupEventListeners() {
        if (this.elements.form) {
            this.elements.form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.saveSettings();
            });
        }
        if (this.elements.syncBtn) {
            this.elements.syncBtn.addEventListener('click', () => this.syncAlbums());
        }
        if (this.elements.cleanupBtn) {
            this.elements.cleanupBtn.addEventListener('click', () => this.cleanup());
        }
    }
    
    async loadSettings() {
        try {
            const response = await fetch('/api/creator/discord/settings');
            const data = await response.json();
            if (data) {
                if (data.webhook_url && this.elements.webhookInput) {
                    this.elements.webhookInput.value = data.webhook_url;
                }
                if (data.bot_token && this.elements.botTokenInput) {
                    this.elements.botTokenInput.value = data.bot_token;
                }
                if (data.base_url && this.elements.baseUrlInput) {
                    this.elements.baseUrlInput.value = data.base_url;
                }
            }
        } catch (error) {
            console.error('Error loading Discord settings:', error);
        }
    }
    
    async saveSettings() {
        const settings = {
            webhook_url: this.elements.webhookInput?.value.trim(),
            bot_token: this.elements.botTokenInput?.value.trim(),
            base_url: this.elements.baseUrlInput?.value.trim()
        };
        
        try {
            const response = await fetch('/api/creator/discord/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            const data = await response.json();
            if (data.status === 'success') {
                showNotification('Discord settings saved successfully');
            } else {
                showNotification('Failed to save Discord settings', true);
            }
        } catch (error) {
            console.error('Error saving Discord settings:', error);
            showNotification('Error saving Discord settings', true);
        }
    }
    
    async syncAlbums() {
        if (!this.elements.webhookInput?.value.trim()) {
            showNotification('Please enter Discord webhook URL first', true);
            return;
        }
        
        this.elements.syncBtn.disabled = true;
        if (this.elements.syncStatus) this.elements.syncStatus.style.display = 'block';
        if (this.elements.syncResult) this.elements.syncResult.style.display = 'none';
        
        try {
            const response = await fetch('/api/creator/discord/sync', { method: 'POST' });
            const data = await response.json();
            
            if (this.elements.syncStatus) this.elements.syncStatus.style.display = 'none';
            if (this.elements.syncResult) this.elements.syncResult.style.display = 'block';
            
            if (response.ok) {
                if (this.elements.syncMessage) {
                    this.elements.syncMessage.textContent = data.message || 'Albums synced successfully';
                }
                showNotification('Albums synced to Discord successfully');
            } else {
                if (this.elements.syncMessage) {
                    this.elements.syncMessage.textContent = data.message || 'Failed to sync albums';
                }
                showNotification('Failed to sync albums to Discord', true);
            }
        } catch (error) {
            console.error('Error syncing to Discord:', error);
            showNotification('Error syncing albums to Discord', true);
        } finally {
            this.elements.syncBtn.disabled = false;
        }
    }
    
    async cleanup() {
        if (!this.elements.webhookInput?.value.trim()) {
            showNotification('Please enter Discord webhook URL first', true);
            return;
        }
        
        if (!confirm('Delete all Discord messages? This cannot be undone.')) {
            return;
        }
        
        this.elements.cleanupBtn.disabled = true;
        if (this.elements.syncStatus) this.elements.syncStatus.style.display = 'block';
        if (this.elements.syncResult) this.elements.syncResult.style.display = 'none';
        
        try {
            const response = await fetch('/api/creator/discord/cleanup', { method: 'POST' });
            const data = await response.json();
            
            if (this.elements.syncStatus) this.elements.syncStatus.style.display = 'none';
            if (this.elements.syncResult) this.elements.syncResult.style.display = 'block';
            
            if (response.ok) {
                if (this.elements.syncMessage) {
                    this.elements.syncMessage.textContent = data.message || 'Discord messages deleted';
                }
                showNotification('Discord messages deleted successfully');
            } else {
                if (this.elements.syncMessage) {
                    this.elements.syncMessage.textContent = data.message || 'Failed to delete messages';
                }
                showNotification('Failed to delete Discord messages', true);
            }
        } catch (error) {
            console.error('Error deleting Discord messages:', error);
            showNotification('Error deleting Discord messages', true);
        } finally {
            this.elements.cleanupBtn.disabled = false;
        }
    }
    
    destroy() {
        // Cleanup if needed
    }
}

// ============================================================================
// KO-FI MANAGER
// ============================================================================

class KofiManager {
    constructor() {
        this.cacheElements();
        if (this.elements.form) {
            this.init();
        }
    }
    
    cacheElements() {
        this.elements = {
            form: document.getElementById('kofi-settings-form'),
            testBtn: document.getElementById('test-kofi-connection'),
            googleSheetInput: document.getElementById('google_sheet_url'),
            verificationTokenInput: document.getElementById('verification_token'),
            connectionResult: document.getElementById('kofiConnectionResult'),
            connectionMessage: document.getElementById('kofiConnectionMessage')
        };
    }
    
    init() {
        this.setupEventListeners();
        this.loadSettings();
    }
    
    setupEventListeners() {
        if (this.elements.form) {
            this.elements.form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.saveSettings();
            });
        }
        if (this.elements.testBtn) {
            this.elements.testBtn.addEventListener('click', () => this.testConnection());
        }
    }
    
    async loadSettings() {
        try {
            const response = await fetch('/api/kofi/settings');
            const data = await response.json();
            if (data && data.status === 'success' && data.settings) {
                if (this.elements.googleSheetInput) {
                    this.elements.googleSheetInput.value = data.settings.google_sheet_url || '';
                }
                if (this.elements.verificationTokenInput) {
                    this.elements.verificationTokenInput.value = data.settings.verification_token || '';
                }
            }
        } catch (error) {
            console.error('Error loading Ko-fi settings:', error);
        }
    }
    
    async saveSettings() {
        const settings = {
            google_sheet_url: this.elements.googleSheetInput?.value.trim(),
            verification_token: this.elements.verificationTokenInput?.value.trim()
        };
        
        if (!settings.google_sheet_url) {
            showNotification('Please enter Google Sheet API URL', true);
            return;
        }
        if (!settings.verification_token) {
            showNotification('Please enter verification token', true);
            return;
        }
        
        try {
            const response = await fetch('/api/kofi/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            const data = await response.json();
            if (data.status === 'success') {
                showNotification('Ko-fi settings saved successfully');
            } else {
                showNotification(data.message || 'Failed to save Ko-fi settings', true);
            }
        } catch (error) {
            console.error('Error saving Ko-fi settings:', error);
            showNotification('Error saving Ko-fi settings', true);
        }
    }
    
    async testConnection() {
        const googleSheetUrl = this.elements.googleSheetInput?.value.trim();
        if (!googleSheetUrl) {
            showNotification('Please enter Google Sheet API URL first', true);
            return;
        }
        
        this.elements.testBtn.disabled = true;
        this.elements.testBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Testing...';
        if (this.elements.connectionResult) {
            this.elements.connectionResult.style.display = 'none';
        }
        
        try {
            const response = await fetch('/api/kofi/test-connection', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ google_sheet_url: googleSheetUrl })
            });
            const data = await response.json();
            
            if (this.elements.connectionResult) {
                this.elements.connectionResult.style.display = 'block';
            }
            
            if (response.ok && data.status === 'success') {
                if (this.elements.connectionMessage) {
                    this.elements.connectionMessage.textContent = 
                        'Connection test successful! Google Sheet API responding correctly.';
                }
                showNotification('Ko-fi connection test successful');
            } else {
                if (this.elements.connectionMessage) {
                    this.elements.connectionMessage.textContent = data.message || 'Connection test failed';
                }
                showNotification('Ko-fi connection test failed', true);
            }
        } catch (error) {
            console.error('Error testing Ko-fi connection:', error);
            if (this.elements.connectionMessage) {
                this.elements.connectionMessage.textContent = 'Error testing connection';
            }
            showNotification('Error testing Ko-fi connection', true);
        } finally {
            this.elements.testBtn.disabled = false;
            this.elements.testBtn.innerHTML = '<i class="fas fa-link"></i> Test Connection';
        }
    }
    
    destroy() {
        // Cleanup if needed
    }
}

// ============================================================================
// GUEST TRIAL MANAGER
// ============================================================================

class GuestTrialManager {
    constructor() {
        this.cacheElements();
        if (this.elements.form) {
            this.init();
        }
    }
    
    cacheElements() {
        this.elements = {
            form: document.getElementById('guest-trial-settings-form'),
            enabledCheckbox: document.getElementById('guest_trial_enabled'),
            trialDurationInput: document.getElementById('trial_duration_hours'),
            guestTierAmountInput: document.getElementById('guest_tier_amount'),
            maxTrialsEmailInput: document.getElementById('max_trials_per_email'),
            maxTrialsDeviceInput: document.getElementById('max_trials_per_device'),
            maxTrialsIpInput: document.getElementById('max_trials_per_ip'),
            registrationLimitEnabled: document.getElementById('registration_limit_enabled'),
            enableDailyReset: document.getElementById('enable_daily_reset'),
            maxDailyRegistrations: document.getElementById('max_daily_registrations'),
            maxTotalRegistrations: document.getElementById('max_total_registrations'),
            registrationLimitsConfig: document.getElementById('registration-limits-config'),
            registrationStatus: document.getElementById('registration-status'),
            connectionResult: document.getElementById('guestTrialConnectionResult'),
            connectionMessage: document.getElementById('guestTrialConnectionMessage'),
            viewStatsBtn: document.getElementById('view-registration-stats'),
            testBtn: document.getElementById('test-guest-trial'),
            resetCountersBtn: document.getElementById('reset-counters'),
            statsModal: document.getElementById('registration-stats-modal'),
            resetModal: document.getElementById('reset-counters-modal'),
            refreshStatsBtn: document.getElementById('refresh-stats'),
            confirmResetBtn: document.getElementById('confirm-reset-counters')
        };
    }
    
    init() {
        this.setupEventListeners();
        this.loadSettings();
        
        // Auto-refresh status every 30 seconds
        setInterval(() => this.updateRegistrationStatus(), 30000);
    }
    
    setupEventListeners() {
        if (this.elements.form) {
            this.elements.form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.saveSettings();
            });
        }
        
        if (this.elements.registrationLimitEnabled) {
            this.elements.registrationLimitEnabled.addEventListener('change', () => 
                this.updateLimitsVisibility());
        }
        
        if (this.elements.viewStatsBtn) {
            this.elements.viewStatsBtn.addEventListener('click', () => this.viewStats());
        }
        
        if (this.elements.testBtn) {
            this.elements.testBtn.addEventListener('click', () => this.testGuestTrial());
        }
        
        if (this.elements.resetCountersBtn) {
            this.elements.resetCountersBtn.addEventListener('click', () => {
                if (this.elements.resetModal) {
                    this.elements.resetModal.style.display = 'flex';
                }
            });
        }
        
        if (this.elements.refreshStatsBtn) {
            this.elements.refreshStatsBtn.addEventListener('click', () => this.loadRegistrationStats());
        }
        
        if (this.elements.confirmResetBtn) {
            this.elements.confirmResetBtn.addEventListener('click', () => this.resetCounters());
        }
    }
    
    async loadSettings() {
        try {
            const response = await fetch('/api/guest-trial/admin/settings');
            const data = await response.json();
            
            if (data) {
                // Basic settings
                if (this.elements.enabledCheckbox) {
                    this.elements.enabledCheckbox.checked = data.is_enabled || false;
                }
                if (this.elements.trialDurationInput) {
                    this.elements.trialDurationInput.value = data.trial_duration_hours || 48;
                }
                if (this.elements.guestTierAmountInput) {
                    const amountInDollars = Math.round((data.guest_tier_amount_cents || 0) / 100);
                    this.elements.guestTierAmountInput.value = amountInDollars;
                }
                
                // Abuse prevention
                if (this.elements.maxTrialsEmailInput) {
                    this.elements.maxTrialsEmailInput.value = data.max_trials_per_email_per_week || 1;
                }
                if (this.elements.maxTrialsDeviceInput) {
                    this.elements.maxTrialsDeviceInput.value = data.max_trials_per_device_per_month || 1;
                }
                if (this.elements.maxTrialsIpInput) {
                    this.elements.maxTrialsIpInput.value = data.max_trials_per_ip_per_day || 3;
                }
                
                // Registration limits
                if (this.elements.registrationLimitEnabled) {
                    this.elements.registrationLimitEnabled.checked = data.registration_limit_enabled || false;
                }
                if (this.elements.enableDailyReset) {
                    this.elements.enableDailyReset.checked = data.enable_daily_reset !== false;
                }
                if (this.elements.maxDailyRegistrations) {
                    this.elements.maxDailyRegistrations.value = data.max_daily_registrations || 50;
                }
                if (this.elements.maxTotalRegistrations) {
                    this.elements.maxTotalRegistrations.value = data.max_total_registrations || 0;
                }
                
                this.updateLimitsVisibility();
                this.updateRegistrationStatus();
            }
        } catch (error) {
            console.error('Error loading guest trial settings:', error);
        }
    }
    
    updateLimitsVisibility() {
        if (this.elements.registrationLimitEnabled?.checked) {
            if (this.elements.registrationLimitsConfig) {
                this.elements.registrationLimitsConfig.style.display = 'block';
            }
            this.updateRegistrationStatus();
        } else {
            if (this.elements.registrationLimitsConfig) {
                this.elements.registrationLimitsConfig.style.display = 'none';
            }
            if (this.elements.registrationStatus) {
                this.elements.registrationStatus.style.display = 'none';
            }
        }
    }
    
    async updateRegistrationStatus() {
        if (!this.elements.registrationLimitEnabled?.checked || !this.elements.registrationStatus) {
            return;
        }
        
        try {
            const response = await fetch('/api/guest-trial/admin/registration-stats');
            const data = await response.json();
            
            if (data.status === 'success') {
                const stats = data.stats;
                this.elements.registrationStatus.style.display = 'block';
                
                const statusDetails = document.getElementById('registration-status-details');
                if (statusDetails) {
                    statusDetails.innerHTML = `
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 0.5rem;">
                            <div>
                                <strong>Daily:</strong> ${stats.current_daily_count} / ${stats.daily_limit > 0 ? stats.daily_limit : '∞'}
                                ${stats.daily_limit > 0 ? `<div class="progress-bar"><div class="progress-bar-fill ${this.getProgressBarClass(stats.current_daily_count, stats.daily_limit)}" style="width: ${Math.min(100, (stats.current_daily_count / stats.daily_limit) * 100)}%"></div></div>` : ''}
                            </div>
                            <div>
                                <strong>Total:</strong> ${stats.current_total_count} / ${stats.total_limit > 0 ? stats.total_limit : '∞'}
                                ${stats.total_limit > 0 ? `<div class="progress-bar"><div class="progress-bar-fill ${this.getProgressBarClass(stats.current_total_count, stats.total_limit)}" style="width: ${Math.min(100, (stats.current_total_count / stats.total_limit) * 100)}%"></div></div>` : ''}
                            </div>
                        </div>
                        ${stats.hours_until_reset > 0 ? `<div class="time-until-reset"><i class="fas fa-clock"></i> Reset in ${stats.hours_until_reset.toFixed(1)}h</div>` : ''}
                    `;
                }
            }
        } catch (error) {
            console.error('Error loading registration status:', error);
        }
    }
    
    async saveSettings() {
        const amountInCents = parseInt(this.elements.guestTierAmountInput?.value || 0) * 100;
        
        const settings = {
            is_enabled: this.elements.enabledCheckbox?.checked || false,
            trial_duration_hours: parseInt(this.elements.trialDurationInput?.value || 48),
            guest_tier_amount_cents: amountInCents,
            max_trials_per_email_per_week: parseInt(this.elements.maxTrialsEmailInput?.value || 1),
            max_trials_per_device_per_month: parseInt(this.elements.maxTrialsDeviceInput?.value || 1),
            max_trials_per_ip_per_day: parseInt(this.elements.maxTrialsIpInput?.value || 3),
            registration_limit_enabled: this.elements.registrationLimitEnabled?.checked || false,
            enable_daily_reset: this.elements.enableDailyReset?.checked !== false,
            max_daily_registrations: parseInt(this.elements.maxDailyRegistrations?.value || 0),
            max_total_registrations: parseInt(this.elements.maxTotalRegistrations?.value || 0)
        };
        
        try {
            const response = await fetch('/api/guest-trial/admin/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            const data = await response.json();
            
            if (data.status === 'success') {
                this.showResult('success', 'Guest trial settings saved successfully');
                this.updateRegistrationStatus();
            } else {
                this.showResult('error', data.message || 'Failed to save settings');
            }
        } catch (error) {
            console.error('Error saving guest trial settings:', error);
            this.showResult('error', 'Error saving guest trial settings');
        }
    }
    
    async testGuestTrial() {
        this.elements.testBtn.disabled = true;
        this.elements.testBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Testing...';
        
        try {
            const response = await fetch('/api/guest-trial/admin/test', { method: 'POST' });
            const data = await response.json();
            
            if (response.ok && data.status === 'success') {
                const amountDisplay = data.guest_tier_amount_cents === 0 ? 
                    'Guest trials disabled ($0)' : 
                    `Guest trial tier: $${Math.round(data.guest_tier_amount_cents / 100)}`;
                this.showResult('success', `Guest trial system working! ${amountDisplay}`);
            } else {
                this.showResult('error', data.message || 'Guest trial test failed');
            }
        } catch (error) {
            console.error('Error testing guest trial:', error);
            this.showResult('error', 'Error testing guest trial system');
        } finally {
            this.elements.testBtn.disabled = false;
            this.elements.testBtn.innerHTML = '<i class="fas fa-vial"></i> Test Guest Trial';
        }
    }
    
    async viewStats() {
        await this.loadRegistrationStats();
        if (this.elements.statsModal) {
            this.elements.statsModal.style.display = 'flex';
        }
    }
    
    async loadRegistrationStats() {
        try {
            const response = await fetch('/api/guest-trial/admin/registration-stats');
            const data = await response.json();
            
            if (data.status === 'success') {
                const stats = data.stats;
                const content = document.getElementById('registration-stats-content');
                if (content) {
                    content.innerHTML = `
                        <div class="registration-stats-grid">
                            <div class="stat-card ${this.getStatCardClass(stats.current_daily_count, stats.daily_limit)}">
                                <h4>Daily Registrations</h4>
                                <div class="stat-value">${stats.current_daily_count}</div>
                                <p class="stat-subtitle">${stats.daily_limit > 0 ? `of ${stats.daily_limit} limit` : 'unlimited'}</p>
                            </div>
                            <div class="stat-card ${this.getStatCardClass(stats.current_total_count, stats.total_limit)}">
                                <h4>Total Registrations</h4>
                                <div class="stat-value">${stats.current_total_count}</div>
                                <p class="stat-subtitle">${stats.total_limit > 0 ? `of ${stats.total_limit} limit` : 'unlimited'}</p>
                            </div>
                            <div class="stat-card">
                                <h4>Today's Activity</h4>
                                <div class="stat-value success">${stats.today_stats.successful}</div>
                                <p class="stat-subtitle">${stats.today_stats.failed} failed</p>
                            </div>
                            <div class="stat-card">
                                <h4>Weekly Total</h4>
                                <div class="stat-value">${stats.weekly_stats.successful}</div>
                                <p class="stat-subtitle">${stats.weekly_stats.active_days} active days</p>
                            </div>
                        </div>
                    `;
                }
            }
        } catch (error) {
            console.error('Error loading registration stats:', error);
        }
    }
    
    async resetCounters() {
        const resetDaily = document.getElementById('reset-daily-counter')?.checked;
        const resetTotal = document.getElementById('reset-total-counter')?.checked;
        
        if (!resetDaily && !resetTotal) {
            alert('Please select at least one counter to reset');
            return;
        }
        
        try {
            this.elements.confirmResetBtn.disabled = true;
            this.elements.confirmResetBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Resetting...';
            
            const response = await fetch('/api/guest-trial/admin/reset-counters', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reset_daily: resetDaily, reset_total: resetTotal })
            });
            const data = await response.json();
            
            if (data.status === 'success') {
                if (this.elements.resetModal) {
                    this.elements.resetModal.style.display = 'none';
                }
                this.showResult('success', 'Counters reset successfully');
                this.updateRegistrationStatus();
                if (this.elements.statsModal?.style.display === 'flex') {
                    this.loadRegistrationStats();
                }
            } else {
                this.showResult('error', data.message || 'Failed to reset counters');
            }
        } catch (error) {
            console.error('Error resetting counters:', error);
            this.showResult('error', 'Error resetting counters');
        } finally {
            this.elements.confirmResetBtn.disabled = false;
            this.elements.confirmResetBtn.innerHTML = '<i class="fas fa-redo"></i> Reset Selected Counters';
        }
    }
    
    showResult(type, message) {
        if (!this.elements.connectionResult) return;
        this.elements.connectionResult.style.display = 'block';
        if (this.elements.connectionMessage) {
            this.elements.connectionMessage.textContent = message;
        }
        setTimeout(() => {
            if (this.elements.connectionResult) {
                this.elements.connectionResult.style.display = 'none';
            }
        }, 5000);
    }
    
    getStatCardClass(current, limit) {
        if (limit === 0) return '';
        const percentage = (current / limit) * 100;
        if (percentage >= 90) return 'danger';
        if (percentage >= 75) return 'warning';
        return 'success';
    }
    
    getProgressBarClass(current, limit) {
        if (limit === 0) return '';
        const percentage = (current / limit) * 100;
        if (percentage >= 90) return 'danger';
        if (percentage >= 75) return 'warning';
        return '';
    }
    
    destroy() {
        // Cleanup if needed
    }
}

// ============================================================================
// PATREON MANAGER
// ============================================================================

class PatreonManager {
    constructor() {
        this.cacheElements();
        if (this.elements.form) {
            this.init();
        }
    }
    
    cacheElements() {
        this.elements = {
            form: document.getElementById('patreon-settings-form'),
            campaignSelector: document.getElementById('campaign_selector'),
            newCampaignInput: document.getElementById('new-campaign-input'),
            newCampaignName: document.getElementById('new_campaign_name'),
            deleteBtn: document.getElementById('delete-campaign'),
            syncBtn: document.getElementById('sync-campaigns'),
            refreshTokenBtn: document.getElementById('refresh-token'),
            fullRefreshBtn: document.getElementById('full-refresh-token'),
            testConnectionBtn: document.getElementById('test-connection'),
            tokenStatus: document.getElementById('token-status'),
            accessTokenInput: document.getElementById('access_token'),
            refreshTokenInput: document.getElementById('refresh_token'),
            campaignIdInput: document.getElementById('campaign_id'),
            webhookSecretInput: document.getElementById('webhook_secret'),
            clientIdInput: document.getElementById('client_id'),
            clientSecretInput: document.getElementById('client_secret')
        };
    }
    
    init() {
        this.setupEventListeners();
        this.loadCampaigns();
        this.loadSettings();
        this.updateTokenStatus();
    }
    
    setupEventListeners() {
        if (this.elements.campaignSelector) {
            this.elements.campaignSelector.addEventListener('change', async (e) => {
                const value = e.target.value;
                if (value === 'new') {
                    if (this.elements.newCampaignInput) {
                        this.elements.newCampaignInput.style.display = 'block';
                    }
                    if (this.elements.deleteBtn) {
                        this.elements.deleteBtn.style.display = 'none';
                    }
                    this.clearForm();
                } else {
                    if (this.elements.newCampaignInput) {
                        this.elements.newCampaignInput.style.display = 'none';
                    }
                    if (value) {
                        await this.loadCampaignSettings(value);
                    } else if (this.elements.deleteBtn) {
                        this.elements.deleteBtn.style.display = 'none';
                    }
                }
            });
        }
        
        if (this.elements.form) {
            this.elements.form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.saveSettings();
            });
        }
        
        if (this.elements.deleteBtn) {
            this.elements.deleteBtn.addEventListener('click', () => this.deleteCampaign());
        }
        
        if (this.elements.syncBtn) {
            this.elements.syncBtn.addEventListener('click', () => this.syncCampaigns());
        }
        
        if (this.elements.refreshTokenBtn) {
            this.elements.refreshTokenBtn.addEventListener('click', () => this.refreshToken(false));
        }
        
        if (this.elements.fullRefreshBtn) {
            this.elements.fullRefreshBtn.addEventListener('click', () => this.refreshToken(true));
        }
        
        if (this.elements.testConnectionBtn) {
            this.elements.testConnectionBtn.addEventListener('click', () => this.testConnection());
        }
    }
    
    async loadCampaigns() {
        try {
            const response = await fetch('/api/creator/patreon/campaigns');
            const data = await response.json();
            
            if (this.elements.campaignSelector) {
                this.elements.campaignSelector.innerHTML = `
                    <option value="">Select a campaign...</option>
                    <option value="new">➕ Create New Campaign</option>
                `;
                
                if (data.campaigns && data.campaigns.length > 0) {
                    const primaryCampaign = data.campaigns.find(c => c.is_primary);
                    data.campaigns.forEach(campaign => {
                        const option = document.createElement('option');
                        option.value = campaign.id;
                        option.textContent = campaign.name;
                        if (campaign.is_primary) option.selected = true;
                        this.elements.campaignSelector.appendChild(option);
                    });
                    
                    if (primaryCampaign) {
                        await this.loadCampaignSettings(primaryCampaign.id);
                    }
                }
            }
            
            if (this.elements.deleteBtn) {
                this.elements.deleteBtn.style.display = 'none';
            }
        } catch (error) {
            console.error('Error loading campaigns:', error);
            showNotification('Error loading campaigns', true);
        }
    }
    
    async loadSettings() {
        try {
            const response = await fetch('/api/creator/patreon/settings');
            const data = await response.json();
            if (data) {
                const fields = [
                    'access_token', 'refresh_token', 'campaign_id',
                    'webhook_secret', 'client_id', 'client_secret'
                ];
                fields.forEach(field => {
                    const input = this.elements[field + 'Input'];
                    if (input) input.value = data[field] || '';
                });
            }
        } catch (error) {
            console.error('Error loading Patreon settings:', error);
        }
    }
    
    async loadCampaignSettings(campaignId) {
        try {
            const response = await fetch(`/api/creator/patreon/settings/${campaignId}`);
            const data = await response.json();
            const fields = [
                'access_token', 'refresh_token', 'campaign_id',
                'webhook_secret', 'client_id', 'client_secret'
            ];
            fields.forEach(field => {
                const input = this.elements[field + 'Input'];
                if (input) input.value = data[field] || '';
            });
            if (this.elements.deleteBtn) {
                this.elements.deleteBtn.style.display = 'block';
            }
        } catch (error) {
            showNotification('Error loading campaign settings', true);
        }
    }
    
    clearForm() {
        const inputs = this.elements.form?.querySelectorAll('input');
        inputs?.forEach(input => (input.value = ''));
    }
    
    async saveSettings() {
        const isNewCampaign = this.elements.campaignSelector?.value === 'new';
        if (isNewCampaign && !this.elements.newCampaignName?.value.trim()) {
            showNotification('Please enter a campaign name', true);
            return;
        }
        
        const settings = {
            access_token: this.elements.accessTokenInput?.value.trim(),
            refresh_token: this.elements.refreshTokenInput?.value.trim(),
            campaign_id: this.elements.campaignIdInput?.value.trim(),
            webhook_secret: this.elements.webhookSecretInput?.value.trim(),
            client_id: this.elements.clientIdInput?.value.trim(),
            client_secret: this.elements.clientSecretInput?.value.trim()
        };
        
        if (isNewCampaign) {
            settings.isNewCampaign = true;
            settings.name = this.elements.newCampaignName.value.trim();
        } else {
            settings.campaign_db_id = this.elements.campaignSelector.value;
        }
        
        try {
            const response = await fetch('/api/creator/patreon/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            const data = await response.json();
            
            if (data.status === 'success') {
                showNotification(isNewCampaign ? 'Campaign created successfully' : 'Settings saved successfully');
                await this.loadCampaigns();
                if (isNewCampaign) {
                    this.elements.campaignSelector.value = data.campaign_id;
                    if (this.elements.newCampaignInput) {
                        this.elements.newCampaignInput.style.display = 'none';
                    }
                    await this.loadCampaignSettings(data.campaign_id);
                }
                await this.updateTokenStatus();
            } else {
                showNotification(data.message || 'Error saving settings', true);
            }
        } catch (error) {
            console.error('Error saving Patreon settings:', error);
            showNotification('Error saving settings', true);
        }
    }
    
    async deleteCampaign() {
        const dbCampaignId = this.elements.campaignSelector?.value;
        if (!dbCampaignId) return;
        
        if (confirm('Delete this campaign? This will remove all associated data.')) {
            try {
                const response = await fetch(`/api/creator/patreon/campaigns/${dbCampaignId}`, {
                    method: 'DELETE'
                });
                if (response.ok) {
                    const data = await response.json();
                    showNotification(data.message);
                    await this.loadCampaigns();
                    await this.loadSettings();
                } else {
                    const error = await response.json();
                    showNotification(error.detail || 'Error deleting campaign', true);
                }
            } catch (error) {
                console.error('Error deleting campaign:', error);
                showNotification('Error deleting campaign', true);
            }
        }
    }
    
    async syncCampaigns() {
        const selectedDbId = this.elements.campaignSelector?.value;
        if (!selectedDbId || selectedDbId === 'new') {
            showNotification('Please select an existing campaign first', true);
            return;
        }
        
        this.elements.syncBtn.disabled = true;
        this.elements.syncBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Syncing...';
        
        try {
            const response = await fetch('/api/creator/patreon/sync-campaigns', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ db_campaign_id: selectedDbId })
            });
            const data = await response.json();
            
            if (data.status === 'success') {
                showNotification(data.message || 'Campaign synced successfully');
                await this.loadCampaigns();
            } else {
                showNotification(data.message || 'Sync failed', true);
            }
        } catch (error) {
            console.error('Sync error:', error);
            showNotification('Error syncing campaigns', true);
        } finally {
            this.elements.syncBtn.disabled = false;
            this.elements.syncBtn.innerHTML = '<i class="fas fa-sync-alt"></i> Sync Campaigns';
        }
    }
    
    async refreshToken(fullRefresh) {
        const btn = fullRefresh ? this.elements.fullRefreshBtn : this.elements.refreshTokenBtn;
        if (!btn) return;
        
        try {
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Refreshing...';
            
            const response = await fetch('/api/creator/patreon/refresh-token', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ full_refresh: fullRefresh })
            });
            const data = await response.json();
            
            if (data.success) {
                showNotification(fullRefresh ? 'Full token refresh successful' : 'Access token refreshed');
                await this.loadSettings();
                await this.updateTokenStatus();
            } else if (data.error === 'invalid_grant') {
                showNotification('Invalid refresh token. Please update credentials', true);
            } else {
                showNotification(data.message || 'Error refreshing token', true);
            }
        } catch (error) {
            showNotification('Error refreshing token', true);
        } finally {
            btn.disabled = false;
            btn.innerHTML = fullRefresh ? 
                '<i class="fas fa-sync-alt"></i> Full Token Refresh' : 
                '<i class="fas fa-sync"></i> Refresh Access Token';
        }
    }
    
    async updateTokenStatus() {
        try {
            const response = await fetch('/api/creator/patreon/refresh-status');
            const data = await response.json();
            
            if (this.elements.tokenStatus) {
                let statusClass = data.status;
                let icon = data.has_valid_token ? 'fa-check-circle' : 'fa-exclamation-circle';
                let statusMessage = data.message;
                
                if (data.token_type === 'missing_refresh') {
                    statusMessage = 'Missing refresh token - update Patreon settings';
                } else if (data.token_type === 'needs_refresh') {
                    statusMessage = 'Access token needs refresh';
                }
                
                this.elements.tokenStatus.innerHTML = `
                    <div class="token-info ${statusClass}">
                        <div class="status-header">
                            <i class="fas ${icon}"></i>
                            <span>${statusMessage}</span>
                        </div>
                    </div>
                `;
            }
        } catch (error) {
            console.error('Error updating token status:', error);
        }
    }
    
    async testConnection() {
        try {
            this.elements.testConnectionBtn.disabled = true;
            this.elements.testConnectionBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Testing...';
            
            const response = await fetch('/api/creator/patreon/test-connection');
            const data = await response.json();
            
            const dialogContent = `
                <div class="connection-test-results">
                    <div class="test-header ${data.success ? 'success' : 'error'}">
                        <i class="fas ${data.success ? 'fa-check-circle' : 'fa-exclamation-circle'}"></i>
                        <h3>${data.message}</h3>
                    </div>
                    <div class="test-details">
                        <div class="detail-item">
                            <span class="detail-label">Campaign:</span>
                            <span class="detail-value ${data.details.campaign_status ? 'success' : 'error'}">
                                ${data.details.campaign_status ? 'Connected' : 'Not Connected'}
                            </span>
                        </div>
                        ${data.details.campaign_name ? `
                            <div class="detail-item">
                                <span class="detail-label">Name:</span>
                                <span class="detail-value">${data.details.campaign_name}</span>
                            </div>
                        ` : ''}
                        <div class="detail-item">
                            <span class="detail-label">Tiers:</span>
                            <span class="detail-value">${data.details.tier_count}</span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label">Patrons:</span>
                            <span class="detail-value">${data.details.patron_count}</span>
                        </div>
                    </div>
                </div>
            `;
            
            const modal = document.createElement('div');
            modal.className = 'connection-test-modal';
            modal.innerHTML = `
                <div class="modal-content">
                    ${dialogContent}
                    <div class="modal-actions">
                        <button class="pin-button pin-button-primary" onclick="this.closest('.connection-test-modal').remove()">
                            Close
                        </button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            
            showNotification(data.success ? 'Connection test completed' : 'Connection test failed', !data.success);
        } catch (error) {
            showNotification('Connection test failed', true);
        } finally {
            this.elements.testConnectionBtn.disabled = false;
            this.elements.testConnectionBtn.innerHTML = '<i class="fas fa-link"></i> Test Connection';
        }
    }
    
    destroy() {
        // Cleanup if needed
    }
}

// ============================================================================
// AUTO-INITIALIZE IN SSR MODE
// ============================================================================

if (document.readyState === 'loading') {
    console.log('🎛️ [DEBUG] Document still loading, waiting for DOMContentLoaded...');
    document.addEventListener('DOMContentLoaded', initSSRMode);
} else {
    console.log('🎛️ [DEBUG] Document already loaded, initializing immediately...');
    initSSRMode();
}

function initSSRMode() {
    console.log('🎛️ [DEBUG] initSSRMode() called');
    
    // Only auto-initialize if we're on the creator management page in SSR mode
    const bootstrapScript = document.getElementById('creator-management-bootstrap-data');
    
    console.log('🎛️ [DEBUG] Bootstrap script found:', !!bootstrapScript);
    
    if (bootstrapScript) {
        console.log('🎛️ [DEBUG] Bootstrap data:', bootstrapScript.textContent);
        console.log('🎛️ Auto-initializing Creator Management (SSR mode)');
        
        try {
            const controller = new CreatorManagementController('ssr');
            controller.mount();
            window.creatorManagementController = controller;
            console.log('✅ [DEBUG] Controller initialized and mounted successfully');
        } catch (error) {
            console.error('❌ [DEBUG] Error initializing controller:', error);
        }
    } else {
        console.log('ℹ️ [DEBUG] No bootstrap script found - not auto-initializing (this is normal for SPA mode)');
    }
}