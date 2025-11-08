// my-benefits-spa.js - SPA wrapper for My Benefits page
// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { MyBenefitsController } = await import(`./my-benefits-shared-spa.js?v=${v}`);

export class MyBenefitsSPA {
    constructor() {
        this.controller = new MyBenefitsController('spa');
    }

    getRequiredStyles() {
        return ['/static/css/my-benefits.css'];
    }

    getPageTitle() {
        return 'My Benefits';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        window.myBenefitsController = this.controller;
    }

    async destroy() {
        delete window.myBenefitsController;
        return await this.controller.destroy();
    }
}
