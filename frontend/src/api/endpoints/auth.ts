import { apiClient } from '../client';
import type { AuthResponse, PortalAuthResponse, AuthUser } from '../types';

export const authApi = {
  login: (login: string, password: string): Promise<AuthResponse> =>
    apiClient('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ login, password }),
    }),

  me: (): Promise<AuthUser> => apiClient('/auth/me'),

  logout: (): Promise<{ message: string }> =>
    apiClient('/auth/logout', { method: 'POST' }),

  requestMagicLink: (email: string): Promise<void> =>
    apiClient('/portal/auth/magic-link', {
      method: 'POST',
      body: JSON.stringify({ email }),
    }),

  verifyMagicLink: (token: string): Promise<PortalAuthResponse> =>
    apiClient(`/portal/auth/verify?token=${encodeURIComponent(token)}`),

  exchangeOdooToken: (token: string): Promise<PortalAuthResponse> =>
    apiClient('/portal/auth/exchange', {
      method: 'POST',
      body: JSON.stringify({ token }),
    }),

  portalMe: (): Promise<{ id: number; name: string }> => apiClient('/portal/me'),
};
