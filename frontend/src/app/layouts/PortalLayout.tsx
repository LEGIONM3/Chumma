import React from 'react';
import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { ShieldCheck, LogOut, User } from 'lucide-react';
import { usePortalAuthStore } from '@/features/portal/portalAuthStore';
import { PORTAL_NAV_TABS } from '@/lib/rbac';

export const PortalLayout: React.FC = () => {
  const { partner, clearAuth } = usePortalAuthStore();
  const navigate = useNavigate();

  const handleLogout = () => {
    clearAuth();
    navigate('/portal/login');
  };

  return (
    <div className="min-h-screen bg-app flex flex-col">
      <header className="sticky top-0 z-30 flex h-14 w-full items-center justify-between bg-brand px-6 text-brand-ink shadow-md select-none">
        <div className="flex items-center gap-6">
          <div
            onClick={() => navigate('/portal/quotations')}
            className="flex items-center gap-2 cursor-pointer font-extrabold text-base tracking-tight text-brand-ink"
          >
            <ShieldCheck className="h-5 w-5 fill-brand-ink" />
            <span>DealFlow360 Customer Portal</span>
          </div>

          <nav className="flex items-center space-x-1">
            {PORTAL_NAV_TABS.map((tab) => (
              <NavLink
                key={tab.path}
                to={tab.path}
                className={({ isActive }) =>
                  `rounded-chip px-3.5 py-1.5 text-xs font-semibold transition-all duration-150 ${
                    isActive
                      ? 'bg-nav-active-bg text-text-primary shadow-sm'
                      : 'text-brand-ink hover:bg-black/10'
                  }`
                }
              >
                {tab.label}
              </NavLink>
            ))}
          </nav>
        </div>

        <div className="flex items-center gap-3">
          {partner && (
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1.5 text-xs font-medium text-brand-ink">
                <User className="h-3.5 w-3.5 opacity-70" />
                <span>{partner.name}</span>
                <span className="rounded bg-black/20 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider">
                  CUSTOMER
                </span>
              </div>
              <button
                onClick={handleLogout}
                className="rounded-chip p-1 text-brand-ink hover:bg-black/10 transition-colors ml-2"
                title="Log out"
              >
                <LogOut className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </div>
      </header>

      <main className="flex-1 max-w-6xl w-full mx-auto px-4 sm:px-6 py-6 pb-16">
        <Outlet />
      </main>
    </div>
  );
};
