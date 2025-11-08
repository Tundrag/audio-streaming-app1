/**
 * forum-thread-settings.js - Thread Settings Module
 * Handles: Tier system, thread settings management, access control
 */

class ForumThreadSettings {
    constructor() {
        // Tier system data
        this.availableTiers = [];
        this.userTierInfo = null;
        
        // Reference to ForumCore instance
        this.forum = null;
        
        this.init();
    }
    attachForum(forumInstance) {
        console.log('🔗 ForumThreadSettings: Attaching new ForumCore instance');
        this.forum = forumInstance;
        
        // Optional: Re-initialize if needed when new forum attaches
        if (forumInstance.currentUser?.is_creator && !this.availableTiers?.length) {
            this.loadTierData();
        }
    }

    async init() {
        console.log('🔧 ForumThreadSettings: Initializing...');
        
        // Wait for ForumCore to be available
        if (window.forum) {
            this.attachForum(window.forum);
            
            // Load tier data if user is creator
            if (this.forum.currentUser.is_creator) {
                await this.loadTierData();
            }
        } else {
            // Only retry a few times, then give up
            // ForumCore will call attachForum when it's ready
            let retries = 0;
            const retryInit = () => {
                if (window.forum) {
                    this.attachForum(window.forum);
                    if (this.forum.currentUser.is_creator) {
                        this.loadTierData();
                    }
                } else if (retries < 5) {
                    retries++;
                    setTimeout(retryInit, 100);
                } else {
                    console.log('⚠️ ForumThreadSettings: Giving up on initial attachment, will wait for ForumCore to attach us');
                }
            };
            setTimeout(retryInit, 100);
        }
    }

    // ================== TIER SYSTEM ==================

    async loadTierData() {
        try {
            // Load available tiers for dropdowns
            const tiersResponse = await fetch('/api/forum/tiers/available');
            if (tiersResponse.ok) {
                const tiersData = await tiersResponse.json();
                this.availableTiers = tiersData.tiers || [];
            }
            
            // Load user tier info
            const userTierResponse = await fetch('/api/forum/user-tier-info');
            if (userTierResponse.ok) {
                this.userTierInfo = await userTierResponse.json();
            }
            
            console.log('✅ Tier data loaded:', {
                availableTiers: this.availableTiers.length,
                userTierInfo: this.userTierInfo
            });
            
        } catch (error) {
            console.error('Error loading tier data:', error);
        }
    }

    renderTierSelector(selectedTierId = null) {
        // Always start with free access option
        let options = '<option value="">Free Access</option>';
        
        if (this.availableTiers && this.availableTiers.length > 0) {
            options += this.availableTiers.map(tier => {
                const selected = selectedTierId === tier.id ? 'selected' : '';
                const priceText = tier.amount_cents > 0 ? ` ($${(tier.amount_cents / 100).toFixed(2)}+)` : '';
                // Ensure we use tier.id and not empty string
                const tierValue = tier.id || '';
                console.log('🎯 Rendering tier option:', { id: tier.id, title: tier.title, value: tierValue });
                return `<option value="${tierValue}" ${selected}>${tier.title}${priceText}</option>`;
            }).join('');
        }
        
        console.log('✅ Tier selector rendered with', this.availableTiers?.length || 0, 'tiers');
        return options;
    }
    renderTierBadge(tierInfo) {
        if (!tierInfo || !tierInfo.is_restricted) {
            return '<span class="tier-badge free">Free</span>';
        }
        
        const color = tierInfo.tier_color || '#3b82f6';
        const title = tierInfo.tier_title || `$${(tierInfo.min_tier_cents / 100).toFixed(2)}+`;
        
        return `<span class="tier-badge restricted" style="background-color: ${color};">${title}</span>`;
    }

    async updateThreadTier(threadId, tierId) {
        try {
            const response = await fetch(`/api/forum/threads/${threadId}/tier-access`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ min_tier_id: tierId })
            });
            
            if (!response.ok) {
                throw new Error('Failed to update tier access');
            }
            
            const result = await response.json();
            console.log('✅ Thread tier updated:', result);
            
            // Refresh thread info
            if (this.forum.currentThread && this.forum.currentThread.id === threadId) {
                await this.refreshCurrentThread();
            }
            
            this.forum.showToast('Thread access updated successfully');
            return result;
            
        } catch (error) {
            console.error('Error updating thread tier:', error);
            this.forum.showError('Failed to update thread access');
            throw error;
        }
    }

    async refreshCurrentThread() {
        if (!this.forum.currentThread) return;
        
        try {
            const response = await fetch(`/api/forum/threads/${this.forum.currentThread.id}`);
            if (response.ok) {
                const updatedThread = await response.json();
                this.forum.currentThread = updatedThread;
            }
        } catch (error) {
            console.error('Error refreshing thread:', error);
        }
    }

    // ================== MODAL MANAGEMENT ==================

    async showThreadSettingsModal(forumInstance = null) {
        // Always refresh the forum reference if provided
        if (forumInstance) {
            this.attachForum(forumInstance);
        }
        
        console.log('🔧 Opening thread settings modal...');

        // Check if we have a current thread to update (guard AFTER attaching)
        if (!this.forum?.currentThread) {
            this.forum?.showError('Please open a thread first to access its settings');
            return;
        }

        // Load tiers if needed
        if (!this.availableTiers?.length) {
            await this.loadTierData();
        }

        // Show modal
        this.forum.showModal('threadSettingsModal');

        // Store thread ID in form dataset as backup if we have a current thread
        setTimeout(() => {
            const form = document.getElementById('threadSettingsForm');
            if (form && this.forum?.currentThread) {
                form.dataset.threadId = this.forum.currentThread.id;
            }
            this.populateThreadSettingsModal();
            this.attachFormHandler();
        }, 150);
    }
    // Updated method to handle cases with or without current thread
    populateThreadSettingsModal() {
        console.log('🔍 Populating modal with thread:', this.forum?.currentThread || 'No current thread');
        
        // Set tier selection - handle different possible data structures
        const tierSelect = document.getElementById('settingsTier');
        if (tierSelect) {
            // First populate the options
            tierSelect.innerHTML = this.renderTierSelector();
            
            let currentTierId = null;
            
            // Only try to get current tier if we have a thread
            if (this.forum?.currentThread) {
                // Try different ways to get the current tier ID
                if (this.forum.currentThread.tier_info && this.forum.currentThread.tier_info.tier_id) {
                    currentTierId = this.forum.currentThread.tier_info.tier_id;
                } else if (this.forum.currentThread.min_tier_id) {
                    currentTierId = this.forum.currentThread.min_tier_id;
                } else if (this.forum.currentThread.min_tier_cents > 0) {
                    // Try to find tier by amount if no ID
                    const matchingTier = this.availableTiers.find(tier => 
                        tier.amount_cents === this.forum.currentThread.min_tier_cents
                    );
                    currentTierId = matchingTier ? matchingTier.id : null;
                }
            }
            
            console.log('🔍 Setting tier select to:', currentTierId);
            
            // Set the select value (defaults to free if no tier)
            tierSelect.value = currentTierId || '';
            
            // Update description
            this.updateTierDescription(tierSelect.value, 'settingsTierDescription');
            
            // Add change event listener
            tierSelect.onchange = (e) => {
                this.updateTierDescription(e.target.value, 'settingsTierDescription');
            };
        }
        
        // Set checkboxes with fallback to false
        const pinnedCheckbox = document.getElementById('settingsPinned');
        const lockedCheckbox = document.getElementById('settingsLocked');
        
        if (pinnedCheckbox) {
            pinnedCheckbox.checked = Boolean(this.forum?.currentThread?.is_pinned);
        }
        if (lockedCheckbox) {
            lockedCheckbox.checked = Boolean(this.forum?.currentThread?.is_locked);
        }
        
        // Attach form submit handler
        const form = document.getElementById('threadSettingsForm');
        if (form) {
            form.onsubmit = (e) => this.handleUpdateThread(e);
        }
        
        console.log('✅ Modal populated successfully');
    }

    attachFormHandler() {
        const form = document.getElementById('threadSettingsForm');
        if (form) {
            form.onsubmit = (e) => this.handleUpdateThread(e);
        }
    }

    updateTierDescription(tierId, descriptionId = 'tierDescription') {
        const descriptionEl = document.getElementById(descriptionId);
        if (!descriptionEl) {
            console.warn('⚠️ Tier description element not found:', descriptionId);
            return;
        }
        
        console.log('🔍 Updating tier description for:', tierId);
        
        if (!tierId || tierId === '') {
            descriptionEl.textContent = 'Anyone can access this discussion';
            descriptionEl.className = 'tier-description free';
            return;
        }
        
        const tier = this.availableTiers.find(t => t.id == tierId);
        if (tier) {
            const description = tier.description || `${tier.title} members and above can access`;
            descriptionEl.textContent = description;
            descriptionEl.className = 'tier-description restricted';
            console.log('✅ Tier description updated:', description);
        } else {
            descriptionEl.textContent = 'Tier access required';
            descriptionEl.className = 'tier-description restricted';
            console.warn('⚠️ Tier not found for ID:', tierId);
        }
    }
    // ================== THREAD MANAGEMENT ==================

    async handleUpdateThread(event) {
        event.preventDefault();
        this.attachForum(window.forum);
        
        // Check if we have a current thread to update
        if (!this.forum?.currentThread) {


            this.forum.showError('No thread selected to update settings for');
            return;
        }
        
        const formData = new FormData(event.target);
        const updateData = {
            min_tier_id: document.getElementById('settingsTier').value || null,
            is_pinned: document.getElementById('settingsPinned').checked,
            is_locked: document.getElementById('settingsLocked').checked
        };
        
        console.log('📤 Updating thread with data:', updateData);
        
        try {
            const response = await fetch(`/api/forum/threads/${this.forum.currentThread.id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updateData)
            });
            
            if (!response.ok) {
                throw new Error('Failed to update thread');
            }
            
            console.log('✅ Thread updated');
            
            this.forum.hideModal('threadSettingsModal');
            this.forum.showToast('Thread settings updated successfully!');
            
            // Refresh current thread data
            await this.refreshCurrentThread();
            
            // Re-render the view if available
            if (this.forum.renderDiscussionView) {
                this.forum.renderDiscussionView();
            }
            
        } catch (error) {
            console.error('Error updating thread:', error);
            this.forum.showError('Failed to update thread settings');
        }
    }

    // ================== WEBSOCKET EVENT HANDLER ==================

    handleThreadUpdate(data) {
        console.log('🔄 Thread update received:', data);
        
        if (this.forum.currentThread && this.forum.currentThread.id === data.thread_id) {
            // Update the local thread data with new settings
            if (data.thread) {
                Object.assign(this.forum.currentThread, data.thread);
                
                // Re-render the discussion view to show updated settings
                if (this.forum.renderDiscussionView) {
                    this.forum.renderDiscussionView();
                }
                
                // Show notification about the update
                if (data.updates) {
                    const updateMessages = [];
                    if (data.updates.is_pinned !== undefined) {
                        updateMessages.push(data.updates.is_pinned ? 'Thread pinned' : 'Thread unpinned');
                    }
                    if (data.updates.is_locked !== undefined) {
                        updateMessages.push(data.updates.is_locked ? 'Thread locked' : 'Thread unlocked');
                    }
                    if (data.updates.min_tier_id !== undefined) {
                        updateMessages.push('Thread access level updated');
                    }
                    
                    if (updateMessages.length > 0) {
                        this.forum.showToast(updateMessages.join(', '));
                    }
                }
            }
        }
    }
}

// ================== MODULE INTEGRATION ==================

// Extend ForumCore showModal to handle thread settings
const originalShowModal = ForumCore.prototype.showModal;
ForumCore.prototype.showModal = function(modalId) {
    originalShowModal.call(this, modalId);
    
    // Pre-populate thread settings modal if viewing a thread
    if (modalId === 'threadSettingsModal' && this.currentThread && window.forumThreadSettings) {
        setTimeout(() => {
            window.forumThreadSettings.populateThreadSettingsModal();
        }, 50);
    }
};

// Add method to open thread settings to ForumCore
ForumCore.prototype.openThreadSettings = function() {
    if (window.forumThreadSettings) {
        window.forumThreadSettings.showThreadSettingsModal();
    }
};

// Add reference to thread settings instance to ForumCore
ForumCore.prototype.getThreadSettings = function() {
    return window.forumThreadSettings;
};

// Initialize the thread settings module
document.addEventListener('DOMContentLoaded', () => {
    window.forumThreadSettings = new ForumThreadSettings();
    console.log('✅ Forum Thread Settings module loaded');
    
    // If forum already exists, attach immediately
    if (window.forum) {
        window.forumThreadSettings.attachForum(window.forum);
    }
});

// Export for other modules
window.ForumThreadSettings = ForumThreadSettings;