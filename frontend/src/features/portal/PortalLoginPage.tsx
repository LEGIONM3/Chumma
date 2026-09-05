import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { HintStrip } from '@/components/data/HintStrip';
import { authApi } from '@/api/endpoints/auth';
import { Loader2, MailCheck } from 'lucide-react';

export const PortalLoginPage: React.FC = () => {
  const [email, setEmail] = useState('buyer@acme.test');
  const [submitted, setSubmitted] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    try {
      await authApi.requestMagicLink(email);
    } catch {
      // 202 accepted always for customer privacy
    } finally {
      setIsLoading(false);
      setSubmitted(true);
    }
  };

  return (
    <Card className="border-border bg-surface shadow-2xl">
      <CardHeader className="text-center pb-3">
        <CardTitle className="text-xl font-bold">Customer Portal Access</CardTitle>
        <CardDescription>Review and negotiate your quotation securely</CardDescription>
      </CardHeader>

      <CardContent className="space-y-4 pt-2">
        {submitted ? (
          <div className="text-center py-6 space-y-3">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-chip bg-success/20 text-success">
              <MailCheck className="h-6 w-6" />
            </div>
            <h4 className="text-sm font-semibold text-text-primary">Magic Link Dispatched</h4>
            <p className="text-xs text-text-secondary max-w-xs mx-auto">
              If an account exists for <span className="text-text-primary font-medium">{email}</span>, a direct sign-in link has been sent to your inbox.
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => setSubmitted(false)}
            >
              Try another email
            </Button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="email" className="text-xs font-semibold text-text-secondary">
                Your Business Email
              </label>
              <Input
                id="email"
                type="email"
                inputMode="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="buyer@company.com"
              />
            </div>

            <Button type="submit" className="w-full font-bold" disabled={isLoading}>
              {isLoading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Sending Link…
                </>
              ) : (
                'Send Sign-In Link'
              )}
            </Button>
          </form>
        )}

        <HintStrip>
          Customer portal links are secure and time-limited. No password setup required.
        </HintStrip>
      </CardContent>
    </Card>
  );
};
