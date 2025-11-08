// my-book-requests-spa.js - SPA wrapper for My Book Requests

// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { MyBookRequestsController } = await import(`./my-book-requests-shared-spa.js?v=${v}`);

export class MyBookRequestsSPA {
    constructor() {
        this.controller = new MyBookRequestsController('spa');
    }

    getRequiredStyles() {
        const v = window.spaRouter?.cacheVersion || Date.now();
        return [`/static/css/my-book-requests.css?v=${v}`];
    }

    getPageTitle() {
        return 'My Book Requests';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        window.myBookRequestsController = this.controller;
    }

    async destroy() {
        delete window.myBookRequestsController;
        return await this.controller.destroy();
    }
}
