/**
 * forum-websockets.js - WebSocket Management for Forum System
 * Handles: Global WebSocket, Thread WebSocket, Heartbeat, Connection Status
 */

class ForumWebSocketManager {
    constructor(forumCore) {
        this.forumCore = forumCore;
        
        // WebSocket connections
        this.globalWebsocket = null;
        this.threadWebsocket = null;
        
        // Connection state management
        this.connectionState = 'disconnected';
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000;
        
        // Heartbeat system
        this.heartbeatInterval = null;
        this.heartbeatTimeout = null;
        this.lastHeartbeat = null;
        
        // Connection tracking
        this.connectionId = null;
        this.connectionInProgress = false;
        this.manualDisconnect = false;
        this.connectionTimeout = null;
        
        console.log('🔌 ForumWebSocketManager initialized');
    }

    // ================== DEBUG TOOLS ==================

    setupDebugTools() {
        if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
            window.wsDebug = {
                status: () => {
                    console.log('🔍 WebSocket Debug Status:', {
                        connectionState: this.connectionState,
                        reconnectAttempts: this.reconnectAttempts,
                        hasGlobalWS: !!this.globalWebsocket,
                        wsReadyState: this.globalWebsocket?.readyState,
                        connectionId: this.connectionId,
                        lastHeartbeat: this.lastHeartbeat,
                        manualDisconnect: this.manualDisconnect
                    });
                    return this.connectionState;
                },
                forceReconnect: () => {
                    console.log('🔄 Forcing WebSocket reconnection...');
                    this.manualDisconnect = false;
                    this.disconnectGlobalWebSocket();
                    setTimeout(() => this.connectGlobalWebSocket(), 1000);
                },
                testConnection: () => {
                    if (this.globalWebsocket?.readyState === WebSocket.OPEN) {
                        console.log('🧪 Testing WebSocket connection...');
                        this.sendHeartbeat();
                    } else {
                        console.log('❌ WebSocket not connected');
                    }
                }
            };
            console.log('🛠️ Debug tools available: window.wsDebug.status(), .forceReconnect(), .testConnection()');
        }
    }

    // ================== GLOBAL WEBSOCKET MANAGEMENT ==================

    connectGlobalWebSocket() {
        if (this.connectionInProgress || 
            (this.globalWebsocket && this.globalWebsocket.readyState === WebSocket.CONNECTING)) {
            console.log('🌐 Connection already in progress, skipping');
            return;
        }

        if (this.globalWebsocket && this.globalWebsocket.readyState === WebSocket.OPEN) {
            console.log('🌐 Already connected to global WebSocket');
            return;
        }

        this.connectionInProgress = true;
        this.connectionState = 'connecting';
        this.connectionId = Date.now().toString(36) + Math.random().toString(36).substr(2);
        
        this.disconnectGlobalWebSocket();

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/forum/ws/global`;
    
        console.log(`🌐 Connecting to global forum WebSocket (${this.connectionId}):`, wsUrl);
        
        try {
            this.globalWebsocket = new WebSocket(wsUrl);
            
            this.connectionTimeout = setTimeout(() => {
                if (this.connectionState === 'connecting') {
                    console.log('⏰ WebSocket connection timeout');
                    this.handleConnectionFailure('timeout');
                }
            }, 10000);

            this.globalWebsocket.onopen = () => this.handleGlobalWebSocketOpen();
            this.globalWebsocket.onmessage = (event) => this.handleGlobalWebSocketMessage(JSON.parse(event.data));
            this.globalWebsocket.onclose = (event) => this.handleGlobalWebSocketClose(event);
            this.globalWebsocket.onerror = (error) => this.handleGlobalWebSocketError(error);

        } catch (error) {
            console.error('🚨 Failed to create WebSocket:', error);
            this.handleConnectionFailure('creation_error');
        }
    }

    handleGlobalWebSocketOpen() {
        console.log(`✅ Global forum WebSocket connected (${this.connectionId})`);
        this.connectionInProgress = false;
        this.connectionState = 'connected';
        this.reconnectAttempts = 0;
        this.reconnectDelay = 1000;
        this.manualDisconnect = false;
        
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }
        
        this.updateGlobalLiveIndicator(true);
        this.startHeartbeat();
    }

    handleGlobalWebSocketMessage(data) {
        console.log(`📨 Global WebSocket message (${this.connectionId}):`, data);
        
        const handlers = {
            'connected': () => {
                console.log('🤝 WebSocket connection confirmed:', data.message);
                if (data.session_id) {
                    console.log('🔑 Session confirmed:', data.session_id.substring(0, 8) + '...');
                }
            },
            'new_thread_created': () => this.forumCore.handleNewThreadCreated(data),
            'new_sub_thread_created': () => this.forumCore.handleNewSubThreadCreated(data),
            'thread_deleted': () => this.forumCore.handleThreadDeleted(data),
            'forum_notification': () => this.forumCore.handleForumNotification(data.notification),
            'forum_notification_count': () => this.forumCore.handleNotificationCountUpdate(data.count),
            'heartbeat': () => this.handleHeartbeat(),
            'pong': () => this.handlePong(),
            'test_broadcast': () => {
                console.log('🧪 Global test broadcast received:', data);
                this.forumCore.showToast(`Global test: ${data.message}`);
            }
        };
        
        if (handlers[data.type]) {
            handlers[data.type]();
        } else {
            console.log('🤷 Unknown global message type:', data.type, data);
        }
    }

    handleGlobalWebSocketClose(event) {
        console.log(`❌ Global forum WebSocket disconnected (${this.connectionId}):`, event.code, event.reason);
        this.connectionInProgress = false;
        this.connectionState = 'disconnected';
        this.stopHeartbeat();
        this.updateGlobalLiveIndicator(false);
        
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }

        if (!this.manualDisconnect && event.code !== 1008) {
            this.scheduleReconnect();
        }
    }

    handleGlobalWebSocketError(error) {
        console.error(`🚨 Global WebSocket error (${this.connectionId}):`, error);
        this.handleConnectionFailure('websocket_error');
    }

    handleConnectionFailure(reason) {
        console.log(`💥 Connection failure (${this.connectionId}): ${reason}`);
        this.connectionInProgress = false;
        this.connectionState = 'disconnected';
        this.stopHeartbeat();
        this.updateGlobalLiveIndicator(false);
        
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }

        if (!this.manualDisconnect) {
            this.scheduleReconnect();
        }
    }

    scheduleReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.log('🛑 Max reconnection attempts reached. Manual reconnection required.');
            this.connectionState = 'failed';
            this.updateGlobalLiveIndicator(false);
            return;
        }

        this.reconnectAttempts++;
        this.connectionState = 'reconnecting';
        
        console.log(`🔄 Scheduling reconnection attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts} in ${this.reconnectDelay}ms`);
        
        setTimeout(() => {
            if (!this.manualDisconnect && this.connectionState === 'reconnecting') {
                this.connectGlobalWebSocket();
            }
        }, this.reconnectDelay);
        
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
    }

    disconnectGlobalWebSocket() {
        this.manualDisconnect = true;
        this.stopHeartbeat();
        
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }

        if (this.globalWebsocket) {
            this.globalWebsocket.onopen = null;
            this.globalWebsocket.onmessage = null;
            this.globalWebsocket.onclose = null;
            this.globalWebsocket.onerror = null;
            
            if (this.globalWebsocket.readyState === WebSocket.OPEN || 
                this.globalWebsocket.readyState === WebSocket.CONNECTING) {
                this.globalWebsocket.close(1000, 'Manual disconnect');
            }
            this.globalWebsocket = null;
        }
        
        this.connectionState = 'disconnected';
        this.connectionInProgress = false;
        this.updateGlobalLiveIndicator(false);
    }

    // ================== THREAD WEBSOCKET MANAGEMENT ==================

    connectThreadWebSocket(threadId) {
        if (this.threadWebsocket) {
            this.threadWebsocket.close();
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/forum/ws/thread/${threadId}`;
    
        this.threadWebsocket = new WebSocket(wsUrl);
        this.threadWebsocket.onopen = () => this.updateLiveIndicator(true);
        this.threadWebsocket.onmessage = (event) => this.handleThreadWebSocketMessage(JSON.parse(event.data));
        this.threadWebsocket.onclose = () => this.updateLiveIndicator(false);
        this.threadWebsocket.onerror = () => this.updateLiveIndicator(false);
    }

    handleThreadWebSocketMessage(data) {
        // Delegate to ForumCore for thread-specific message handling
        if (this.forumCore.handleWebSocketMessage) {
            this.forumCore.handleWebSocketMessage(data);
        }
    }

    disconnectThreadWebSocket() {
        if (this.threadWebsocket) {
            this.threadWebsocket.close();
            this.threadWebsocket = null;
        }
    }

    // ================== HEARTBEAT SYSTEM ==================

    startHeartbeat() {
        this.stopHeartbeat();
        
        this.heartbeatInterval = setInterval(() => {
            this.sendHeartbeat();
        }, 25000);
        
        console.log('💓 Heartbeat system started');
    }

    stopHeartbeat() {
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
        }
        
        if (this.heartbeatTimeout) {
            clearTimeout(this.heartbeatTimeout);
            this.heartbeatTimeout = null;
        }
    }

    sendHeartbeat() {
        if (this.globalWebsocket?.readyState === WebSocket.OPEN) {
            try {
                this.globalWebsocket.send(JSON.stringify({ type: 'ping' }));
                this.lastHeartbeat = Date.now();
                
                this.heartbeatTimeout = setTimeout(() => {
                    console.log('💔 Heartbeat timeout - connection may be dead');
                    this.handleConnectionFailure('heartbeat_timeout');
                }, 5000);
                
            } catch (error) {
                console.error('💔 Failed to send heartbeat:', error);
                this.handleConnectionFailure('heartbeat_send_error');
            }
        }
    }

    handleHeartbeat() {
        if (this.globalWebsocket?.readyState === WebSocket.OPEN) {
            try {
                this.globalWebsocket.send(JSON.stringify({ type: 'heartbeat_ack' }));
            } catch (error) {
                console.error('💔 Failed to send heartbeat ack:', error);
            }
        }
    }

    handlePong() {
        if (this.heartbeatTimeout) {
            clearTimeout(this.heartbeatTimeout);
            this.heartbeatTimeout = null;
        }
        console.log('🏓 Received pong - connection alive');
    }

    // ================== CONNECTION STATUS INDICATORS ==================

    updateGlobalLiveIndicator(connected) {
        const indicator = document.getElementById('globalConnectionIndicator');
        if (!indicator) return;
        
        const statusText = indicator.querySelector('.status-text');
        const dot = indicator.querySelector('.live-dot');
        
        if (connected) {
            indicator.className = 'live-indicator connected';
            statusText.textContent = 'Live';
            dot.style.display = 'inline-block';
        } else {
            indicator.className = `live-indicator disconnected ${this.connectionState}`;
            if (this.connectionState === 'connecting') {
                statusText.textContent = 'Connecting...';
            } else if (this.connectionState === 'reconnecting') {
                statusText.textContent = `Reconnecting... (${this.reconnectAttempts}/${this.maxReconnectAttempts})`;
            } else if (this.connectionState === 'failed') {
                statusText.textContent = 'Connection Failed';
            } else {
                statusText.textContent = 'Disconnected';
            }
            dot.style.display = this.connectionState === 'connecting' ? 'inline-block' : 'none';
        }
    }

    updateLiveIndicator(connected) {
        document.querySelectorAll('.live-indicator:not(#globalConnectionIndicator)').forEach(indicator => {
            const statusText = indicator.querySelector('.status-text');
            const dot = indicator.querySelector('.live-dot');
            
            if (connected) {
                indicator.className = 'live-indicator connected';
                if (statusText) statusText.textContent = 'Live';
                if (dot) dot.style.display = 'inline-block';
            } else {
                indicator.className = 'live-indicator disconnected';
                if (statusText) statusText.textContent = 'Disconnected';
                if (dot) dot.style.display = 'none';
            }
        });
    }

    // ================== PUBLIC API METHODS ==================

    /**
     * Send typing indicator to thread
     */
    sendTypingIndicator(threadId, isTyping) {
        if (this.threadWebsocket?.readyState === WebSocket.OPEN) {
            try {
                this.threadWebsocket.send(JSON.stringify({
                    type: 'typing',
                    is_typing: isTyping
                }));
            } catch (error) {
                console.error('Error sending typing indicator:', error);
            }
        }
    }

    /**
     * Send ping to thread WebSocket
     */
    sendThreadPing() {
        if (this.threadWebsocket?.readyState === WebSocket.OPEN) {
            try {
                this.threadWebsocket.send(JSON.stringify({ type: 'ping' }));
            } catch (error) {
                console.error('Error sending thread ping:', error);
            }
        }
    }

    /**
     * Get connection status
     */
    getConnectionStatus() {
        return {
            global: {
                connected: this.globalWebsocket?.readyState === WebSocket.OPEN,
                state: this.connectionState,
                reconnectAttempts: this.reconnectAttempts
            },
            thread: {
                connected: this.threadWebsocket?.readyState === WebSocket.OPEN
            }
        };
    }

    /**
     * Force reconnection
     */
    forceReconnect() {
        this.manualDisconnect = false;
        this.disconnectGlobalWebSocket();
        setTimeout(() => this.connectGlobalWebSocket(), 1000);
    }

    // ================== CLEANUP ==================

    destroy() {
        console.log('🧹 Destroying ForumWebSocketManager');
        
        this.manualDisconnect = true;
        this.disconnectGlobalWebSocket();
        this.disconnectThreadWebSocket();
        
        if (window.wsDebug) {
            delete window.wsDebug;
        }
        
        console.log('🧹 ForumWebSocketManager cleaned up');
    }
}