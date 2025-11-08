// manage-book-requests-spa.js - SPA wrapper for Manage Book Requests Page
// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { ManageBookRequestsController } = await import(`./manage-book-requests-shared-spa.js?v=${v}`);

export class ManageBookRequestsSPA {
    constructor() {
        this.controller = new ManageBookRequestsController('spa');
    }

    getRequiredStyles() {
        return ['/static/css/manage-book-requests.css'];
    }

    getPageTitle() {
        return 'Manage Book Requests';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        window.manageBookRequestsController = this.controller;
    }

    async destroy() {
        delete window.manageBookRequestsController;
        return await this.controller.destroy();
    }
}
