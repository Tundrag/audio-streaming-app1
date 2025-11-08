// my-benefits-shared-spa.js - Universal controller for My Benefits (SSR and SPA modes)

export class MyBenefitsController {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.benefitsData = null;
        this.currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
        this.bootstrapData = null;
    }

    // ✅ For SPA mode: generate HTML
    async render() {
        if (this.mode === 'ssr') {
            throw new Error('render() should not be called in SSR mode');
        }

        try {
            // Fetch benefits data
            const response = await fetch('/api/my-benefits');
            if (!response.ok) throw new Error('Failed to load benefits');

            this.benefitsData = await response.json();

            return this.generateHTML();
        } catch (error) {
            console.error('Error rendering My Benefits:', error);
            return this.errorHTML(error.message);
        }
    }

    // ✅ For both modes: attach event listeners and initialize
    async mount() {
        console.log(`💼 MyBenefits: Mounting in ${this.mode} mode...`);

        if (this.mode === 'ssr') {
            // SSR: Read bootstrap data from DOM if available
            this.hydrateFromDOM();
        }

        // Update active links
        this.updateActiveLinks('/my-benefits');

        console.log('✅ MyBenefits: Mounted successfully');
    }

    // ✅ Read data from DOM (SSR mode)
    hydrateFromDOM() {
        const bootstrapScript = document.getElementById('my-benefits-bootstrap-data');
        if (bootstrapScript) {
            try {
                this.bootstrapData = JSON.parse(bootstrapScript.textContent);
                if (this.bootstrapData.benefits) {
                    this.benefitsData = this.bootstrapData.benefits;
                    console.log('📦 Hydrated my-benefits data from DOM');
                }
            } catch (error) {
                console.error('Error parsing bootstrap data:', error);
            }
        }
    }

    generateHTML() {
        const b = this.benefitsData;

        // Helper functions
        const formatDate = (dateStr) => {
            if (!dateStr) return 'N/A';
            try {
                const date = new Date(dateStr);
                return date.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
            } catch {
                return dateStr;
            }
        };

        const formatDateTime = (dateStr) => {
            if (!dateStr) return 'N/A';
            try {
                const date = new Date(dateStr);
                return date.toLocaleString('en-US', { month: 'long', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
            } catch {
                return dateStr;
            }
        };

        const getServiceBadge = () => {
            if (!b.tier_info?.service_type) return '';
            const serviceType = b.tier_info.service_type.toLowerCase().replace(/\s+/g, '');
            return `<span class="service-badge ${serviceType}">${b.tier_info.service_type}</span>`;
        };

        const getStatusIndicator = () => {
            let status = 'status-active';
            let icon = 'fa-check-circle';
            let label = 'Active';

            if (b.grace_period_message) {
                status = 'expired' in b.grace_period_message.toLowerCase() ? 'status-expired' : 'status-grace';
                icon = 'expired' in b.grace_period_message.toLowerCase() ? 'fa-exclamation-circle' : 'fa-clock';
                label = 'expired' in b.grace_period_message.toLowerCase() ? 'Expired' : 'Grace Period';
            } else if (b.tier_info?.service_type === 'Guest Trial') {
                if (b.trial_active) {
                    status = 'status-trial';
                    icon = 'fa-hourglass-half';
                    label = 'Trial Active';
                } else {
                    status = 'status-expired';
                    icon = 'fa-exclamation-circle';
                    label = 'Trial Expired';
                }
            }

            return `
                <div class="status-indicator ${status}">
                    <i class="fas ${icon}"></i> ${label}
                </div>
            `;
        };

        const getGracePeriodBanner = () => {
            if (!b.grace_period_message) return '';

            const isExpired = 'expired' in b.grace_period_message.toLowerCase();
            const bannerClass = isExpired ? 'grace-period-expired' : 'grace-period-active';
            const icon = isExpired ? 'fa-exclamation-circle' : 'fa-clock';
            const title = isExpired ? 'Grace Period Expired' : 'Grace Period Active';
            const buttonText = isExpired ? 'Renew Now' : 'Renew';
            const buttonHref = isExpired ? '/support' : (b.tier_info?.service_type === 'Ko-fi' ? 'https://ko-fi.com/webaudio' : 'https://patreon.com/youraccount');
            const target = isExpired ? '' : 'target="_blank"';

            return `
                <div class="grace-period-banner ${bannerClass}">
                    <div class="grace-icon">
                        <i class="fas ${icon}"></i>
                    </div>
                    <div class="grace-content">
                        <div class="grace-title">${title}</div>
                        <div class="grace-message">${b.grace_period_message}</div>
                    </div>
                    <a href="${buttonHref}" class="grace-action" ${target}>${buttonText}</a>
                </div>
            `;
        };

        const getTrialSection = () => {
            if (!b.trial_expires_at || b.tier_info?.service_type !== 'Guest Trial') return '';

            const urgencyClass = b.trial_hours_remaining < 6 ? 'trial-urgent' :
                              b.trial_hours_remaining < 24 ? 'trial-warning' : '';

            const timeLabel = b.trial_hours_remaining >= 24
                ? `${(b.trial_hours_remaining / 24).toFixed(1)} day${(b.trial_hours_remaining / 24).toFixed(1) != 1 ? 's' : ''} remaining`
                : `${Math.round(b.trial_hours_remaining)} hour${Math.round(b.trial_hours_remaining) != 1 ? 's' : ''} remaining`;

            const timeMessage = b.trial_hours_remaining < 1 ? 'Your trial is ending very soon!' :
                              b.trial_hours_remaining < 6 ? 'Your trial is ending soon! Consider supporting to maintain access.' :
                              b.trial_hours_remaining < 24 ? 'Your trial expires today. Don\'t forget to support if you\'re enjoying the content!' :
                              'Enjoy your trial period! Support the creator to continue access after trial.';

            const supportButtons = b.trial_hours_remaining < 24 ? `
                <div class="flex gap-2 mt-3">
                    <a href="/support" class="inline-flex items-center px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700 transition-colors">
                        <i class="fas fa-heart mr-2"></i>
                        Support Now
                    </a>
                    <a href="https://ko-fi.com/webaudio" target="_blank" class="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
                        <i class="fas fa-coffee mr-2"></i>
                        Ko-fi
                    </a>
                </div>
            ` : '';

            return `
                <div class="border-t border-theme mt-6 pt-4">
                    <div class="benefit-subtitle mb-2">Trial Period</div>
                    <div class="grid grid-cols-2 gap-4 text-sm mb-4">
                        <div>
                            <span class="benefit-subtitle">Trial Started:</span>
                            <span>${formatDateTime(b.trial_started_at)}</span>
                        </div>
                        <div>
                            <span class="benefit-subtitle">Trial Expires:</span>
                            <span class="${b.trial_hours_remaining && b.trial_hours_remaining < 6 ? 'text-red-500 font-semibold' : (b.trial_hours_remaining && b.trial_hours_remaining < 24 ? 'text-yellow-500 font-medium' : '')}">
                                ${formatDateTime(b.trial_expires_at)}
                            </span>
                        </div>
                    </div>

                    <div class="trial-info ${urgencyClass}">
                        <div class="flex items-center gap-3 mb-3">
                            <i class="fas fa-hourglass-half ${b.trial_hours_remaining < 6 ? 'text-red-500' : (b.trial_hours_remaining < 24 ? 'text-yellow-500' : 'text-purple-500')} text-xl"></i>
                            <div>
                                <div class="trial-time-remaining ${b.trial_hours_remaining < 6 ? 'text-red-500' : (b.trial_hours_remaining < 24 ? 'text-yellow-500' : 'text-purple-500')}">
                                    ${timeLabel}
                                </div>
                                <div class="text-sm opacity-75 mt-1">
                                    ${timeMessage}
                                </div>
                            </div>
                        </div>
                        ${supportButtons}
                    </div>
                </div>
            `;
        };

        const getPeriodSection = () => {
            if (!b.period_start || !b.next_reset || b.is_unlimited || (b.tier_info?.service_type === 'Guest Trial')) return '';

            let serviceMsg = '';
            if (b.tier_info?.service_type && b.tier_info.service_type !== 'Team') {
                const bgColor = b.tier_info.service_type === 'Ko-fi' ? 'rgba(41, 171, 224, 0.1)' : 'rgba(249, 104, 84, 0.1)';
                const icon = b.tier_info.service_type === 'Ko-fi' ? 'fa-coffee' : 'fab fa-patreon';
                const text = b.tier_info.service_type === 'Ko-fi'
                    ? 'Thank you for your Ko-fi support! Your benefits are active until the reset date.'
                    : 'Thank you for your Patreon support! Your benefits will reset on your next billing date.';

                serviceMsg = `
                    <div class="mt-4 text-sm p-2 rounded-lg"
                         style="background-color: ${bgColor}">
                        <i class="fas ${icon}"></i>
                        ${text}
                    </div>
                `;
            }

            return `
                <div class="border-t border-theme mt-6 pt-4">
                    <div class="benefit-subtitle mb-2">Subscription Period</div>
                    <div class="grid grid-cols-2 gap-4 text-sm">
                        <div>
                            <span class="benefit-subtitle">Started:</span>
                            <span>${formatDate(b.period_start)}</span>
                        </div>
                        <div>
                            <span class="benefit-subtitle">Resets:</span>
                            <span>${formatDate(b.next_reset)}</span>
                        </div>
                    </div>
                    ${serviceMsg}
                </div>
            `;
        };

        const getPatronStatus = () => {
            if (!b.tier_info?.patron_status || b.tier_info.service_type === 'Guest Trial') return '';

            return `
                <div class="border-t border-theme mt-6 pt-4">
                    <div class="flex items-center justify-between">
                        <span class="benefit-subtitle">Patron Status</span>
                        <span class="font-medium capitalize">
                            ${b.tier_info.patron_status.replace(/_/g, ' ')}
                        </span>
                    </div>
                </div>
            `;
        };

        // Return HTML without inline CSS (now in separate file)
        return `
            <div class="container mx-auto px-4 sm:px-6 lg:px-8 py-8">
                <!-- Support Tier Card -->
                <div class="benefit-card rounded-xl shadow-md overflow-hidden p-6">
                    <!-- Header Section -->
                    <div class="flex items-center justify-between border-b border-theme pb-4 mb-4">
                        <div>
                            <h2 class="text-3xl font-bold benefit-title">
                                ${b.tier_title}
                                ${getServiceBadge()}
                            </h2>
                            <p class="mt-1 benefit-subtitle text-sm">
                                ${b.is_unlimited ? 'Unlimited Access' :
                                  (b.tier_info?.service_type === 'Guest Trial' ? (b.trial_active ? 'Trial Access - Limited Time' : 'Trial Expired') :
                                  (b.next_reset ? `Resets on ${formatDate(b.next_reset)}` : 'Monthly subscription'))}
                            </p>
                        </div>
                        ${getStatusIndicator()}
                    </div>

                    <!-- Enhanced Grace Period Message -->
                    ${getGracePeriodBanner()}

                    <!-- Stats Section -->
                    <div class="stats-grid">
                        <!-- Album Downloads -->
                        <div class="benefit-section rounded-lg p-4">
                            <div class="flex items-center justify-between mb-3">
                                <h3 class="text-lg font-medium">Albums</h3>
                                <span class="text-2xl font-bold stat-value-primary">
                                    ${b.is_unlimited ? '∞' : b.album_downloads.downloads_remaining}
                                </span>
                            </div>
                            <p class="text-sm benefit-subtitle">
                                Used: ${b.album_downloads.downloads_used} /
                                Total: ${b.is_unlimited ? 'Unlimited' : b.album_downloads.downloads_allowed}
                            </p>
                        </div>

                        <!-- Track Downloads -->
                        <div class="benefit-section rounded-lg p-4">
                            <div class="flex items-center justify-between mb-3">
                                <h3 class="text-lg font-medium">Tracks</h3>
                                <span class="text-2xl font-bold stat-value-success">
                                    ${b.is_unlimited ? '∞' : b.track_downloads.downloads_remaining}
                                </span>
                            </div>
                            <p class="text-sm benefit-subtitle">
                                Used: ${b.track_downloads.downloads_used} /
                                Total: ${b.is_unlimited ? 'Unlimited' : b.track_downloads.downloads_allowed}
                            </p>
                        </div>

                        <!-- Book Requests -->
                        <div class="benefit-section rounded-lg p-4">
                            <div class="flex items-center justify-between mb-3">
                                <h3 class="text-lg font-medium">Book Requests</h3>
                                <span class="text-2xl font-bold stat-value-chapters">
                                    ${b.is_unlimited ? '∞' : b.book_requests.requests_remaining}
                                </span>
                            </div>
                            <p class="text-sm benefit-subtitle">
                                Used: ${b.book_requests.requests_used} /
                                Total: ${b.is_unlimited ? 'Unlimited' : b.book_requests.requests_allowed}
                            </p>
                            ${b.book_requests.requests_remaining > 0 ? `
                                <div class="mt-2">
                                    <a href="/my-book-requests" class="inline-block px-3 py-1 text-xs rounded-full text-white" style="background-color: #9c59b6;">
                                        <i class="fas fa-book"></i> Make a Request
                                    </a>
                                </div>
                            ` : ''}
                        </div>

                        <!-- Chapters Allowed -->
                        <div class="benefit-section rounded-lg p-4">
                            <div class="flex items-center justify-between mb-3">
                                <h3 class="text-lg font-medium">Chapters Allowed</h3>
                                <span class="text-2xl font-bold stat-value-chapters">
                                    ${b.chapters_allowed === undefined ? '?' : (b.is_unlimited || b.chapters_allowed >= 1000000 ? '∞' : b.chapters_allowed)}
                                </span>
                            </div>
                            <p class="text-sm benefit-subtitle">
                                ${b.chapters_allowed === undefined ? 'Chapter limit not configured' :
                                  (b.is_unlimited || b.chapters_allowed >= 1000000 ? 'Unlimited chapters per request' :
                                  (b.chapters_allowed > 0 ? 'Maximum chapters per book request' : 'No chapter limit set'))}
                            </p>
                            ${b.chapters_allowed !== undefined && b.chapters_allowed > 0 && b.chapters_allowed < 1000000 ? `
                                <div class="chapters-info">
                                    <i class="fas fa-file-alt"></i>
                                    ${b.chapters_allowed} chapter${b.chapters_allowed != 1 ? 's' : ''} max per request
                                </div>
                            ` : ((b.is_unlimited || (b.chapters_allowed !== undefined && b.chapters_allowed >= 1000000)) ? `
                                <div class="chapters-info">
                                    <i class="fas fa-infinity"></i>
                                    Unlimited chapters per request
                                </div>
                            ` : '')}
                        </div>
                    </div>

                    <!-- Patron Status -->
                    ${getPatronStatus()}

                    <!-- Trial Period Information for Guest Trials -->
                    ${getTrialSection()}

                    <!-- Period Information for Regular Subscribers -->
                    ${getPeriodSection()}
                </div>
            </div>
        `;
    }

    errorHTML(message) {
        return `
            <div style="max-width: 1200px; margin: 0 auto; padding: 32px 24px;">
                <div style="background-color: var(--custom-bg-secondary); border-radius: 0.75rem; padding: 40px; text-align: center;">
                    <i class="fas fa-exclamation-circle" style="font-size: 2rem; color: #ef4444; margin-bottom: 16px; display: block;"></i>
                    <h2 style="color: var(--custom-text-primary); margin: 0 0 8px 0;">Error Loading Benefits</h2>
                    <p style="color: var(--custom-text-secondary); margin: 0;">${message}</p>
                    <button onclick="location.reload()" style="margin-top: 20px; padding: 10px 20px; background-color: var(--custom-blue); color: white; border: none; border-radius: 0.375rem; cursor: pointer; font-weight: 500;">
                        Reload Page
                    </button>
                </div>
            </div>
        `;
    }

    updateActiveLinks(path) {
        document.querySelectorAll('.nav-link, .side-nav-item, .dropdown-item').forEach(link => {
            const href = link.getAttribute('href');
            if (href === path) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });
    }

    async destroy() {
        console.log('🧹 MyBenefits: Destroying...');
        this.benefitsData = null;
        return Promise.resolve();
    }
}
