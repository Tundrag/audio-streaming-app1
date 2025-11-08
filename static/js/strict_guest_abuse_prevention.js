// strict_guest_abuse_prevention.js - WebAuthn Passkey Layer
class StrictGuestAbusePreventionSystem {
    constructor() {
        this.rpId = window.location.hostname;
        this.rpName = "Web Audio Guest Trial";
        this.credentialStorageKey = 'webaudio_passkey_used';
        this.initialized = false;
    }

    /**
     * Initialize the strict WebAuthn system
     */
    async init() {
        if (this.initialized) return;
        
        try {
            // Check WebAuthn support
            if (!window.PublicKeyCredential) {
                console.warn('⚠️ WebAuthn not supported - falling back to light prevention only');
                this.initialized = false;
                return;
            }

            // Check platform authenticator availability
            const available = await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
            if (!available) {
                console.warn('⚠️ Platform authenticator not available - falling back to light prevention only');
                this.initialized = false;
                return;
            }

            this.initialized = true;
            console.log('✅ Strict WebAuthn abuse prevention system initialized');
        } catch (error) {
            console.error('❌ Failed to initialize WebAuthn system:', error);
            this.initialized = false;
        }
    }

    /**
     * Generate challenge for WebAuthn operations
     */
    generateChallenge() {
        return crypto.getRandomValues(new Uint8Array(32));
    }

    /**
     * Convert ArrayBuffer to base64url
     */
    arrayBufferToBase64url(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
    }

    /**
     * Convert base64url to ArrayBuffer
     */
    base64urlToArrayBuffer(base64url) {
        const binary = atob(base64url.replace(/-/g, '+').replace(/_/g, '/'));
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes.buffer;
    }

    /**
     * Silent check for existing credentials during registration
     */
    async silentCredentialCheck() {
        if (!this.initialized) {
            return { hasCredential: false, reason: 'WebAuthn not available' };
        }

        try {
            // Create a dummy request to see if any credentials exist
            const challenge = this.generateChallenge();
            
            const getOptions = {
                challenge: challenge,
                rpId: this.rpId,
                allowCredentials: [], // Empty = check for any resident credentials
                userVerification: 'preferred',
                timeout: 5000 // Short timeout for silent check
            };

            // Attempt silent credential discovery
            const credential = await navigator.credentials.get({
                publicKey: getOptions,
                signal: AbortSignal.timeout(5000)
            });

            if (credential) {
                const credentialId = this.arrayBufferToBase64url(credential.rawId);
                console.log('🚫 Found existing passkey credential:', credentialId.substring(0, 16) + '...');
                
                return {
                    hasCredential: true,
                    credentialId: credentialId,
                    reason: 'Device already has a trial passkey registered'
                };
            }

            return { hasCredential: false };
            
        } catch (error) {
            // Silent failure is expected if no credentials exist
            if (error.name === 'NotAllowedError' || error.name === 'TimeoutError') {
                return { hasCredential: false };
            }
            
            console.warn('Silent credential check failed:', error.name);
            return { hasCredential: false, reason: 'Credential check failed' };
        }
    }

    /**
     * Create passkey credential after successful OTP verification
     */
    async createTrialPasskey(userData) {
        if (!this.initialized) {
            throw new Error('WebAuthn not available on this device');
        }

        try {
            const challenge = this.generateChallenge();
            const userId = new TextEncoder().encode(userData.email);

            const createOptions = {
                challenge: challenge,
                rp: {
                    id: this.rpId,
                    name: this.rpName
                },
                user: {
                    id: userId,
                    name: userData.email,
                    displayName: userData.username
                },
                pubKeyCredParams: [
                    { alg: -7, type: "public-key" },  // ES256
                    { alg: -257, type: "public-key" } // RS256
                ],
                authenticatorSelection: {
                    authenticatorAttachment: "platform",
                    userVerification: "preferred",
                    requireResidentKey: true,
                    residentKey: "required"
                },
                attestation: "none",
                timeout: 60000
            };

            console.log('🔐 Requesting passkey creation...');
            
            const credential = await navigator.credentials.create({
                publicKey: createOptions
            });

            if (!credential) {
                throw new Error('Failed to create passkey credential');
            }

            const credentialId = this.arrayBufferToBase64url(credential.rawId);
            const publicKey = this.arrayBufferToBase64url(credential.response.publicKey);

            // Store credential info locally for future checks
            const credentialInfo = {
                credentialId: credentialId,
                email: userData.email,
                createdAt: Date.now(),
                deviceInfo: {
                    userAgent: navigator.userAgent.substring(0, 100),
                    platform: navigator.platform
                }
            };

            try {
                localStorage.setItem(this.credentialStorageKey, JSON.stringify(credentialInfo));
            } catch (e) {
                console.warn('Failed to store credential info locally:', e);
            }

            console.log('✅ Passkey created successfully:', credentialId.substring(0, 16) + '...');

            return {
                credentialId: credentialId,
                publicKey: publicKey,
                attestationObject: this.arrayBufferToBase64url(credential.response.attestationObject),
                clientDataJSON: this.arrayBufferToBase64url(credential.response.clientDataJSON)
            };

        } catch (error) {
            console.error('❌ Passkey creation failed:', error);
            
            if (error.name === 'NotAllowedError') {
                throw new Error('You need to save the device passkey to claim your trial');
            } else if (error.name === 'AbortError') {
                throw new Error('Passkey creation was cancelled');
            } else if (error.name === 'NotSupportedError') {
                throw new Error('Passkeys are not supported on this device');
            } else if (error.name === 'SecurityError') {
                throw new Error('Security error during passkey creation');
            } else {
                throw new Error('Failed to create device passkey: ' + error.message);
            }
        }
    }

    /**
     * Get request data including device fingerprint
     */
    async getRequestData() {
        const baseData = {
            // Include light system data
            screen_width: screen.width,
            screen_height: screen.height,
            color_depth: screen.colorDepth,
            hardware_concurrency: navigator.hardwareConcurrency || 0,
            platform: navigator.platform,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            language: navigator.language,
            user_agent: navigator.userAgent,
            timestamp: Date.now()
        };

        // Add device fingerprint
        const deviceString = JSON.stringify(baseData);
        const hashBuffer = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(deviceString));
        const deviceFingerprint = this.arrayBufferToBase64url(hashBuffer);

        return {
            ...baseData,
            device_fingerprint: deviceFingerprint
        };
    }

    /**
     * Check if system is available
     */
    isAvailable() {
        return this.initialized;
    }

    /**
     * Clean up old credentials (privacy)
     */
    cleanupOldCredentials() {
        try {
            const stored = localStorage.getItem(this.credentialStorageKey);
            if (stored) {
                const data = JSON.parse(stored);
                const ninetyDaysAgo = Date.now() - (90 * 24 * 60 * 60 * 1000);
                
                if (data.createdAt < ninetyDaysAgo) {
                    localStorage.removeItem(this.credentialStorageKey);
                    console.log('🧹 Cleaned up old passkey data for privacy');
                }
            }
        } catch (error) {
            console.warn('Error during passkey cleanup:', error);
        }
    }
}

// Initialize global strict system
window.strictGuestAbusePreventionSystem = new StrictGuestAbusePreventionSystem();

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.strictGuestAbusePreventionSystem.init();
    });
} else {
    window.strictGuestAbusePreventionSystem.init();
}

// Cleanup old data periodically
setInterval(() => {
    if (window.strictGuestAbusePreventionSystem.initialized) {
        window.strictGuestAbusePreventionSystem.cleanupOldCredentials();
    }
}, 24 * 60 * 60 * 1000); // Once per day

console.log('🔐 Strict WebAuthn Abuse Prevention System loaded');