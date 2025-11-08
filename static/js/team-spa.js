// team-spa.js - SPA wrapper for Team Management

// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { TeamManagementController } = await import(`./team-shared-spa.js?v=${v}`);

export class TeamSPA {
    constructor() {
        this.controller = new TeamManagementController('spa');
    }

    getRequiredStyles() {
        const v = window.spaRouter?.cacheVersion || Date.now();
        return [`/static/css/team.css?v=${v}`];
    }

    getPageTitle() {
        return 'Team';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        window.teamManagement = this.controller;
    }

    async destroy() {
        delete window.teamManagement;
        return await this.controller.destroy();
    }
}