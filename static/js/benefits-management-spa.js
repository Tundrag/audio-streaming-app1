// /static/js/benefits-management-spa.js

// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { BenefitsManagementController } = await import(`./benefits-management-shared.js?v=${v}`);

export class BenefitsManagementSPA {  // ← Changed from MyBenefitsSPA
    constructor() {
        this.controller = new BenefitsManagementController('spa');
    }
    
    getRequiredStyles() {
        const v = window.spaRouter?.cacheVersion || Date.now();
        return [`/static/css/benefits-management.css?v=${v}`];
    }

    getPageTitle() {
        return 'Benefits Management';
    }

    async render() {
        return await this.controller.render();
    }
    
    async mount() {
        await this.controller.mount();
        window.benefitsController = this.controller;
    }
    
    async destroy() {
        delete window.benefitsController;
        return await this.controller.destroy();
    }
}