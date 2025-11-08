// team-shared-spa.js - Universal controller for team management (SSR and SPA modes)

export class TeamManagementController {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.teamMembersData = [];
        this.teamStatusData = {};
        this.statusUpdateInterval = null;
        this.currentEditingMemberId = null;
    }

    // ✅ For SPA mode: generate HTML
    async render() {
        if (this.mode === 'ssr') {
            throw new Error('render() should not be called in SSR mode');
        }
        
        // Fetch data for SPA mode
        await this.loadTeamMembers();
        
        return this.generateHTML();
    }

    // ✅ For both modes: attach event listeners and initialize
    async mount() {
        console.log(`👥 TeamManagement: Mounting in ${this.mode} mode...`);
        
        if (this.mode === 'ssr') {
            // SSR: Read bootstrap data from DOM if available
            this.hydrateFromDOM();
            // Render the hydrated data immediately
            if (this.teamMembersData.length > 0) {
                this.renderTeamMembers(this.teamMembersData);
            }
        }
        
        // Load/refresh data (includes online status now)
        this.loadTeamMembers().catch(err => {
            console.error('Failed to load team members:', err);
            this.renderEmptyTeamGrid(err.message);
        });
        
        // Setup event handlers and start status updates
        this.setupEventListeners();
        // ✅ Update status every 30 seconds (still fetches fresh data)
        this.statusUpdateInterval = setInterval(() => this.loadTeamMembers(), 30000);
        
        console.log('✅ TeamManagement: Mounted successfully');
    }


    // ✅ Read data from DOM (SSR mode)
    hydrateFromDOM() {
        const bootstrapScript = document.getElementById('team-bootstrap-data');
        if (bootstrapScript) {
            try {
                const data = JSON.parse(bootstrapScript.textContent);
                this.teamMembersData = data.members || [];
                console.log('📦 Hydrated team data from DOM:', this.teamMembersData);
            } catch (error) {
                console.error('Error parsing bootstrap data:', error);
            }
        }
    }

    // ✅ Generate HTML for SPA mode
    generateHTML() {
        return `
            <main class="team-management">
                <section class="team-header">
                    <h2>Team Management</h2>
                    <button class="add-team-btn" onclick="teamManagement.openAddTeamModal()">
                        <i class="fas fa-plus"></i> Add Team Member
                    </button>
                </section>

                <section id="teamMembersGrid" class="team-grid">
                    <div class="empty-collection">
                        <i class="fas fa-spinner fa-spin"></i>
                        <h2>Loading Team Members</h2>
                        <p>Please wait while we load your team members...</p>
                    </div>
                </section>
            </main>

            ${this.generateModalsHTML()}
        `;
    }

    // ✅ Generate modals HTML
    generateModalsHTML() {
        return `
            <!-- Add Team Member Modal -->
            <div id="addTeamModal" class="modal">
                <div class="modal-content">
                    <div class="modal-header">
                        <h2>Add Team Member</h2>
                        <button class="modal-close" onclick="teamManagement.closeAddTeamModal()">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    
                    <form id="addTeamForm" onsubmit="return false;">
                        <div class="form-group">
                            <label for="teamEmail">Email</label>
                            <div class="input-wrapper">
                                <i class="fas fa-envelope"></i>
                                <input type="email" id="teamEmail" placeholder="Enter email address" required>
                            </div>
                        </div>

                        <div class="form-group">
                            <label for="teamUsername">Username</label>
                            <div class="input-wrapper">
                                <i class="fas fa-user"></i>
                                <input type="text" id="teamUsername" placeholder="Enter username" required>
                            </div>
                        </div>

                        <div class="form-group">
                            <label for="teamPassword">Password</label>
                            <div class="input-wrapper">
                                <i class="fas fa-lock"></i>
                                <input type="password" id="teamPassword" placeholder="Enter password" required>
                            </div>
                        </div>

                        <div class="modal-footer">
                            <button type="button" class="btn-cancel" onclick="teamManagement.closeAddTeamModal()">
                                Cancel
                            </button>
                            <button type="button" class="btn-primary" onclick="teamManagement.addTeamMember()">
                                <i class="fas fa-plus"></i>
                                Add Member
                            </button>
                        </div>
                    </form>
                </div>
            </div>

            <!-- Edit Team Member Details Modal -->
            <div id="editDetailsModal" class="modal">
                <div class="modal-content">
                    <div class="modal-header">
                        <h2>Edit Team Member Details</h2>
                        <button class="modal-close" onclick="teamManagement.closeEditDetailsModal()">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    
                    <form id="editDetailsForm">
                        <div class="form-group">
                            <label for="editEmail">Email</label>
                            <div class="input-wrapper">
                                <i class="fas fa-envelope"></i>
                                <input type="email" id="editEmail" placeholder="Enter new email">
                            </div>
                        </div>

                        <div class="form-group">
                            <label for="editUsername">Username</label>
                            <div class="input-wrapper">
                                <i class="fas fa-user"></i>
                                <input type="text" id="editUsername" placeholder="Enter new username">
                            </div>
                        </div>

                        <div class="form-group">
                            <label for="editPassword">New Password</label>
                            <div class="input-wrapper">
                                <i class="fas fa-lock"></i>
                                <input type="password" id="editPassword" placeholder="Enter new password (optional)">
                            </div>
                            <small>Leave blank to keep current password</small>
                        </div>

                        <div class="modal-footer">
                            <button type="button" class="btn-cancel" onclick="teamManagement.closeEditDetailsModal()">
                                Cancel
                            </button>
                            <button type="button" class="btn-primary" onclick="teamManagement.saveTeamDetails()">
                                <i class="fas fa-save"></i>
                                Save Changes
                            </button>
                        </div>
                    </form>
                </div>
            </div>

            <!-- Edit Team Permissions Modal -->
            <div id="editPermissionsModal" class="modal">
                <div class="modal-content">
                    <div class="modal-header">
                        <h2>Edit Team Permissions</h2>
                        <button class="modal-close" onclick="teamManagement.closeEditPermissionsModal()">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    
                    <form id="editPermissionsForm">
                        <div class="permissions-grid">
                            <div class="permission-section">
                                <h4><i class="fas fa-download"></i> Download Permissions</h4>
                                <div class="form-group">
                                    <label>Album Downloads (per month)</label>
                                    <input type="number" id="albumDownloadsAllowed" min="0" max="100" value="0">
                                    <small>Number of albums this member can download per month</small>
                                </div>
                                
                                <div class="form-group">
                                    <label>Track Downloads (per month)</label>
                                    <input type="number" id="trackDownloadsAllowed" min="0" max="500" value="0">
                                    <small>Number of tracks this member can download per month</small>
                                </div>
                            </div>

                            <div class="permission-section">
                                <h4><i class="fas fa-book"></i> Book Request Permissions</h4>
                                <div class="form-group">
                                    <label>Book Requests (per month)</label>
                                    <input type="number" id="bookRequestsAllowed" min="0" max="20" value="0">
                                    <small>Number of book requests this member can make per month</small>
                                </div>
                            </div>

                            <div class="permission-section">
                                <h4><i class="fas fa-trash-alt"></i> Deletion Permissions</h4>
                                <div class="form-group">
                                    <label>Album Deletions (per 24 hours)</label>
                                    <input type="number" id="albumDeletionsAllowed" min="0" max="10" value="0">
                                    <small>Number of albums this member can delete every 24 hours</small>
                                </div>
                                
                                <div class="form-group">
                                    <label>Track Deletions (per 24 hours)</label>
                                    <input type="number" id="trackDeletionsAllowed" min="0" max="50" value="0">
                                    <small>Number of tracks this member can delete every 24 hours</small>
                                </div>
                            </div>
                        </div>
                        
                        <div id="currentUsage" class="usage-info"></div>
                        
                        <div class="modal-footer">
                            <button type="button" class="btn-cancel" onclick="teamManagement.closeEditPermissionsModal()">Cancel</button>
                            <button type="button" class="btn-primary" onclick="teamManagement.saveTeamPermissions()">
                                <i class="fas fa-save"></i> Save Permissions
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        `;
    }

    async loadTeamMembers() {
        try {
            const response = await fetch('/api/team/members');
            if (!response.ok) throw new Error('Failed to load team members');
            
            this.teamMembersData = await response.json();
            console.log('Loaded team members with online status:', this.teamMembersData);
            
            // ✅ Data already includes is_online, just render
            this.renderTeamMembers(this.teamMembersData);
        } catch (error) {
            console.error('Error loading team members:', error);
            this.showError('Failed to load team members');
            this.renderEmptyTeamGrid(error.message);
        }
    }

    
    renderTeamMembers(members) {
        const grid = document.getElementById('teamMembersGrid');
        if (!grid) return;
        
        if (!members || members.length === 0) {
            this.renderEmptyTeamGrid();
            return;
        }

        grid.innerHTML = members.map(member => `
            <div class="team-card" data-member-id="${member.id}">
                <div class="team-header">
                    <div class="team-info">
                        <h3>${this.escapeHtml(member.username)}</h3>
                        <p>${this.escapeHtml(member.email)}</p>
                    </div>
                    <div class="team-actions">
                        <button onclick="teamManagement.editTeamDetails('${member.id}')" class="btn-details" title="Edit details">
                            <i class="fas fa-user-edit"></i>
                        </button>
                        <button onclick="teamManagement.editTeamPermissions('${member.id}')" class="btn-edit" title="Edit permissions">
                            <i class="fas fa-cog"></i>
                        </button>
                        <button onclick="teamManagement.deleteTeamMember('${member.id}')" class="btn-danger" title="Delete member">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </div>
                <div class="team-details">
                    <p><i class="fas fa-download"></i> Downloads: ${member.album_downloads_allowed || 0} albums, ${member.track_downloads_allowed || 0} tracks</p>
                    
                    <div class="book-requests-info">
                        <h5><i class="fas fa-book"></i> Book Request Permissions (Monthly)</h5>
                        <p><i class="fas fa-book-open"></i> Requests: ${member.book_requests_used || 0}/${member.book_requests_allowed || 0} used</p>
                        <p><i class="fas fa-clock"></i> Remaining: ${member.book_requests_remaining || 0} requests</p>
                    </div>
                    
                    <div class="deletions-info">
                        <h5><i class="fas fa-trash-alt"></i> Deletion Permissions (24hr cycle)</h5>
                        <p><i class="fas fa-compact-disc"></i> Albums: ${member.album_deletions_used || 0}/${member.album_deletions_allowed || 0} used</p>
                        <p><i class="fas fa-music"></i> Tracks: ${member.track_deletions_used || 0}/${member.track_deletions_allowed || 0} used</p>
                        <p><i class="fas fa-clock"></i> Remaining: ${member.album_deletions_remaining || 0} albums, ${member.track_deletions_remaining || 0} tracks</p>
                    </div>
                    
                    <p><i class="fas fa-clock"></i> Last Login: ${member.last_login ? new Date(member.last_login).toLocaleString() : 'Never'}</p>
                    <p><i class="fas fa-circle ${member.is_online ? 'status-active' : 'status-inactive'}"></i> Status: 
                        <span class="status-badge ${member.is_online ? 'online' : 'offline'}">
                            ${member.is_online ? 'Online' : 'Offline'}
                        </span>
                    </p>
                </div>
            </div>
        `).join('');
    }

    renderEmptyTeamGrid(errorMessage = null) {
        const grid = document.getElementById('teamMembersGrid');
        if (!grid) return;
        
        grid.innerHTML = `
            <div class="empty-collection">
                ${errorMessage ? `<i class="fas fa-exclamation-circle"></i>` : `<i class="fas fa-users"></i>`}
                <h2>${errorMessage ? 'Error Loading Team Members' : 'No Team Members Found'}</h2>
                <p>${errorMessage || 'Start adding team members to collaborate and manage your content.'}</p>
                <button class="btn-primary" onclick="${errorMessage ? 'location.reload()' : 'teamManagement.openAddTeamModal()'}">
                    <i class="fas fa-${errorMessage ? 'sync' : 'plus'}"></i> 
                    ${errorMessage ? 'Refresh Page' : 'Add Team Member'}
                </button>
            </div>
        `;
    }

    async addTeamMember() {
        const email = document.getElementById('teamEmail').value;
        const username = document.getElementById('teamUsername').value;
        const password = document.getElementById('teamPassword').value;

        try {
            const formData = new FormData();
            formData.append('email', email);
            formData.append('username', username);
            formData.append('password', password);

            const response = await fetch('/creator/add-team', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to add team member');
            }

            this.closeAddTeamModal();
            await this.loadTeamMembers();
            this.showToast('Team member added successfully');
            
        } catch (error) {
            console.error('Error:', error);
            this.showError(error.message);
        }
    }

    async deleteTeamMember(memberId) {
        const member = this.teamMembersData.find(m => m.id == memberId);
        if (!member) return;
        
        if (!confirm(`Are you sure you want to delete team member "${member.username}"?`)) return;

        try {
            const response = await fetch(`/api/team/members/${memberId}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to delete team member');
            }

            await this.loadTeamMembers();
            this.showToast('Team member deleted successfully');
            
        } catch (error) {
            console.error('Error:', error);
            this.showError(error.message);
        }
    }

    editTeamDetails(memberId) {
        this.currentEditingMemberId = memberId;
        const member = this.teamMembersData.find(m => m.id == memberId);
        
        if (member) {
            document.getElementById('editEmail').value = member.email || '';
            document.getElementById('editUsername').value = member.username || '';
            document.getElementById('editPassword').value = '';
            
            document.getElementById('editDetailsModal').classList.add('active');
        }
    }

    closeEditDetailsModal() {
        document.getElementById('editDetailsModal').classList.remove('active');
        document.getElementById('editDetailsForm').reset();
        this.currentEditingMemberId = null;
    }

    async saveTeamDetails() {
        if (!this.currentEditingMemberId) return;
        
        try {
            const email = document.getElementById('editEmail').value.trim();
            const username = document.getElementById('editUsername').value.trim();
            const password = document.getElementById('editPassword').value.trim();
            
            const updateData = {};
            if (email) updateData.email = email;
            if (username) updateData.username = username;
            if (password) updateData.password = password;
            
            if (Object.keys(updateData).length === 0) {
                this.showError('Please provide at least one field to update');
                return;
            }
            
            const response = await fetch(`/api/team/members/${this.currentEditingMemberId}/details`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updateData)
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to update team member details');
            }

            const result = await response.json();
            this.closeEditDetailsModal();
            await this.loadTeamMembers();
            this.showToast(result.message);
            
        } catch (error) {
            console.error('Error:', error);
            this.showError(error.message);
        }
    }

    editTeamPermissions(memberId) {
        this.currentEditingMemberId = memberId;
        const member = this.teamMembersData.find(m => m.id == memberId);
        
        if (member) {
            document.getElementById('albumDownloadsAllowed').value = member.album_downloads_allowed || 0;
            document.getElementById('trackDownloadsAllowed').value = member.track_downloads_allowed || 0;
            document.getElementById('bookRequestsAllowed').value = member.book_requests_allowed || 0;
            document.getElementById('albumDeletionsAllowed').value = member.album_deletions_allowed || 0;
            document.getElementById('trackDeletionsAllowed').value = member.track_deletions_allowed || 0;
            
            document.getElementById('currentUsage').innerHTML = `
                <h4><i class="fas fa-chart-bar"></i> Current Usage</h4>
                <div class="permissions-grid">
                    <div>
                        <p><strong>Downloads (Monthly):</strong></p>
                        <p>Albums: ${member.album_downloads_used || 0}/${member.album_downloads_allowed || 0}</p>
                        <p>Tracks: ${member.track_downloads_used || 0}/${member.track_downloads_allowed || 0}</p>
                    </div>
                    <div>
                        <p><strong>Book Requests (Monthly):</strong></p>
                        <p>Requests: ${member.book_requests_used || 0}/${member.book_requests_allowed || 0}</p>
                        <p style="color: var(--primary-color);">Remaining: ${member.book_requests_remaining || 0} requests</p>
                    </div>
                    <div>
                        <p><strong>Deletions (24hr):</strong></p>
                        <p>Albums: ${member.album_deletions_used || 0}/${member.album_deletions_allowed || 0}</p>
                        <p>Tracks: ${member.track_deletions_used || 0}/${member.track_deletions_allowed || 0}</p>
                        <p style="color: var(--primary-color);">Remaining: ${member.album_deletions_remaining || 0} albums, ${member.track_deletions_remaining || 0} tracks</p>
                    </div>
                </div>
            `;
            
            document.getElementById('editPermissionsModal').classList.add('active');
        }
    }

    closeEditPermissionsModal() {
        document.getElementById('editPermissionsModal').classList.remove('active');
        document.getElementById('editPermissionsForm').reset();
        this.currentEditingMemberId = null;
    }

    async saveTeamPermissions() {
        if (!this.currentEditingMemberId) return;
        
        try {
            const albumDownloadsAllowed = parseInt(document.getElementById('albumDownloadsAllowed').value) || 0;
            const trackDownloadsAllowed = parseInt(document.getElementById('trackDownloadsAllowed').value) || 0;
            const bookRequestsAllowed = parseInt(document.getElementById('bookRequestsAllowed').value) || 0;
            const albumDeletionsAllowed = parseInt(document.getElementById('albumDeletionsAllowed').value) || 0;
            const trackDeletionsAllowed = parseInt(document.getElementById('trackDeletionsAllowed').value) || 0;
            
            const response = await fetch(`/api/team/members/${this.currentEditingMemberId}/permissions`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    album_downloads_allowed: albumDownloadsAllowed,
                    track_downloads_allowed: trackDownloadsAllowed,
                    book_requests_allowed: bookRequestsAllowed,
                    album_deletions_allowed: albumDeletionsAllowed,
                    track_deletions_allowed: trackDeletionsAllowed
                })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to update permissions');
            }

            const result = await response.json();
            this.closeEditPermissionsModal();
            await this.loadTeamMembers();
            this.showToast(result.message);
            
        } catch (error) {
            console.error('Error:', error);
            this.showError(error.message);
        }
    }

    openAddTeamModal() {
        document.getElementById('addTeamModal').classList.add('active');
    }

    closeAddTeamModal() {
        document.getElementById('addTeamModal').classList.remove('active');
        document.getElementById('addTeamForm').reset();
    }

    showError(message) {
        const toast = document.createElement('div');
        toast.className = 'error-toast';
        toast.innerHTML = `
            <div class="error-content">
                <i class="fas fa-exclamation-circle"></i>
                <span>${message}</span>
            </div>
        `;
        document.body.appendChild(toast);

        setTimeout(() => toast.remove(), 5000);
    }

    showToast(message) {
        const toast = document.createElement('div');
        toast.className = 'error-toast';
        toast.style.borderLeftColor = '#38a169';
        toast.innerHTML = `
            <div class="error-content">
                <i class="fas fa-check-circle" style="color: #38a169;"></i>
                <span>${message}</span>
            </div>
        `;
        document.body.appendChild(toast);

        setTimeout(() => toast.remove(), 3000);
    }

    setupEventListeners() {
        window.onclick = (event) => {
            const modals = ['addTeamModal', 'editDetailsModal', 'editPermissionsModal'];
            modals.forEach(modalId => {
                const modal = document.getElementById(modalId);
                if (event.target === modal) {
                    modal.classList.remove('active');
                    if (modalId === 'editDetailsModal') this.closeEditDetailsModal();
                    else if (modalId === 'editPermissionsModal') this.closeEditPermissionsModal();
                    else if (modalId === 'addTeamModal') this.closeAddTeamModal();
                }
            });
        };
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
        console.log('👥 TeamManagement: Destroying...');
        if (this.statusUpdateInterval) {
            clearInterval(this.statusUpdateInterval);
        }
        return Promise.resolve();
    }
}

// Make it available globally for inline onclick handlers
window.teamManagement = null;