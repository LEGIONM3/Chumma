import React, { useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { Bell, LogOut, User as UserIcon, Shield } from 'lucide-react';
import { useAuthStore } from '@/features/auth/authStore';
import { INTERNAL_NAV_TABS, canAccessRoute, UserRole } from '@/lib/rbac';
import { NotificationsDrawer } from './NotificationsDrawer';
import { useQuery } from '@tanstack/react-query';
import { notificationsApi } from '@/api/endpoints/notifications';
import { queryKeys } from '@/api/queryKeys';

export const TopNav: React.FC = () => {
  const { user, clearAuth } = useAuthStore();
  const navigate = useNavigate();
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data: notifications = [] } = useQuery({
    queryKey: queryKeys.notifications.unread,
    queryFn: () => notificationsApi.list(true),
    refetchInterval: 30000,
  });

  const unreadCount = notifications.filter((n) => !n.is_read).length;

  const handleLogout = () => {
    clearAuth();
    navigate('/login');
  };

  const allowedTabs = INTERNAL_NAV_TABS.filter((tab) =>
    canAccessRoute(user?.role as UserRole, tab.allowedRoles)
  );

  return (
    <>
      <header className="sticky top-0 z-30 flex h-14 w-full items-center justify-between bg-brand px-6 text-brand-ink shadow-md select-none">
        {/* Brand & Tabs */}
        <div className="flex items-center gap-6">
          <div
            onClick={() => navigate('/')}
            className="flex items-center gap-2 cursor-pointer font-extrabold text-base tracking-tight text-brand-ink"
          >
            <Shield className="h-5 w-5 fill-brand-ink" />
            <span>DealFlow360</span>
          </div>

          <nav className="flex items-center space-x-1">
            {allowedTabs.map((tab) => (
              <NavLink
                key={tab.path}
                to={tab.path}
                end={tab.path === '/'}
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

        {/* Right side: Notifications & User Menu */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => setDrawerOpen(true)}
            className="relative rounded-chip p-1.5 text-brand-ink hover:bg-black/10 transition-colors focus:outline-none focus:ring-2 focus:ring-black"
            aria-label={`Notifications, ${unreadCount} unread`}
          >
            <Bell className="h-4 w-4" />
            {unreadCount > 0 && (
              <span className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-danger text-[10px] font-bold text-text-primary">
                {unreadCount > 9 ? '9+' : unreadCount}
              </span>
            )}
          </button>

          {user && (
            <div className="flex items-center gap-2 pl-2 border-l border-brand-ink/20">
              <div className="flex items-center gap-1.5 text-xs font-medium text-brand-ink">
                <UserIcon className="h-3.5 w-3.5 opacity-70" />
                <span>{user.name}</span>
                <span className="rounded bg-black/20 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider">
                  {user.role}
                </span>
              </div>
              <button
                onClick={handleLogout}
                className="rounded-chip p-1 text-brand-ink hover:bg-black/10 transition-colors"
                title="Log out"
                aria-label="Log out"
              >
                <LogOut className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </div>
      </header>

      <NotificationsDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        notifications={notifications}
      />
    </>
  );
};
