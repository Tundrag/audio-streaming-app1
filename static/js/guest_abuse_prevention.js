// guest_abuse_prevention.js - Light approach for trial abuse prevention

class LightGuestAbusePreventionSystem {
    constructor() {
        this.trialUsedKey = 'webaudio_trial_used';
        this.deviceTokenKey = 'webaudio_device_token';
        this.initialized = false;
    }

    /**
     * Initialize the light abuse prevention system
     */
    async init() {
        if (this.initialized) return;
        
        try {
            this.initialized = true;
            console.log('✅ Light guest abuse prevention system initialized');
        } catch (error) {
            console.error('❌ Failed to initialize light abuse prevention:', error);
        }
    }

    /**
     * Generate simple device token (not hardware-based)
     */
    generateSimpleDeviceToken() {
        return 'device_' + Date.now() + '_' + Math.random().toString(36).substring(2);
    }

    /**
     * Get minimal device characteristics for server
     */
    getDeviceCharacteristics() {
        return {
            screen_width: screen.width,
            screen_height: screen.height,
            color_depth: screen.colorDepth,
            hardware_concurrency: navigator.hardwareConcurrency || 0,
            platform: navigator.platform,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            language: navigator.language
        };
    }

    /**
     * Mark trial as used on this browser/device
     */
    markTrialUsed(email) {
        const token = this.generateSimpleDeviceToken();
        const data = {
            trialUsed: true,
            timestamp: Date.now(),
            email: email,
            deviceToken: token,
            userAgent: navigator.userAgent.substring(0, 100) // First 100 chars
        };

        // Store in ALL browser storage locations
        this.storeTrialData(data);
        
        console.log('✅ Trial marked as used on this device');
    }

    /**
     * Store trial data in multiple storage locations
     */
    storeTrialData(data) {
        const dataString = JSON.stringify(data);
        
        // localStorage
        try {
            localStorage.setItem(this.trialUsedKey, dataString);
        } catch (error) {
            console.warn('Failed to store in localStorage:', error);
        }

        // sessionStorage
        try {
            sessionStorage.setItem(this.trialUsedKey, dataString);
        } catch (error) {
            console.warn('Failed to store in sessionStorage:', error);
        }

        // Cookies (1 year expiry)
        try {
            this.setCookie(this.trialUsedKey, dataString, 365);
        } catch (error) {
            console.warn('Failed to store in cookies:', error);
        }

        // IndexedDB for extra persistence
        this.storeInIndexedDB(data);
    }

    /**
     * Check if trial has been used on this browser/device
     */
    hasTrialBeenUsed() {
        // Check all storage sources
        const sources = [
            () => localStorage.getItem(this.trialUsedKey),
            () => sessionStorage.getItem(this.trialUsedKey),
            () => this.getCookie(this.trialUsedKey)
        ];

        for (const getSource of sources) {
            try {
                const data = getSource();
                if (data) {
                    const parsed = JSON.parse(data);
                    if (parsed.trialUsed) {
                        return {
                            blocked: true,
                            reason: "This device has already been used for a trial",
                            usedAt: new Date(parsed.timestamp).toLocaleDateString(),
                            previousEmail: parsed.email ? parsed.email.substring(0, 3) + "***" : "unknown"
                        };
                    }
                }
            } catch (error) {
                // Ignore parsing errors, continue checking other sources
            }
        }

        return { blocked: false };
    }

    /**
     * Set cookie with security flags
     */
    setCookie(name, value, days) {
        let expires = '';
        if (days) {
            const date = new Date();
            date.setTime(date.getTime() + (days * 24 * 60 * 60 * 1000));
            expires = '; expires=' + date.toUTCString();
        }
        
        // Set cookie with security flags
        const secure = location.protocol === 'https:' ? '; secure' : '';
        document.cookie = name + '=' + encodeURIComponent(value) + expires + '; path=/; samesite=strict' + secure;
    }

    /**
     * Get cookie value
     */
    getCookie(name) {
        const nameEQ = name + '=';
        const ca = document.cookie.split(';');
        
        for (let i = 0; i < ca.length; i++) {
            let c = ca[i];
            while (c.charAt(0) === ' ') c = c.substring(1, c.length);
            if (c.indexOf(nameEQ) === 0) {
                return decodeURIComponent(c.substring(nameEQ.length, c.length));
            }
        }
        return null;
    }

    /**
     * Store in IndexedDB for additional persistence
     */
    async storeInIndexedDB(data) {
        try {
            const dbRequest = indexedDB.open('WebAudioLightGuestDB', 1);
            
            dbRequest.onupgradeneeded = function(event) {
                const db = event.target.result;
                if (!db.objectStoreNames.contains('lightGuestData')) {
                    const store = db.createObjectStore('lightGuestData', { keyPath: 'id' });
                }
            };

            dbRequest.onsuccess = function(event) {
                const db = event.target.result;
                const transaction = db.transaction(['lightGuestData'], 'readwrite');
                const store = transaction.objectStore('lightGuestData');
                
                store.put({
                    id: 'trialUsageData',
                    ...data,
                    stored_at: Date.now()
                });
            };
        } catch (error) {
            console.warn('Failed to store in IndexedDB:', error);
        }
    }

    /**
     * Pre-check registration before sending to server
     */
    async preCheckRegistration(email) {
        // Ensure system is initialized
        if (!this.initialized) {
            await this.init();
        }

        // Check if trial already used on this browser
        const clientCheck = this.hasTrialBeenUsed();
        if (clientCheck.blocked) {
            return {
                allowed: false,
                reason: clientCheck.reason,
                code: 'BROWSER_ALREADY_USED',
                source: 'client',
                details: {
                    usedAt: clientCheck.usedAt,
                    previousEmail: clientCheck.previousEmail
                }
            };
        }

        return { allowed: true, source: 'client' };
    }

    /**
     * Get device data for server-side validation
     */
    async getDeviceDataForServer() {
        if (!this.initialized) {
            await this.init();
        }

        return this.getDeviceCharacteristics();
    }

    /**
     * Clean up old data (privacy compliance)
     */
    cleanupOldData() {
        try {
            // Check if stored data is older than 90 days
            const sources = [
                () => localStorage.getItem(this.trialUsedKey),
                () => sessionStorage.getItem(this.trialUsedKey)
            ];

            const ninetyDaysAgo = Date.now() - (90 * 24 * 60 * 60 * 1000);

            for (const getSource of sources) {
                try {
                    const data = getSource();
                    if (data) {
                        const parsed = JSON.parse(data);
                        if (parsed.timestamp < ninetyDaysAgo) {
                            // Data is old, remove it
                            localStorage.removeItem(this.trialUsedKey);
                            sessionStorage.removeItem(this.trialUsedKey);
                            console.log('🧹 Cleaned up old trial data for privacy');
                            break;
                        }
                    }
                } catch (error) {
                    // Ignore errors during cleanup
                }
            }
        } catch (error) {
            console.warn('Error during data cleanup:', error);
        }
    }
}

// Initialize global instance
window.lightGuestAbusePreventionSystem = new LightGuestAbusePreventionSystem();

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.lightGuestAbusePreventionSystem.init();
    });
} else {
    window.lightGuestAbusePreventionSystem.init();
}

// Cleanup old data periodically (privacy)
setInterval(() => {
    if (window.lightGuestAbusePreventionSystem.initialized) {
        window.lightGuestAbusePreventionSystem.cleanupOldData();
    }
}, 24 * 60 * 60 * 1000); // Once per day

// Utility functions for login.html integration
window.lightGuestUtils = {
    /**
     * Get device data for registration
     */
    async getDeviceData() {
        if (!window.lightGuestAbusePreventionSystem.initialized) {
            await window.lightGuestAbusePreventionSystem.init();
        }
        
        return await window.lightGuestAbusePreventionSystem.getDeviceDataForServer();
    },

    /**
     * Pre-check registration before sending to server
     */
    async preCheckRegistration(email) {
        if (!window.lightGuestAbusePreventionSystem.initialized) {
            await window.lightGuestAbusePreventionSystem.init();
        }
        
        return await window.lightGuestAbusePreventionSystem.preCheckRegistration(email);
    },

    /**
     * Mark trial as used after successful registration
     */
    markTrialUsed(email) {
        if (window.lightGuestAbusePreventionSystem.initialized) {
            window.lightGuestAbusePreventionSystem.markTrialUsed(email);
        }
    },

    /**
     * Show user-friendly error for blocked registration
     */
    showBlockedRegistrationMessage(blockReason) {
        const messages = {
            'BROWSER_ALREADY_USED': `This device has already been used for a trial. Only one trial per device is allowed.`,
            'EMAIL_USED': 'This email address has already been used for a trial. Please consider supporting on Ko-fi for full access.',
            'DEVICE_ALREADY_USED': 'This device has already been used for a trial. Please use a different device or consider supporting on Ko-fi.'
        };
        
        return messages[blockReason.code] || blockReason.reason || 'Registration not allowed at this time.';
    }
};

console.log('🔒 Light Guest Abuse Prevention System loaded');