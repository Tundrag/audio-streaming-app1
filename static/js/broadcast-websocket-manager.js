// broadcast-websocket-manager.js
// Client-side WebSocket manager for broadcast system

class BroadcastWebSocketManager {
    constructor() {
        this.config = {
            reconnectInterval: 5000,
            maxReconnectAttempts: 10,
            heartbeatInterval: 30000
        };
        
        this.state = {
            websocket: null,
            isConnected: false,
            reconnectAttempts: 0,
            reconnectTimer: null,
            heartbeatTimer: null,
            userId: null,
            isInitialized: false
        };
        
        this.callbacks = {
            onConnect: [],
            onDisconnect: [],
            onBroadcast: [],
            onBroadcastClear: [],
            onError: []
        };
    }
    
    init(userId) {
        if (this.state.isInitialized) {
            // console.log('BroadcastWebSocketManager already initialized');
            return;
        }
        
        this.state.userId = userId;
        this.state.isInitialized = true;
        
        // console.log('Initializing BroadcastWebSocketManager for user:', userId);
        this.connect();
        
        // Handle page visibility changes
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                this.stopHeartbeat();
            } else {
                if (this.state.isConnected) {
                    this.startHeartbeat();
                } else {
                    this.connect();
                }
            }
        });
        
        // Handle page unload
        window.addEventListener('beforeunload', () => {
            this.disconnect();
        });
    }
    
    connect() {
        if (this.state.websocket && this.state.websocket.readyState === WebSocket.OPEN) {
            // console.log('WebSocket already connected');
            return;
        }
        
        if (!this.state.userId) {
            // console.error('User ID not set for broadcast WebSocket');
            return;
        }
        
        try {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/api/creator/broadcast/ws?user_id=${this.state.userId}`;
            
            // console.log('Connecting to broadcast WebSocket:', wsUrl);
            this.state.websocket = new WebSocket(wsUrl);
            
            this.state.websocket.onopen = (event) => {
                // console.log('Broadcast WebSocket connected');
                this.state.isConnected = true;
                this.state.reconnectAttempts = 0;
                
                // Clear reconnect timer
                if (this.state.reconnectTimer) {
                    clearTimeout(this.state.reconnectTimer);
                    this.state.reconnectTimer = null;
                }
                
                // Start heartbeat
                this.startHeartbeat();
                
                // Trigger connect callbacks
                this.callbacks.onConnect.forEach(callback => {
                    try {
                        callback();
                    } catch (error) {
                        // console.error('Error in onConnect callback:', error);
                    }
                });
                
                // Request active broadcast
                this.send({
                    type: 'get_active_broadcast'
                });
            };
            
            this.state.websocket.onmessage = (event) => {
                try {
                    // Handle heartbeat messages
                    if (event.data === 'ping' || event.data === 'pong') {
                        // console.log('Heartbeat:', event.data);
                        return;
                    }
                    
                    // Handle JSON messages
                    const data = JSON.parse(event.data);
                    this.handleMessage(data);
                } catch (error) {
                    // console.error('Error parsing broadcast WebSocket message:', error);
                }
            };
            
            this.state.websocket.onclose = (event) => {
                // console.log('Broadcast WebSocket disconnected:', event.code, event.reason);
                this.state.isConnected = false;
                this.stopHeartbeat();
                
                // Trigger disconnect callbacks
                this.callbacks.onDisconnect.forEach(callback => {
                    try {
                        callback();
                    } catch (error) {
                        // console.error('Error in onDisconnect callback:', error);
                    }
                });
                
                // Attempt to reconnect if not intentionally closed
                if (event.code !== 1000 && this.state.reconnectAttempts < this.config.maxReconnectAttempts) {
                    this.scheduleReconnect();
                }
            };
            
            this.state.websocket.onerror = (error) => {
                // console.error('Broadcast WebSocket error:', error);
                
                // Trigger error callbacks
                this.callbacks.onError.forEach(callback => {
                    try {
                        callback(error);
                    } catch (err) {
                        // console.error('Error in onError callback:', err);
                    }
                });
            };
            
        } catch (error) {
            // console.error('Error creating broadcast WebSocket:', error);
            this.scheduleReconnect();
        }
    }    
    disconnect() {
        // console.log('Disconnecting broadcast WebSocket');
        
        // Clear timers
        if (this.state.reconnectTimer) {
            clearTimeout(this.state.reconnectTimer);
            this.state.reconnectTimer = null;
        }
        
        this.stopHeartbeat();
        
        // Close WebSocket
        if (this.state.websocket) {
            this.state.websocket.close(1000, 'User disconnected');
            this.state.websocket = null;
        }
        
        this.state.isConnected = false;
    }
    
    scheduleReconnect() {
        if (this.state.reconnectTimer) {
            return; // Already scheduled
        }
        
        this.state.reconnectAttempts++;
        const delay = Math.min(
            this.config.reconnectInterval * Math.pow(2, this.state.reconnectAttempts - 1),
            30000 // Max 30 seconds
        );
        
        // console.log(`Scheduling broadcast WebSocket reconnect in ${delay}ms (attempt ${this.state.reconnectAttempts})`);
        
        this.state.reconnectTimer = setTimeout(() => {
            this.state.reconnectTimer = null;
            this.connect();
        }, delay);
    }
    
    startHeartbeat() {
        this.stopHeartbeat();
        
        this.state.heartbeatTimer = setInterval(() => {
            if (this.state.isConnected) {
                this.send('ping');
            }
        }, this.config.heartbeatInterval);
    }
    
    stopHeartbeat() {
        if (this.state.heartbeatTimer) {
            clearInterval(this.state.heartbeatTimer);
            this.state.heartbeatTimer = null;
        }
    }
    
    send(data) {
        if (!this.state.websocket || this.state.websocket.readyState !== WebSocket.OPEN) {
            // console.warn('Cannot send message: WebSocket not connected');
            return false;
        }
        
        try {
            if (typeof data === 'string') {
                this.state.websocket.send(data);
            } else {
                this.state.websocket.send(JSON.stringify(data));
            }
            return true;
        } catch (error) {
            // console.error('Error sending WebSocket message:', error);
            return false;
        }
    }
    
    handleMessage(data) {
        // console.log('Broadcast WebSocket message:', data);
        
        switch (data.type) {
            case 'connected':
                // console.log('Broadcast WebSocket connection confirmed');
                break;
                
            case 'new_broadcast':
                this.handleNewBroadcast(data.broadcast);
                break;
                
            case 'broadcast_cleared':
                this.handleBroadcastCleared();
                break;
                
            case 'active_broadcast':
                if (data.broadcast) {
                    this.handleNewBroadcast(data.broadcast);
                }
                break;
                
            case 'stats_update':
                // Handle stats if needed
                break;
                
            default:
                // console.log('Unknown broadcast message type:', data.type);
        }
    }
    
    handleNewBroadcast(broadcast) {
        // console.log('Received new broadcast:', broadcast);
        
        // Show broadcast banner
        this.showBroadcastBanner(broadcast);
        
        // Trigger broadcast callbacks
        this.callbacks.onBroadcast.forEach(callback => {
            try {
                callback(broadcast);
            } catch (error) {
                // console.error('Error in onBroadcast callback:', error);
            }
        });
    }
    
    handleBroadcastCleared() {
        // console.log('Broadcast cleared');
        
        // Hide broadcast banner
        this.hideBroadcastBanner();
        
        // Trigger clear callbacks
        this.callbacks.onBroadcastClear.forEach(callback => {
            try {
                callback();
            } catch (error) {
                // console.error('Error in onBroadcastClear callback:', error);
            }
        });
    }
    
    showBroadcastBanner(broadcast) {
        // Check if user has already acknowledged this broadcast
        const dismissKey = `broadcast_${broadcast.id}_dismissed`;
        if (localStorage.getItem(dismissKey) === 'true') {
            return;
        }
        
        // Remove any existing banner
        this.hideBroadcastBanner();
        
        // Create banner element
        const banner = document.createElement('div');
        banner.id = 'broadcastBanner';
        banner.className = 'announcement-banner';
        banner.dataset.broadcastId = broadcast.id;
        
        // Set banner style based on message type
        const styles = {
            warning: { bg: '#fef3c7', color: '#92400e', border: '#f59e0b', icon: 'fa-exclamation-triangle' },
            alert: { bg: '#fee2e2', color: '#b91c1c', border: '#ef4444', icon: 'fa-exclamation-circle' },
            default: { bg: '#fef08a', color: '#92400e', border: '#facc15', icon: 'fa-bullhorn' }
        };
        
        const style = styles[broadcast.message_type] || styles.default;
        Object.assign(banner.style, { 
            backgroundColor: style.bg, 
            color: style.color, 
            borderBottomColor: style.border,
            display: 'flex'
        });
        
        banner.innerHTML = `
            <div class="announcement-banner-message">
                <i class="fas ${style.icon}"></i>
                ${broadcast.message}
            </div>
            <button id="broadcastDismissBtn" class="announcement-dismiss-btn" style="background-color: ${style.border};">
                Acknowledge
            </button>
        `;
        
        // Insert banner at the top of the page
        document.body.insertBefore(banner, document.body.firstChild);
        
        // Add dismiss handler
        const dismissBtn = banner.querySelector('#broadcastDismissBtn');
        dismissBtn.addEventListener('click', () => {
            this.acknowledgeBroadcast(broadcast.id);
        });
        
        // Adjust header spacing
        this.adjustHeaderForBanners();
    }
    
    hideBroadcastBanner() {
        const existingBanner = document.getElementById('broadcastBanner');
        if (existingBanner) {
            existingBanner.remove();
            this.adjustHeaderForBanners();
        }
    }
    
    acknowledgeBroadcast(broadcastId) {
        // Hide the banner
        this.hideBroadcastBanner();
        
        // Store acknowledgment locally
        const dismissKey = `broadcast_${broadcastId}_dismissed`;
        localStorage.setItem(dismissKey, 'true');
        
        // Send acknowledgment to server
        this.send({
            type: 'acknowledge_broadcast',
            broadcast_id: broadcastId
        });
        
        // console.log('Acknowledged broadcast:', broadcastId);
    }
    
    adjustHeaderForBanners() {
        const header = document.querySelector('.main-header');
        const container = document.querySelector('.container');
        const banners = document.querySelectorAll('.announcement-banner');
        
        if (!header || !container) return;
        
        let totalBannerHeight = 0;
        banners.forEach(banner => {
            const style = window.getComputedStyle(banner);
            if (style.display === 'flex') {
                totalBannerHeight += banner.offsetHeight;
            }
        });
        
        header.style.top = totalBannerHeight + 'px';
        
        const headerHeight = 60;
        container.style.paddingTop = (totalBannerHeight + headerHeight) + 'px';
    }
    
    // Event listener methods
    onConnect(callback) {
        this.callbacks.onConnect.push(callback);
    }
    
    onDisconnect(callback) {
        this.callbacks.onDisconnect.push(callback);
    }
    
    onBroadcast(callback) {
        this.callbacks.onBroadcast.push(callback);
    }
    
    onBroadcastClear(callback) {
        this.callbacks.onBroadcastClear.push(callback);
    }
    
    onError(callback) {
        this.callbacks.onError.push(callback);
    }
    
    // Utility methods
    isConnected() {
        return this.state.isConnected;
    }
    
    getConnectionState() {
        return {
            isConnected: this.state.isConnected,
            reconnectAttempts: this.state.reconnectAttempts,
            userId: this.state.userId
        };
    }
}

// Create global instance
window.BroadcastWebSocketManager = new BroadcastWebSocketManager();

// Auto-initialize if user ID is available
document.addEventListener('DOMContentLoaded', () => {
    // Try to get user ID from global window object
    const userId = window.currentUserId;
    if (userId) {
        // console.log('Auto-initializing BroadcastWebSocketManager for user:', userId);
        window.BroadcastWebSocketManager.init(userId);
        
        // Add connection status indicator if needed
        const wsIndicator = document.getElementById('ws-connection-indicator');
        if (wsIndicator) {
            window.BroadcastWebSocketManager.onConnect(() => {
                wsIndicator.style.backgroundColor = '#22c55e';
                wsIndicator.title = 'Connected to live updates';
            });
            
            window.BroadcastWebSocketManager.onDisconnect(() => {
                wsIndicator.style.backgroundColor = '#ef4444';
                wsIndicator.title = 'Disconnected from live updates';
            });
        }
    } else {
        // console.warn('User ID not found - broadcast WebSocket not initialized');
    }
});