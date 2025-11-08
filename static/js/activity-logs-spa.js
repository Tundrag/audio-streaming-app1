// activity-logs-spa.js - SPA wrapper for Activity Logs Page
// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { ActivityLogsController } = await import(`./activity-logs-shared-spa.js?v=${v}`);

export class ActivityLogsSPA {
    constructor() {
        this.controller = new ActivityLogsController('spa');
    }

    getRequiredStyles() {
        return ['/static/css/activity-logs.css'];
    }

    getPageTitle() {
        return 'Activity Logs';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        window.activityLogsController = this.controller;
    }

    async destroy() {
        delete window.activityLogsController;
        return await this.controller.destroy();
    }
}
