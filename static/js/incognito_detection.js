// Enhanced Incognito Detection System - IMPROVED for better accuracy
// File: incognito_detector.js

class EnhancedIncognitoDetector {
    constructor() {
        this.detectionMethods = [];
        this.initialized = false;
        this.rateLimitStorage = 'incognito_rate_limit';
        this.cooldownPeriod = 10 * 60 * 1000; // 10 minutes
    }

    async init() {
        if (this.initialized) return;
        
        this.detectionMethods = [
            // ✅ IMPROVED: More aggressive storage quota detection
            { method: 'storage_quota_enhanced', test: this.testStorageQuotaEnhanced.bind(this), weight: 0.20 },
            // ✅ IMPROVED: Better IndexedDB behavior testing
            { method: 'indexeddb_enhanced', test: this.testIndexedDBEnhanced.bind(this), weight: 0.18 },
            // ✅ NEW: Multiple storage persistence tests
            { method: 'storage_persistence_multi', test: this.testStoragePersistenceMulti.bind(this), weight: 0.15 },
            // ✅ IMPROVED: Better localStorage quota behavior
            { method: 'localstorage_quota_enhanced', test: this.testLocalStorageQuotaEnhanced.bind(this), weight: 0.12 },
            // ✅ NEW: Cache API behavior
            { method: 'cache_api_behavior', test: this.testCacheAPIBehavior.bind(this), weight: 0.10 },
            // ✅ IMPROVED: Enhanced WebRTC detection
            { method: 'webrtc_enhanced', test: this.testWebRTCEnhanced.bind(this), weight: 0.08 },
            // ✅ NEW: Filesystem API detection
            { method: 'filesystem_api', test: this.testFileSystemAPI.bind(this), weight: 0.07 },
            // ✅ IMPROVED: Better service worker detection
            { method: 'service_worker_enhanced', test: this.testServiceWorkerEnhanced.bind(this), weight: 0.05 },
            // ✅ NEW: Browser fingerprint stability
            { method: 'fingerprint_stability', test: this.testFingerprintStability.bind(this), weight: 0.05 }
        ];
        
        this.initialized = true;
        console.log('🔍 Enhanced incognito detection system initialized (improved accuracy)');
    }

    // ✅ IMPROVED: More aggressive decision logic
    async quickIncognitoCheck() {
        if (!this.initialized) await this.init();
        
        const results = [];
        let totalWeight = 0;
        let detectedWeight = 0;
        let hitCount = 0;
        
        for (const methodConfig of this.detectionMethods) {
            try {
                const result = await methodConfig.test();
                const methodResult = {
                    method: methodConfig.method,
                    detected: result.detected,
                    confidence: result.confidence || 0,
                    weight: methodConfig.weight,
                    available: result.available !== false,
                    details: result.details
                };
                
                results.push(methodResult);
                
                if (methodResult.available) {
                    totalWeight += methodConfig.weight;
                    if (methodResult.detected) {
                        detectedWeight += methodConfig.weight * methodResult.confidence;
                        hitCount++;
                    }
                }
            } catch (error) {
                console.warn(`Detection method ${methodConfig.method} failed:`, error);
                results.push({
                    method: methodConfig.method,
                    detected: false,
                    confidence: 0,
                    weight: methodConfig.weight,
                    available: false,
                    error: error.message
                });
            }
        }
        
        const confidence = totalWeight > 0 ? Math.min(detectedWeight / totalWeight, 1) : 0;
        const detectionRatio = results.filter(r => r.available).length > 0 ? 
            hitCount / results.filter(r => r.available).length : 0;
        
        // ✅ IMPROVED: More aggressive decision logic for higher accuracy
        let action = 'allow';
        let blockReason = null;
        
        if (confidence >= 0.55 && hitCount >= 3) {
            action = 'block';
            blockReason = 'High confidence incognito detection with multiple indicators';
        } else if (confidence >= 0.45 && hitCount >= 4) {
            action = 'block';
            blockReason = 'Multiple detection methods indicate private browsing';
        } else if (confidence >= 0.40 && detectionRatio >= 0.60) {
            action = 'block';
            blockReason = 'Strong detection ratio indicates private browsing';
        } else if (confidence >= 0.35 && hitCount >= 2) {
            action = 'warn';
            blockReason = 'Moderate confidence private browsing detection';
        }
        
        console.log(`🔍 Incognito detection: ${Math.round(confidence * 100)}% confidence, ${hitCount} hits, action: ${action}`);
        
        return {
            detected: confidence > 0.25,
            confidence: confidence,
            hitCount: hitCount,
            detectionRatio: detectionRatio,
            action: action,
            blockReason: blockReason,
            methods: results,
            timestamp: Date.now()
        };
    }

    // ✅ IMPROVED: More aggressive storage quota detection
    async testStorageQuotaEnhanced() {
        try {
            if ('storage' in navigator && 'estimate' in navigator.storage) {
                const estimate = await navigator.storage.estimate();
                const quota = estimate.quota || 0;
                const usage = estimate.usage || 0;
                
                // ✅ IMPROVED: More aggressive thresholds
                // Normal browsers usually have GB+ quotas, incognito has much less
                const quotaMB = quota / (1024 * 1024);
                let detected = false;
                let confidence = 0;
                
                if (quotaMB < 50) { // Less than 50MB is very suspicious
                    detected = true;
                    confidence = 0.95;
                } else if (quotaMB < 150) { // Less than 150MB is suspicious
                    detected = true;
                    confidence = 0.80;
                } else if (quotaMB < 500) { // Less than 500MB is moderately suspicious
                    detected = true;
                    confidence = 0.60;
                }
                
                return {
                    detected: detected,
                    confidence: confidence,
                    details: { 
                        quota: quota, 
                        quotaMB: Math.round(quotaMB),
                        usage: usage,
                        usageMB: Math.round(usage / (1024 * 1024))
                    }
                };
            }
        } catch (error) {
            return { detected: true, confidence: 0.70, available: true, details: 'Storage API blocked' };
        }
        
        return { detected: false, confidence: 0, available: false };
    }

    // ✅ IMPROVED: Better IndexedDB testing
    async testIndexedDBEnhanced() {
        return new Promise((resolve) => {
            try {
                const testDB = 'incognito_test_' + Date.now();
                const request = indexedDB.open(testDB, 1);
                
                const timeout = setTimeout(() => {
                    resolve({ detected: true, confidence: 0.85, details: 'IndexedDB timeout (likely blocked)' });
                }, 2000);
                
                request.onerror = (event) => {
                    clearTimeout(timeout);
                    // ✅ IMPROVED: Check error types
                    const error = event.target.error;
                    if (error && error.name === 'UnknownError') {
                        resolve({ detected: true, confidence: 0.90, details: 'IndexedDB UnknownError (incognito signature)' });
                    } else {
                        resolve({ detected: true, confidence: 0.75, details: 'IndexedDB blocked: ' + (error?.name || 'unknown') });
                    }
                };
                
                request.onsuccess = () => {
                    clearTimeout(timeout);
                    try {
                        const db = request.result;
                        
                        // ✅ NEW: Test transaction behavior
                        const transaction = db.transaction(['test'], 'readwrite');
                        transaction.oncomplete = () => {
                            db.close();
                            indexedDB.deleteDatabase(testDB);
                            resolve({ detected: false, confidence: 0.15 });
                        };
                        
                        transaction.onerror = () => {
                            db.close();
                            resolve({ detected: true, confidence: 0.70, details: 'IndexedDB transaction failed' });
                        };
                        
                        // Try to create object store
                        const objectStore = transaction.objectStore('test');
                        
                    } catch (e) {
                        request.result.close();
                        resolve({ detected: true, confidence: 0.80, details: 'IndexedDB operation restricted: ' + e.message });
                    }
                };
                
                request.onupgradeneeded = (event) => {
                    try {
                        const db = event.target.result;
                        if (!db.objectStoreNames.contains('test')) {
                            db.createObjectStore('test');
                        }
                    } catch (e) {
                        clearTimeout(timeout);
                        resolve({ detected: true, confidence: 0.85, details: 'IndexedDB upgrade blocked' });
                    }
                };
                
            } catch (error) {
                resolve({ detected: true, confidence: 0.80, details: 'IndexedDB unavailable: ' + error.message });
            }
        });
    }

    // ✅ NEW: Multiple storage persistence tests
    async testStoragePersistenceMulti() {
        try {
            const testKey = 'incognito_multi_test_' + Date.now();
            let detectedMethods = 0;
            let totalMethods = 0;
            
            // Test 1: localStorage large data
            totalMethods++;
            try {
                const largeData = 'x'.repeat(2 * 1024 * 1024); // 2MB
                localStorage.setItem(testKey + '_large', largeData);
                localStorage.removeItem(testKey + '_large');
            } catch (e) {
                detectedMethods++;
            }
            
            // Test 2: sessionStorage behavior
            totalMethods++;
            try {
                const mediumData = 'x'.repeat(512 * 1024); // 512KB
                sessionStorage.setItem(testKey + '_session', mediumData);
                sessionStorage.removeItem(testKey + '_session');
            } catch (e) {
                detectedMethods++;
            }
            
            // Test 3: Multiple localStorage entries
            totalMethods++;
            try {
                for (let i = 0; i < 50; i++) {
                    localStorage.setItem(testKey + '_' + i, 'x'.repeat(10000));
                }
                for (let i = 0; i < 50; i++) {
                    localStorage.removeItem(testKey + '_' + i);
                }
            } catch (e) {
                detectedMethods++;
            }
            
            const detectionRatio = detectedMethods / totalMethods;
            
            return {
                detected: detectionRatio > 0,
                confidence: detectionRatio,
                details: {
                    detectedMethods: detectedMethods,
                    totalMethods: totalMethods,
                    ratio: detectionRatio
                }
            };
            
        } catch (error) {
            return { detected: true, confidence: 0.75, details: 'Storage completely blocked' };
        }
    }

    // ✅ IMPROVED: Better localStorage quota testing
    async testLocalStorageQuotaEnhanced() {
        try {
            const testKey = 'quota_test_' + Date.now();
            let quotaHit = false;
            let estimatedQuota = 0;
            
            // Binary search for quota limit
            let low = 0;
            let high = 10 * 1024 * 1024; // 10MB max test
            
            while (low < high - 1000) {
                const mid = Math.floor((low + high) / 2);
                try {
                    const testData = 'x'.repeat(mid);
                    localStorage.setItem(testKey, testData);
                    localStorage.removeItem(testKey);
                    low = mid;
                } catch (e) {
                    high = mid;
                    quotaHit = true;
                    break;
                }
            }
            
            estimatedQuota = low;
            const quotaMB = estimatedQuota / (1024 * 1024);
            
            // ✅ IMPROVED: More aggressive thresholds
            let detected = false;
            let confidence = 0;
            
            if (quotaMB < 2) { // Less than 2MB
                detected = true;
                confidence = 0.90;
            } else if (quotaMB < 5) { // Less than 5MB
                detected = true;
                confidence = 0.75;
            } else if (quotaMB < 10) { // Less than 10MB
                detected = true;
                confidence = 0.50;
            }
            
            return {
                detected: detected,
                confidence: confidence,
                details: {
                    estimatedQuotaMB: quotaMB,
                    quotaHit: quotaHit
                }
            };
            
        } catch (error) {
            return { detected: true, confidence: 0.80, details: 'localStorage quota test failed' };
        }
    }

    // ✅ NEW: Cache API behavior test
    async testCacheAPIBehavior() {
        try {
            if (!('caches' in window)) {
                return { detected: false, confidence: 0, available: false };
            }
            
            const cacheName = 'incognito-test-' + Date.now();
            
            try {
                const cache = await caches.open(cacheName);
                
                // Try to add a request
                const response = new Response('test data');
                await cache.put(new Request('/test'), response);
                
                // Check if it's actually stored
                const stored = await cache.match(new Request('/test'));
                
                await caches.delete(cacheName);
                
                if (!stored) {
                    return { detected: true, confidence: 0.70, details: 'Cache API storage failed' };
                }
                
                return { detected: false, confidence: 0.20 };
                
            } catch (error) {
                return { detected: true, confidence: 0.75, details: 'Cache API error: ' + error.message };
            }
            
        } catch (error) {
            return { detected: true, confidence: 0.65, details: 'Cache API unavailable' };
        }
    }

    // ✅ IMPROVED: Better WebRTC detection
    async testWebRTCEnhanced() {
        return new Promise((resolve) => {
            try {
                let candidateFound = false;
                let iceFailed = false;
                
                const rtc = new RTCPeerConnection({
                    iceServers: [
                        { urls: 'stun:stun.l.google.com:19302' },
                        { urls: 'stun:stun1.l.google.com:19302' }
                    ]
                });
                
                const timeout = setTimeout(() => {
                    rtc.close();
                    if (!candidateFound && !iceFailed) {
                        resolve({ detected: true, confidence: 0.70, details: 'WebRTC ICE gathering timeout' });
                    } else if (iceFailed) {
                        resolve({ detected: true, confidence: 0.80, details: 'WebRTC ICE gathering failed' });
                    } else {
                        resolve({ detected: false, confidence: 0.25 });
                    }
                }, 3000);
                
                rtc.onicecandidate = (event) => {
                    if (event.candidate) {
                        candidateFound = true;
                        clearTimeout(timeout);
                        rtc.close();
                        resolve({ detected: false, confidence: 0.20 });
                    }
                };
                
                rtc.onicegatheringstatechange = () => {
                    if (rtc.iceGatheringState === 'complete' && !candidateFound) {
                        iceFailed = true;
                    }
                };
                
                // Create data channel and offer
                rtc.createDataChannel('test');
                rtc.createOffer()
                    .then(offer => rtc.setLocalDescription(offer))
                    .catch(error => {
                        clearTimeout(timeout);
                        rtc.close();
                        resolve({ detected: true, confidence: 0.75, details: 'WebRTC offer creation failed' });
                    });
                
            } catch (error) {
                resolve({ detected: true, confidence: 0.80, details: 'WebRTC completely blocked' });
            }
        });
    }

    // ✅ NEW: Filesystem API detection
    async testFileSystemAPI() {
        try {
            if ('webkitRequestFileSystem' in window) {
                return new Promise((resolve) => {
                    const requestFS = window.webkitRequestFileSystem || window.requestFileSystem;
                    
                    requestFS(window.TEMPORARY, 1024, 
                        (fs) => {
                            resolve({ detected: false, confidence: 0.25 });
                        },
                        (error) => {
                            resolve({ detected: true, confidence: 0.65, details: 'FileSystem API blocked' });
                        }
                    );
                });
            }
            
            return { detected: false, confidence: 0, available: false };
        } catch (error) {
            return { detected: true, confidence: 0.60, details: 'FileSystem API error' };
        }
    }

    // ✅ IMPROVED: Better service worker detection
    async testServiceWorkerEnhanced() {
        try {
            if (!('serviceWorker' in navigator)) {
                return { detected: false, confidence: 0, available: false };
            }
            
            // Try multiple service worker operations
            try {
                // Test 1: Simple registration
                const registration = await navigator.serviceWorker.register(
                    'data:application/javascript,console.log("test");', 
                    { scope: '/test-scope-' + Date.now() }
                );
                
                if (registration) {
                    await registration.unregister();
                    
                    // Test 2: Check if we can get registrations
                    const registrations = await navigator.serviceWorker.getRegistrations();
                    
                    return { detected: false, confidence: 0.15 };
                }
                
            } catch (error) {
                if (error.name === 'SecurityError') {
                    return { detected: true, confidence: 0.85, details: 'Service worker security error (incognito signature)' };
                } else {
                    return { detected: true, confidence: 0.70, details: 'Service worker registration failed: ' + error.name };
                }
            }
            
        } catch (error) {
            return { detected: true, confidence: 0.75, details: 'Service worker completely blocked' };
        }
        
        return { detected: true, confidence: 0.60 };
    }

    // ✅ NEW: Browser fingerprint stability test
    async testFingerprintStability() {
        try {
            // Generate fingerprint components
            const fp1 = this.generateQuickFingerprint();
            
            // Wait and generate again
            await new Promise(resolve => setTimeout(resolve, 100));
            const fp2 = this.generateQuickFingerprint();
            
            // In incognito, some fingerprint components may be randomized or unstable
            const stability = fp1 === fp2 ? 1 : 0;
            
            return {
                detected: stability < 1,
                confidence: stability < 1 ? 0.40 : 0.10,
                details: {
                    stable: stability === 1,
                    fp1: fp1.substring(0, 10) + '...',
                    fp2: fp2.substring(0, 10) + '...'
                }
            };
            
        } catch (error) {
            return { detected: false, confidence: 0, available: false };
        }
    }

    generateQuickFingerprint() {
        const components = [
            navigator.userAgent,
            screen.width + 'x' + screen.height,
            new Date().getTimezoneOffset(),
            navigator.language,
            navigator.hardwareConcurrency || 0
        ];
        
        return components.join('|');
    }

    // Keep existing methods for compatibility
    shouldRateLimit(email) {
        try {
            const stored = localStorage.getItem(this.rateLimitStorage);
            if (!stored) return false;
            
            const data = JSON.parse(stored);
            const now = Date.now();
            
            const recentAttempts = data.attempts.filter(
                attempt => now - attempt.timestamp < this.cooldownPeriod
            );
            
            return recentAttempts.length >= 3;
        } catch (error) {
            return false;
        }
    }

    trackAttempt(email, detectionResult) {
        try {
            const stored = localStorage.getItem(this.rateLimitStorage) || '{"attempts":[]}';
            const data = JSON.parse(stored);
            
            data.attempts.push({
                email: email.substring(0, 3) + '***',
                confidence: detectionResult.confidence,
                action: detectionResult.action,
                timestamp: Date.now()
            });
            
            data.attempts = data.attempts.slice(-10);
            localStorage.setItem(this.rateLimitStorage, JSON.stringify(data));
            
            return data.attempts.length;
        } catch (error) {
            return 0;
        }
    }

    async getEnhancedFingerprint() {
        const components = [];
        
        components.push(navigator.userAgent);
        components.push(navigator.language);
        components.push(screen.width + 'x' + screen.height);
        components.push(screen.colorDepth);
        components.push(new Date().getTimezoneOffset());
        components.push(navigator.platform);
        components.push(navigator.hardwareConcurrency || 0);
        
        try {
            components.push(navigator.deviceMemory || 'unknown');
            components.push(navigator.maxTouchPoints || 0);
            components.push(window.devicePixelRatio || 1);
        } catch (e) {
            components.push('restricted');
        }
        
        const fingerprint = components.join('|');
        const hash = await this.hashString(fingerprint);
        
        return {
            hash: 'enhanced_' + hash,
            fingerprint: {
                userAgent: navigator.userAgent.substring(0, 100),
                screen: screen.width + 'x' + screen.height,
                timezone: new Date().getTimezoneOffset(),
                platform: navigator.platform,
                enhanced: true,
                timestamp: Date.now()
            }
        };
    }

    async hashString(str) {
        const encoder = new TextEncoder();
        const data = encoder.encode(str);
        const hashBuffer = await crypto.subtle.digest('SHA-256', data);
        const hashArray = Array.from(new Uint8Array(hashBuffer));
        return hashArray.map(b => b.toString(16).padStart(2, '0')).join('').substring(0, 16);
    }
}

// Initialize global enhanced detector
window.enhancedIncognitoDetector = new EnhancedIncognitoDetector();

console.log('✅ Enhanced Incognito Detection System loaded (improved accuracy)');