// my-downloads-spa.js - SPA wrapper for My Downloads Page
// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { MyDownloadsController } = await import(`./my-downloads-shared-spa.js?v=${v}`);

export class MyDownloadsSPA {
    constructor() {
        this.controller = new MyDownloadsController('spa');
    }

    getRequiredStyles() {
        return ['/static/css/my-downloads.css'];
    }

    getPageTitle() {
        return 'My Downloads';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        // Keep both names for compatibility
        window.myDownloadsController = this.controller;
        window.downloadsManager = this.controller;
    }

    async destroy() {
        delete window.myDownloadsController;
        delete window.downloadsManager;
        return await this.controller.destroy();
    }
}
