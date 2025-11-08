// support-shared-spa.js - Universal controller for support page (SSR and SPA modes)

export class SupportController {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.tiersData = [];
        this.currentUser = null;
        this.currentKofiUrl = '';
    }

    // ✅ For SPA mode: generate HTML
    async render() {
        if (this.mode === 'ssr') {
            throw new Error('render() should not be called in SSR mode');
        }
        
        // In SPA mode, just return the HTML shell
        // Data loading happens in mount() after HTML is in the DOM
        return this.generateHTML();
    }

    // ✅ For both modes: attach event listeners and initialize
    async mount() {
        console.log(`💝 Support: Mounting in ${this.mode} mode...`);
        
        let needsDataLoad = true;
        
        if (this.mode === 'ssr') {
            // SSR: Read bootstrap data from DOM if available
            this.hydrateFromDOM();
            // Render the hydrated data immediately if we have it
            if (this.tiersData.length > 0) {
                console.log('📦 Support: Rendering hydrated tiers data');
                this.renderTiers(this.tiersData);
                needsDataLoad = false; // Skip API call if we have data from hydration
            }
        }
        // For SPA mode, always load data
        
        // Load/refresh data
        if (needsDataLoad) {
            await this.loadSupportData().catch(err => {
                console.error('Failed to load support data:', err);
                this.renderEmptyState(err.message);
            });
        }
        
        // Setup event handlers (always needed)
        this.setupEventListeners();
        
        console.log('✅ Support: Mounted successfully');
    }

    // ✅ Read data from DOM (SSR mode)
    hydrateFromDOM() {
        const bootstrapScript = document.getElementById('support-bootstrap-data');
        if (bootstrapScript) {
            try {
                const data = JSON.parse(bootstrapScript.textContent);
                this.tiersData = data.tiers || [];
                this.currentUser = data.user || null;
                console.log('📦 Hydrated support data from DOM:', this.tiersData);
                console.log('📦 Hydrated user data:', this.currentUser);
                
                // Update modal email with hydrated user data
                this.updateModalEmail();
            } catch (error) {
                console.error('Error parsing bootstrap data:', error);
            }
        }
    }

    // ✅ Generate HTML for SPA mode
    generateHTML() {
        const userEmail = this.currentUser?.email || 'your-email@example.com';
        
        return `
            <div class="support-container">
                <div class="support-header">
                    <h2>Support Web Audio</h2>
                    <p>Your support helps us maintain and improve Web Audio. Choose the option that works best for you!</p>
                </div>
                
                <!-- Ko-fi Section (First) -->
                <div class="support-section">
                    <div class="support-section-header">
                        <div class="support-section-icon kofi">
                            <i class="fas fa-mug-hot"></i>
                        </div>
                        <div class="support-section-title">
                            <h3>Support with Ko-fi</h3>
                        </div>
                        <p class="support-section-description">Support Web Audio with one-time donations or recurring monthly memberships. Perfect for flexible support options!</p>
                    </div>
                    
                    <!-- Ko-fi Benefits -->
                    <div class="benefits-container">
                        <div class="benefits-header">
                            <h4>Ko-fi Supporter Benefits</h4>
                        </div>
                        <div class="benefits-content">
                            <ul class="benefits-list">
                                <li><i class="fas fa-download"></i> Monthly downloads quota</li>
                                <li><i class="fas fa-book"></i> Book requests with chapter limits</li>
                                <li><i class="fas fa-bolt"></i> Early access to features</li>
                                <li><i class="fas fa-comment-alt"></i> Priority support</li>
                                <li><i class="fas fa-desktop"></i> Multiple active sessions</li>
                                <li><i class="fas fa-book-open"></i> Access to exclusive content</li>
                                <li><i class="fas fa-comments"></i> Discord community access</li>
                                <li><i class="fas fa-heart"></i> Help shape Web Audio's future</li>
                            </ul>
                        </div>
                    </div>
                    
                    <!-- Ko-fi Tiers -->
                    <div class="tiers-container">
                        <div class="tiers-header">
                            <h4>Ko-fi Membership Tiers</h4>
                            <p>Choose the tier that works best for you. All tiers include exclusive content and features.</p>
                        </div>
                        
                        <div id="kofiTiersGrid" class="tiers-grid">
                            <div class="empty-collection">
                                <i class="fas fa-spinner fa-spin"></i>
                                <h2>Loading Ko-fi Tiers</h2>
                                <p>Please wait...</p>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Ko-fi Support Button -->
                    <div class="support-actions">
                        <button class="support-btn kofi kofi-link" data-url="https://ko-fi.com/webaudio">
                            <i class="fas fa-mug-hot"></i> Support on Ko-fi
                        </button>
                    </div>
                </div>
                
                <!-- Patreon Section (Second) -->
                <div class="support-section">
                    <div class="support-section-header">
                        <div class="support-section-icon patreon">
                            <i class="fab fa-patreon"></i>
                        </div>
                        <div class="support-section-title">
                            <h3>Support with Patreon <span class="coming-soon-tag">Coming Soon</span></h3>
                        </div>
                        <p class="support-section-description">Join our Patreon community for exclusive benefits and help us continue to create the best audio experience on the web.</p>
                    </div>
                    
                    <!-- Patreon Benefits -->
                    <div class="benefits-container">
                        <div class="benefits-header">
                            <h4>Patreon Supporter Benefits</h4>
                        </div>
                        <div class="benefits-content">
                            <ul class="benefits-list">
                                <li><i class="fas fa-download"></i> Monthly downloads quota</li>
                                <li><i class="fas fa-book"></i> Book requests with chapter limits</li>
                                <li><i class="fas fa-bolt"></i> Early access to features</li>
                                <li><i class="fas fa-comment-alt"></i> Priority support</li>
                                <li><i class="fas fa-desktop"></i> Multiple active sessions</li>
                                <li><i class="fas fa-book-open"></i> Access to exclusive content</li>
                                <li><i class="fas fa-comments"></i> Discord community access</li>
                                <li><i class="fas fa-heart"></i> Help shape Web Audio's future</li>
                                <li><i class="fas fa-crown"></i> Exclusive Patreon perks</li>
                                <li><i class="fas fa-star"></i> Behind-the-scenes updates</li>
                            </ul>
                        </div>
                    </div>
                    
                    <!-- Patreon Tiers -->
                    <div class="tiers-container">
                        <div class="tiers-header">
                            <h4>Patreon Membership Tiers</h4>
                            <p>Choose the tier that works best for you. All tiers include exclusive content and features.</p>
                        </div>
                        
                        <div id="patreonTiersGrid" class="tiers-grid">
                            <div class="empty-collection">
                                <i class="fas fa-spinner fa-spin"></i>
                                <h2>Loading Patreon Tiers</h2>
                                <p>Please wait...</p>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Patreon Support Button -->
                    <div class="support-actions">
                        <button class="support-btn disabled patreon">
                            <i class="fab fa-patreon"></i> Coming Soon
                        </button>
                    </div>
                </div>
                
                <!-- Thank You Message -->
                <div class="thank-you-message">
                    <h3>Thank you for supporting Web Audio!</h3>
                    <p>Your support helps us continue to improve and maintain the service for everyone. We're grateful for every contribution, no matter how small.</p>
                </div>
            </div>

            <!-- Ko-fi Email Confirmation Modal -->
            <div id="kofiModal" class="modal-overlay">
                <div class="modal-content">
                    <button class="modal-close" onclick="window.supportController.closeKofiModal()">
                        <i class="fas fa-times"></i>
                    </button>
                    
                    <div class="modal-header">
                        <i class="fas fa-mug-hot modal-icon"></i>
                        <h3>Before You Support on Ko-fi</h3>
                        <p>Important information to ensure your benefits are properly activated</p>
                    </div>
                    
                    <div class="modal-body">
                        <div class="modal-warning">
                            <i class="fas fa-exclamation-triangle warning-icon"></i>
                            <h4>⚠️ CRITICAL: Use the Same Email Address!</h4>
                            <p>To receive your Ko-fi supporter benefits on Web Audio, you <strong>MUST</strong> use the exact same email address for both accounts.</p>
                        </div>
                        
                        <div class="email-emphasis">
                            <i class="fas fa-envelope email-icon"></i>
                            <h4>Your Web Audio Account Email:</h4>
                            <div class="user-email">${userEmail}</div>
                            <p><strong>Use this EXACT email when signing up or logging into Ko-fi!</strong></p>
                        </div>
                        
                        <h4 style="color: var(--text-color); margin-bottom: 1rem;">How it works:</h4>
                        <ol class="step-list">
                            <li>
                                <span class="step-number">1</span>
                                <div class="step-content">
                                    <strong>Click "Continue to Ko-fi"</strong> below to go to Ko-fi
                                </div>
                            </li>
                            <li>
                                <span class="step-number">2</span>
                                <div class="step-content">
                                    <strong>Sign up or log in to Ko-fi</strong> using your Web Audio email above
                                </div>
                            </li>
                            <li>
                                <span class="step-number">3</span>
                                <div class="step-content">
                                    <strong>Choose your support tier</strong> on Ko-fi
                                </div>
                            </li>
                            <li>
                                <span class="step-number">4</span>
                                <div class="step-content">
                                    <strong>Return to Web Audio</strong> - your benefits will be automatically activated
                                </div>
                            </li>
                        </ol>
                        
                        <div style="background-color: var(--bg-alt-color); padding: 1rem; border-radius: 6px; margin-top: 1rem;">
                            <p style="margin: 0; color: var(--text-muted); font-size: 0.9rem;">
                                <i class="fas fa-info-circle" style="color: var(--primary-color); margin-right: 0.5rem;"></i>
                                This email matching system is how we automatically connect your Ko-fi support to your Web Audio account and unlock your premium features.
                            </p>
                        </div>
                    </div>
                    
                    <div class="modal-actions">
                        <button class="modal-btn secondary" onclick="window.supportController.closeKofiModal()">
                            <i class="fas fa-arrow-left"></i> Go Back
                        </button>
                        <button class="modal-btn primary" onclick="window.supportController.proceedToKofi()">
                            <i class="fas fa-external-link-alt"></i> Continue to Ko-fi
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    async loadSupportData() {
        try {
            const response = await fetch('/api/support/tiers');
            if (!response.ok) throw new Error('Failed to load support tiers');
            
            const data = await response.json();
            this.tiersData = data.tiers || [];
            this.currentUser = data.user || null;
            
            console.log('Loaded support tiers:', this.tiersData);
            
            // If user data is missing, try to fetch it separately
            if (!this.currentUser || !this.currentUser.email) {
                console.log('⚠️ User data not in tiers API, attempting to fetch separately...');
                await this.fetchUserData();
            }
            
            this.renderTiers(this.tiersData);
            
            // Update email in modal if we have user data
            this.updateModalEmail();
        } catch (error) {
            console.error('Error loading support data:', error);
            this.renderEmptyState(error.message);
        }
    }

    async fetchUserData() {
        try {
            // Try to fetch user data from a common endpoint
            const response = await fetch('/api/user/me');
            if (response.ok) {
                const userData = await response.json();
                this.currentUser = userData;
                console.log('✅ Fetched user data:', this.currentUser);
            } else {
                console.log('⚠️ Could not fetch user data from /api/user/me');
            }
        } catch (error) {
            console.log('⚠️ Failed to fetch user data:', error.message);
        }
    }

    updateModalEmail() {
        if (!this.currentUser || !this.currentUser.email) {
            console.log('⚠️ No user email to update');
            return;
        }
        
        const emailDisplay = document.querySelector('.user-email');
        if (emailDisplay) {
            emailDisplay.textContent = this.currentUser.email;
            console.log('✅ Updated modal email to:', this.currentUser.email);
        } else {
            console.warn('⚠️ Email display element not found in DOM');
        }
    }

    renderTiers(tiers) {
        if (!tiers || tiers.length === 0) {
            this.renderEmptyState();
            return;
        }

        // Filter Ko-fi tiers (exclude Team Members and development tiers)
        const kofiTiers = tiers.filter(tier => 
            tier.is_active && 
            tier.is_kofi && 
            tier.title !== "Team Members" && 
            !tier.title.toLowerCase().includes("development")
        );

        // Filter Patreon tiers (exclude Team Members and development tiers)
        const patreonTiers = tiers.filter(tier => 
            tier.is_active && 
            !tier.is_kofi && 
            tier.title !== "Team Members" && 
            !tier.title.toLowerCase().includes("development")
        );

        // Render Ko-fi tiers
        const kofiGrid = document.getElementById('kofiTiersGrid');
        if (kofiGrid) {
            if (kofiTiers.length > 0) {
                kofiGrid.innerHTML = kofiTiers.map(tier => this.renderTierCard(tier)).join('');
            } else {
                kofiGrid.innerHTML = '<div class="empty-collection"><i class="fas fa-mug-hot"></i><h2>No Ko-fi tiers available</h2></div>';
            }
        }

        // Render Patreon tiers
        const patreonGrid = document.getElementById('patreonTiersGrid');
        if (patreonGrid) {
            if (patreonTiers.length > 0) {
                patreonGrid.innerHTML = patreonTiers.map(tier => this.renderTierCard(tier)).join('');
            } else {
                patreonGrid.innerHTML = '<div class="empty-collection"><i class="fab fa-patreon"></i><h2>No Patreon tiers available yet</h2></div>';
            }
        }
    }

    renderTierCard(tier) {
        const priceDisplay = tier.amount_cents === 0 
            ? 'Free' 
            : `$${(tier.amount_cents / 100).toFixed(2)}`;
        
        const platformBadgeClass = tier.is_kofi ? 'kofi' : 'patreon';
        const platformName = tier.is_kofi ? 'Ko-fi' : 'Patreon';
        const platformIcon = tier.is_kofi ? 'fas fa-mug-hot' : 'fab fa-patreon';
        
        // Handle chapter info
        let chapterInfo = '';
        if (tier.chapters_allowed_per_book_request) {
            if (tier.chapters_allowed_per_book_request === 'inf' || tier.chapters_allowed_per_book_request >= 1000000) {
                chapterInfo = ' <span class="chapter-info">(unlimited chapters)</span>';
            } else {
                chapterInfo = ` <span class="chapter-info">(${tier.chapters_allowed_per_book_request} chapters max)</span>`;
            }
        }

        // Build voice access display
        let voiceAccessHTML = '';
        if (tier.voice_access && tier.voice_access.length > 0) {
            const voiceCount = tier.voice_access.length;
            voiceAccessHTML = `<li><i class="fas fa-microphone"></i> <span class="highlight">${voiceCount}</span>&nbsp;Voice${voiceCount > 1 ? 's' : ''} available</li>`;
        }

        // Read-along access
        let readAlongHTML = '';
        if (tier.read_along_access) {
            readAlongHTML = '<li><i class="fas fa-book-reader"></i> Read-along feature access</li>';
        }

        // Button HTML
        let buttonHTML = '';
        if (tier.is_kofi) {
            buttonHTML = `
                <button class="support-btn kofi kofi-link" data-url="https://ko-fi.com/webaudio">
                    <i class="fas fa-mug-hot"></i> Join on Ko-fi
                </button>
            `;
        } else {
            buttonHTML = `
                <button class="support-btn disabled patreon">
                    <i class="fab fa-patreon"></i> Coming Soon
                </button>
            `;
        }
        
        return `
            <div class="tier-card">
                <div class="tier-card-header">
                    <span class="tier-platform-badge ${platformBadgeClass}">${platformName}</span>
                    <h3 class="tier-card-title">${this.escapeHtml(tier.title)}</h3>
                    <p class="tier-card-price">${priceDisplay}/month</p>
                    <p class="tier-card-description">${this.escapeHtml(tier.description || 'Premium Web Audio experience')}</p>
                </div>
                <div class="tier-card-content">
                    <ul class="tier-benefits-list">
                        <li><i class="fas fa-download"></i> <span class="highlight">${tier.album_downloads_allowed}</span>&nbsp;Album downloads per month</li>
                        <li><i class="fas fa-music"></i> <span class="highlight">${tier.track_downloads_allowed}</span>&nbsp;Track downloads per month</li>
                        <li>
                            <i class="fas fa-book"></i> 
                            <span class="highlight">${tier.book_requests_allowed || 0}</span>&nbsp;Book ${(tier.book_requests_allowed || 0) === 1 ? 'request' : 'requests'} per month${chapterInfo}
                        </li>
                        <li><i class="fas fa-desktop"></i> <span class="highlight">${tier.max_sessions || 1}</span>&nbsp;Active ${(tier.max_sessions || 1) === 1 ? 'session' : 'sessions'}</li>
                        ${voiceAccessHTML}
                        ${readAlongHTML}
                        <li><i class="fas fa-book-open"></i> <span class="highlight">${tier.books_percentage || 0}</span>% of books unlocked</li>
                        <li><i class="fas fa-heart"></i> Support toward development</li>
                        <li><i class="fas fa-comments"></i> Access to exclusive Discord channels</li>
                        ${tier.amount_cents >= 1200 ? '<li><i class="fas fa-crown"></i> Priority access to exclusive features</li>' : ''}
                    </ul>
                </div>
                <div class="tier-card-footer">
                    ${buttonHTML}
                </div>
            </div>
        `;
    }

    renderEmptyState(errorMessage = null) {
        const kofiGrid = document.getElementById('kofiTiersGrid');
        const patreonGrid = document.getElementById('patreonTiersGrid');
        
        const emptyHTML = `
            <div class="empty-collection">
                ${errorMessage ? `<i class="fas fa-exclamation-circle"></i>` : `<i class="fas fa-heart"></i>`}
                <h2>${errorMessage ? 'Error Loading Support Options' : 'No Support Tiers Available'}</h2>
                <p>${errorMessage || 'Support options are currently being configured.'}</p>
                ${errorMessage ? `
                    <button class="btn-primary" onclick="location.reload()">
                        <i class="fas fa-sync"></i> Refresh Page
                    </button>
                ` : ''}
            </div>
        `;
        
        if (kofiGrid) kofiGrid.innerHTML = emptyHTML;
        if (patreonGrid) patreonGrid.innerHTML = emptyHTML;
    }

    selectTier(platform, tierName) {
        console.log(`Selected ${platform} tier: ${tierName}`);
        
        if (platform === 'kofi') {
            this.currentKofiUrl = 'https://ko-fi.com/webaudio';
            this.showKofiModal();
        } else if (platform === 'patreon') {
            window.open('https://www.patreon.com/your-patreon', '_blank');
            this.showToast('Patreon support coming soon!');
        }
    }

    showKofiModal() {
        const modal = document.getElementById('kofiModal');
        if (!modal) {
            console.error('Ko-fi modal not found in DOM');
            return;
        }
        
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
        
        // Focus trap for accessibility
        const firstFocusable = modal.querySelector('.modal-close');
        if (firstFocusable) {
            firstFocusable.focus();
        }
    }

    closeKofiModal() {
        const modal = document.getElementById('kofiModal');
        if (!modal) return;
        
        modal.classList.remove('active');
        document.body.style.overflow = '';
    }

    proceedToKofi() {
        this.closeKofiModal();
        
        setTimeout(() => {
            window.open(this.currentKofiUrl || 'https://ko-fi.com/webaudio', '_blank');
        }, 300);
    }

    setupEventListeners() {
        console.log('📡 Support: Setting up event listeners');
        
        // Ko-fi link click handlers
        const kofiLinks = document.querySelectorAll('.kofi-link');
        kofiLinks.forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                this.currentKofiUrl = link.getAttribute('data-url') || 'https://ko-fi.com/webaudio';
                this.showKofiModal();
            });
        });
        
        // Modal overlay click to close
        const modalOverlay = document.getElementById('kofiModal');
        if (modalOverlay) {
            modalOverlay.addEventListener('click', (e) => {
                if (e.target === modalOverlay) {
                    this.closeKofiModal();
                }
            });
        }
        
        // Escape key to close modal
        this.handleEscapeKey = (e) => {
            if (e.key === 'Escape') {
                const modal = document.getElementById('kofiModal');
                if (modal && modal.classList.contains('active')) {
                    this.closeKofiModal();
                }
            }
        };
        document.addEventListener('keydown', this.handleEscapeKey);
        
        console.log('✅ Support: Event listeners setup complete');
    }

    showToast(message) {
        if (window.showToast) {
            window.showToast(message);
        } else {
            const toast = document.createElement('div');
            toast.className = 'toast-notification';
            toast.textContent = message;
            toast.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background-color:rgba(0,0,0,0.8);color:#fff;padding:12px 24px;border-radius:8px;z-index:10000;transition:opacity 0.5s;';
            document.body.appendChild(toast);
            
            setTimeout(() => {
                toast.style.opacity = '0';
                setTimeout(() => toast.remove(), 500);
            }, 3000);
        }
    }

    escapeHtml(unsafe) {
        if (unsafe === null || unsafe === undefined) return '';
        return String(unsafe)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    destroy() {
        console.log('💝 Support: Destroying...');
        
        // Remove escape key listener
        if (this.handleEscapeKey) {
            document.removeEventListener('keydown', this.handleEscapeKey);
        }
        
        return Promise.resolve();
    }
}

// Make it available globally
window.supportController = null;