// English is the source of truth for translation keys.
// hi.ts is typed as `typeof en`, so a missing or extra key fails `tsc`.
export const en = {
  app: {
    name: 'Kirana Store',
    tagline: 'Shop management',
  },
  nav: {
    dashboard: 'Dashboard',
    products: 'Products',
    inventory: 'Inventory',
    purchases: 'Purchases',
    sales: 'Sales',
    customers: 'Customers (Khata)',
    suppliers: 'Suppliers',
    expenses: 'Expenses',
    reports: 'Reports',
  },
  modules: {
    products: {
      description: 'Manage your product list, prices, MRP and reorder levels.',
    },
    inventory: {
      description:
        'See current stock, low-stock alerts and the full history of every stock movement.',
    },
    purchases: {
      description: 'Record incoming stock from suppliers and handle purchase returns.',
    },
    sales: {
      description: 'Make detailed bills or quick daily sales entries, and handle sales returns.',
    },
    customers: {
      description: 'Keep track of customers and their khata (credit) balances.',
    },
    suppliers: {
      description: 'Maintain supplier details and see what you buy from whom.',
    },
    expenses: {
      description: 'Record daily shop expenses such as rent, electricity and salaries.',
    },
    reports: {
      description: 'Sales, purchase, stock and profit-estimate reports with CSV/Excel export.',
    },
  },
  layout: {
    openMenu: 'Open menu',
    closeMenu: 'Close menu',
    mainNavigation: 'Main navigation',
    language: 'Language',
  },
  status: {
    checking: 'Checking server…',
    online: 'Server connected',
    offline: 'Server not reachable',
  },
  comingSoon: {
    badge: 'Planned for Phase {{phase}}',
    message: 'This module has not been built yet. It arrives in Phase {{phase}} of the roadmap.',
  },
  dashboard: {
    title: 'Dashboard',
    subtitle: "A quick view of your shop's day.",
    foundationNotice:
      'The project foundation is ready. Figures will appear here as each module is built.',
    availableFrom: 'Available from Phase {{phase}}',
    kpi: {
      todaysSales: "Today's Sales",
      todaysPurchases: "Today's Purchases",
      todaysExpenses: "Today's Expenses",
      estimatedProfit: 'Estimated Profit',
      totalProducts: 'Total Products',
      stockValue: 'Stock Value',
      lowStock: 'Low Stock Products',
      outOfStock: 'Out of Stock Products',
    },
    panels: {
      salesTrend: 'Sales trend',
      lowStockList: 'Low stock products',
    },
  },
  notFound: {
    title: 'Page not found',
    message: 'The page you are looking for does not exist.',
    backToDashboard: 'Go to dashboard',
  },
}

export type Translations = typeof en
