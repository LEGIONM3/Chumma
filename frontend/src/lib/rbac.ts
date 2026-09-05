export type UserRole = 'ADMIN' | 'SALES_MANAGER' | 'SALES_REP' | 'FINANCE' | 'CUSTOMER';

export interface NavTab {
  label: string;
  path: string;
  allowedRoles: UserRole[];
}

export const INTERNAL_NAV_TABS: NavTab[] = [
  { label: 'Dashboard', path: '/', allowedRoles: ['ADMIN', 'SALES_MANAGER', 'SALES_REP', 'FINANCE'] },
  { label: 'Quotations', path: '/quotations', allowedRoles: ['ADMIN', 'SALES_MANAGER', 'SALES_REP', 'FINANCE'] },
  { label: 'Approvals', path: '/approvals', allowedRoles: ['ADMIN', 'SALES_MANAGER', 'SALES_REP', 'FINANCE'] },
  { label: 'Fulfillment', path: '/fulfillment', allowedRoles: ['ADMIN', 'SALES_MANAGER', 'SALES_REP', 'FINANCE'] },
  { label: 'Subscriptions', path: '/subscriptions', allowedRoles: ['ADMIN', 'SALES_MANAGER', 'SALES_REP', 'FINANCE'] },
  { label: 'Invoices', path: '/invoices', allowedRoles: ['ADMIN', 'SALES_MANAGER', 'SALES_REP', 'FINANCE'] },
  { label: 'Deal Health', path: '/deal-health', allowedRoles: ['ADMIN', 'SALES_MANAGER', 'SALES_REP', 'FINANCE'] },
  { label: 'Reports', path: '/reports', allowedRoles: ['ADMIN', 'SALES_MANAGER', 'SALES_REP', 'FINANCE'] },
  { label: 'Products', path: '/products', allowedRoles: ['ADMIN', 'SALES_MANAGER', 'SALES_REP', 'FINANCE'] },
  { label: 'Config', path: '/config', allowedRoles: ['ADMIN', 'SALES_MANAGER'] },
];

export const PORTAL_NAV_TABS: NavTab[] = [
  { label: 'My Quotation', path: '/portal/quotations', allowedRoles: ['CUSTOMER'] },
  { label: 'Messages', path: '/portal/messages', allowedRoles: ['CUSTOMER'] },
  { label: 'Profile', path: '/portal/profile', allowedRoles: ['CUSTOMER'] },
];

export function canAccessRoute(role: UserRole | undefined, allowedRoles: UserRole[]): boolean {
  if (!role) return false;
  if (role === 'ADMIN') return true;
  return allowedRoles.includes(role);
}
