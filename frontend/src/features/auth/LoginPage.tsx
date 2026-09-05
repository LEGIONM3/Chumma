import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from './authStore';
import { authApi } from '@/api/endpoints/auth';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { HintStrip } from '@/components/data/HintStrip';
import { ForgotPasswordDialog } from './ForgotPasswordDialog';
import { Loader2 } from 'lucide-react';

export const LoginPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'login' | 'signup'>('login');
  const [login, setLogin] = useState('rep1@dealflow.test');
  const [password, setPassword] = useState('Password123!');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [forgotOpen, setForgotOpen] = useState(false);

  const { setAuth } = useAuthStore();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);

    try {
      const res = await authApi.login(login, password);
      setAuth(res.access_token, res.user);
      navigate('/');
    } catch (err: any) {
      setError(err.message || 'Authentication failed. Please verify credentials.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="w-full">
      <Card className="border-border bg-surface shadow-2xl">
        <CardHeader className="text-center pb-4">
          <CardTitle className="text-xl font-bold">DealFlow360</CardTitle>
          <CardDescription>B2B Sales Operations Platform Backend</CardDescription>

          {/* Wireframe Tabs */}
          <div className="flex border-b border-border mt-4">
            <button
              type="button"
              onClick={() => setActiveTab('login')}
              className={`flex-1 py-2 text-xs font-semibold transition-colors ${
                activeTab === 'login'
                  ? 'border-b-2 border-brand text-text-primary'
                  : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              Log In
            </button>
            <div
              className="flex-1 py-2 text-xs font-semibold text-text-muted/40 cursor-not-allowed text-center relative group"
              title="Internal accounts are provisioned in Odoo. Customers: use the portal link in your email."
            >
              <span>Sign Up</span>
              <div className="hidden group-hover:block absolute top-full left-1/2 -translate-x-1/2 z-50 w-64 p-2 mt-1 rounded bg-elevated border border-border text-[11px] text-text-secondary shadow-lg">
                Internal accounts are provisioned in Odoo. Customers: use the portal link in your email.
              </div>
            </div>
          </div>
        </CardHeader>

        <CardContent className="space-y-4 pt-2">
          {error && (
            <div className="rounded-input border border-danger/40 bg-danger/15 px-3 py-2 text-xs text-danger">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="login" className="text-xs font-semibold text-text-secondary">
                Email / Login
              </label>
              <Input
                id="login"
                type="email"
                inputMode="email"
                autoComplete="email"
                required
                value={login}
                onChange={(e) => setLogin(e.target.value)}
                placeholder="name@company.com"
              />
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label htmlFor="password" className="text-xs font-semibold text-text-secondary">
                  Password
                </label>
                <button
                  type="button"
                  onClick={() => setForgotOpen(true)}
                  className="text-[11px] text-brand hover:underline"
                >
                  Forgot Password?
                </button>
              </div>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>

            <Button type="submit" className="w-full font-bold" disabled={isLoading}>
              {isLoading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Logging in…
                </>
              ) : (
                'Log In'
              )}
            </Button>
          </form>

          {/* Quick switch demo logins */}
          <div className="pt-2 border-t border-border/60">
            <p className="text-[10px] text-text-muted mb-1.5">Quick Demo Roles:</p>
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => { setLogin('rep1@dealflow.test'); setPassword('Password123!'); }}
                className="px-2 py-0.5 rounded text-[10px] bg-elevated hover:bg-elevated/80 text-text-secondary border border-border"
              >
                Sales Rep (rep1)
              </button>
              <button
                type="button"
                onClick={() => { setLogin('manager1@dealflow.test'); setPassword('Password123!'); }}
                className="px-2 py-0.5 rounded text-[10px] bg-elevated hover:bg-elevated/80 text-text-secondary border border-border"
              >
                Manager (manager1)
              </button>
              <button
                type="button"
                onClick={() => { setLogin('finance@dealflow.test'); setPassword('Password123!'); }}
                className="px-2 py-0.5 rounded text-[10px] bg-elevated hover:bg-elevated/80 text-text-secondary border border-border"
              >
                Finance
              </button>
              <button
                type="button"
                onClick={() => { setLogin('admin@dealflow.test'); setPassword('Password123!'); }}
                className="px-2 py-0.5 rounded text-[10px] bg-elevated hover:bg-elevated/80 text-text-secondary border border-border"
              >
                Admin
              </button>
            </div>
          </div>

          <HintStrip>
            After login, internal users land on the Sales Dashboard. Customers land on their Quotation Portal.
          </HintStrip>
        </CardContent>
      </Card>

      <ForgotPasswordDialog open={forgotOpen} onOpenChange={setForgotOpen} />
    </div>
  );
};
