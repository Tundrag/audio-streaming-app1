(() => {
    if (window.TTSStatusChannel) {
        return;
    }

    const queue = [];
    const subscriptions = new Map(); // key -> { trackId, voiceId, count }

    let socket = null;
    let reconnectAttempts = 0;
    const maxReconnectAttempts = 8;
    const baseReconnectDelay = 1000;
    let manualClose = false;

    const socketReady = () => socket && socket.readyState === WebSocket.OPEN;
    const socketConnecting = () => socket && socket.readyState === WebSocket.CONNECTING;

    const getKey = (trackId, voiceId) => `${trackId}:${voiceId}`;

    function dispatch(name, detail = null) {
        if (detail === null) {
            window.dispatchEvent(new Event(name));
        } else {
            window.dispatchEvent(new CustomEvent(name, { detail }));
        }
    }

    function flushQueue() {
        while (queue.length && socketReady()) {
            const payload = queue.shift();
            try {
                socket.send(JSON.stringify(payload));
            } catch (error) {
                console.error('TTSStatusChannel failed to send payload:', error);
            }
        }
    }

    function resendSubscriptions() {
        subscriptions.forEach(({ trackId, voiceId }) => {
            queue.push({ type: 'subscribe', track_id: trackId, voice_id: voiceId });
        });
        flushQueue();
    }

    function scheduleReconnect() {
        if (manualClose) {
            manualClose = false;
            return;
        }
        if (subscriptions.size === 0) {
            return; // Nothing to listen for, skip reconnect until someone subscribes
        }
        if (reconnectAttempts >= maxReconnectAttempts) {
            console.warn('TTSStatusChannel reached max reconnection attempts; waiting for manual connect.');
            return;
        }
        const delay = baseReconnectDelay * Math.pow(2, reconnectAttempts);
        reconnectAttempts += 1;
        setTimeout(() => {
            connect(true);
        }, delay);
    }

    function connect(fromReconnect = false) {
        if (socketReady() || socketConnecting()) {
            return;
        }
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/tts/ws`;

        try {
            socket = new WebSocket(wsUrl);

            socket.onopen = () => {
                reconnectAttempts = 0;
                dispatch('ttsWebSocketConnected');
                flushQueue();
                resendSubscriptions();
            };

            socket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    dispatch('ttsStatusUpdate', data);
                } catch (error) {
                    console.error('TTSStatusChannel failed to parse message:', error);
                }
            };

            socket.onerror = (error) => {
                console.error('TTSStatusChannel socket error:', error);
            };

            socket.onclose = () => {
                dispatch('ttsWebSocketDisconnected');
                socket = null;
                scheduleReconnect();
            };
        } catch (error) {
            console.error('TTSStatusChannel failed to establish websocket:', error);
            socket = null;
            if (!fromReconnect) {
                scheduleReconnect();
            }
        }
    }

    function ensureConnected() {
        if (!socketReady() && !socketConnecting()) {
            connect();
        }
    }

    function subscribe(trackId, voiceId) {
        if (!trackId || !voiceId) return;

        const key = getKey(trackId, voiceId);
        const entry = subscriptions.get(key);

        if (entry) {
            entry.count += 1;
        } else {
            subscriptions.set(key, { trackId, voiceId, count: 1 });
            queue.push({ type: 'subscribe', track_id: trackId, voice_id: voiceId });
        }

        ensureConnected();
        flushQueue();
    }

    function unsubscribe(trackId, voiceId) {
        if (!trackId || !voiceId) return;

        const key = getKey(trackId, voiceId);
        const entry = subscriptions.get(key);
        if (!entry) return;

        entry.count -= 1;
        if (entry.count <= 0) {
            subscriptions.delete(key);
            queue.push({ type: 'unsubscribe', track_id: trackId, voice_id: voiceId });
            flushQueue();
        }

        if (subscriptions.size === 0) {
            manualClose = true;
            if (socket) {
                socket.close();
                socket = null;
            }
        }
    }

    function isConnected() {
        return socketReady();
    }

    function hasActiveSubscriptions() {
        return subscriptions.size > 0;
    }

    function disconnect() {
        manualClose = true;
        reconnectAttempts = 0;
        queue.length = 0;
        subscriptions.clear();
        if (socket) {
            socket.close();
            socket = null;
        }
    }

    window.TTSStatusChannel = {
        connect,
        disconnect,
        subscribe,
        unsubscribe,
        isConnected,
        hasActiveSubscriptions
    };
})();
