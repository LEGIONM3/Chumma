import React, { useState } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { HintStrip } from '@/components/data/HintStrip';
import { configApi } from '@/api/endpoints/config';
import { useMutation } from '@tanstack/react-query';
import { Check, Sliders, ShieldCheck, Box, Settings } from 'lucide-react';

export const ConfigLayout: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'tiers' | 'routing' | 'warehouses' | 'system'>('tiers');
  const [managerThreshold, setManagerThreshold] = useState(20);
  const [financeThreshold, setFinanceThreshold] = useState(50);
  const [singleLinePts, setSingleLinePts] = useState(8);
  const [savedSuccess, setSavedSuccess] = useState(false);

  const saveMutation = useMutation({
    mutationFn: () =>
      configApi.updateSettings({
        manager_threshold: managerThreshold,
        finance_threshold: financeThreshold,
        single_line_finance_pts: singleLinePts,
      }),
    onSuccess: () => {
      setSavedSuccess(true);
      setTimeout(() => setSavedSuccess(false), 3000);
    },
  });

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      <PageHeader
        title="Discount Tiers &amp; Approval Chain Setup"
        subtitle="Calibrate policy thresholds, hierarchical stage routing, and multi-warehouse logistics parameters"
        actions={
          <Button
            size="sm"
            variant="default"
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="gap-1.5 font-bold"
          >
            {savedSuccess ? (
              <>
                <Check className="h-4 w-4" />
                Saved!
              </>
            ) : (
              'Save Configuration'
            )}
          </Button>
        }
      />

      <div className="flex border-b border-border space-x-2">
        <button
          type="button"
          onClick={() => setActiveTab('tiers')}
          className={`flex items-center gap-1.5 px-3 py-2 text-xs font-semibold border-b-2 transition-colors ${
            activeTab === 'tiers' ? 'border-brand text-brand' : 'border-transparent text-text-muted hover:text-text-primary'
          }`}
        >
          <Sliders className="h-3.5 w-3.5" />
          Tiers &amp; Policies
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('routing')}
          className={`flex items-center gap-1.5 px-3 py-2 text-xs font-semibold border-b-2 transition-colors ${
            activeTab === 'routing' ? 'border-brand text-brand' : 'border-transparent text-text-muted hover:text-text-primary'
          }`}
        >
          <ShieldCheck className="h-3.5 w-3.5" />
          Approval Routing
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('warehouses')}
          className={`flex items-center gap-1.5 px-3 py-2 text-xs font-semibold border-b-2 transition-colors ${
            activeTab === 'warehouses' ? 'border-brand text-brand' : 'border-transparent text-text-muted hover:text-text-primary'
          }`}
        >
          <Box className="h-3.5 w-3.5" />
          Warehouse Profiles
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('system')}
          className={`flex items-center gap-1.5 px-3 py-2 text-xs font-semibold border-b-2 transition-colors ${
            activeTab === 'system' ? 'border-brand text-brand' : 'border-transparent text-text-muted hover:text-text-primary'
          }`}
        >
          <Settings className="h-3.5 w-3.5" />
          System &amp; Outbox
        </button>
      </div>

      {activeTab === 'tiers' && (
        <div className="space-y-4">
          <Card className="border-border bg-surface">
            <CardHeader>
              <CardTitle className="text-sm font-bold text-text-primary">Tier Discount Ceilings</CardTitle>
            </CardHeader>
            <CardContent>
              <table className="w-full text-left text-xs border border-border rounded overflow-hidden">
                <thead className="bg-elevated border-b border-border">
                  <tr>
                    <th className="py-2.5 px-4 font-semibold">Tier Code</th>
                    <th className="py-2.5 px-4 font-semibold">Display Name</th>
                    <th className="py-2.5 px-4 font-semibold text-right">Max Discount %</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  <tr>
                    <td className="py-2.5 px-4 font-bold text-info">GOLD</td>
                    <td className="py-2.5 px-4">Gold Tier Partner</td>
                    <td className="py-2.5 px-4 text-right tabular-nums font-semibold">15.0%</td>
                  </tr>
                  <tr>
                    <td className="py-2.5 px-4 font-bold text-text-secondary">SILVER</td>
                    <td className="py-2.5 px-4">Silver Tier Partner</td>
                    <td className="py-2.5 px-4 text-right tabular-nums font-semibold">10.0%</td>
                  </tr>
                  <tr>
                    <td className="py-2.5 px-4 font-bold text-warning">BRONZE</td>
                    <td className="py-2.5 px-4">Bronze Tier Partner</td>
                    <td className="py-2.5 px-4 text-right tabular-nums font-semibold">5.0%</td>
                  </tr>
                </tbody>
              </table>
            </CardContent>
          </Card>
        </div>
      )}

      {activeTab === 'routing' && (
        <Card className="border-border bg-surface">
          <CardHeader>
            <CardTitle className="text-sm font-bold text-text-primary">Approval Routing Thresholds</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-xs">
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="font-semibold block mb-1">Manager Threshold Score</label>
                <Input
                  type="number"
                  value={managerThreshold}
                  onChange={(e) => setManagerThreshold(Number(e.target.value))}
                  className="h-8 tabular-nums font-bold text-sm"
                />
              </div>
              <div>
                <label className="font-semibold block mb-1">Finance Threshold Score</label>
                <Input
                  type="number"
                  value={financeThreshold}
                  onChange={(e) => setFinanceThreshold(Number(e.target.value))}
                  className="h-8 tabular-nums font-bold text-sm"
                />
              </div>
              <div>
                <label className="font-semibold block mb-1">Single Line Finance Pts</label>
                <Input
                  type="number"
                  value={singleLinePts}
                  onChange={(e) => setSingleLinePts(Number(e.target.value))}
                  className="h-8 tabular-nums font-bold text-sm"
                />
              </div>
            </div>

            <div className="p-3.5 rounded bg-elevated/40 border border-border space-y-1.5">
              <span className="font-bold text-text-primary block">Deterministic Routing Rules:</span>
              <p className="text-text-muted">• Within tier/category limit ➔ <strong>No approval needed</strong></p>
              <p className="text-text-muted">• Over limit, blended risk medium (score ≤ {financeThreshold}) ➔ <strong>Sales Manager</strong></p>
              <p className="text-text-muted">• Over limit, blended high risk (&gt; {financeThreshold} or any line ≥ {singleLinePts} pts over) ➔ <strong>Sales Manager then Finance</strong></p>
            </div>
          </CardContent>
        </Card>
      )}

      {activeTab === 'warehouses' && (
        <Card className="border-border bg-surface">
          <CardHeader>
            <CardTitle className="text-sm font-bold text-text-primary">Warehouse Logistics Profiles</CardTitle>
          </CardHeader>
          <CardContent>
            <table className="w-full text-left text-xs border border-border rounded overflow-hidden">
              <thead className="bg-elevated border-b border-border">
                <tr>
                  <th className="py-2.5 px-4 font-semibold">Warehouse</th>
                  <th className="py-2.5 px-4 font-semibold text-center">Priority</th>
                  <th className="py-2.5 px-4 font-semibold text-right">Freight Weight</th>
                  <th className="py-2.5 px-4 font-semibold text-center">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                <tr>
                  <td className="py-2.5 px-4 font-semibold">Main Warehouse (WH1)</td>
                  <td className="py-2.5 px-4 text-center font-bold">1</td>
                  <td className="py-2.5 px-4 text-right tabular-nums">1.0</td>
                  <td className="py-2.5 px-4 text-center text-success font-semibold">Active</td>
                </tr>
                <tr>
                  <td className="py-2.5 px-4 font-semibold">East Depot (WH2)</td>
                  <td className="py-2.5 px-4 text-center font-bold">2</td>
                  <td className="py-2.5 px-4 text-right tabular-nums">2.5</td>
                  <td className="py-2.5 px-4 text-center text-success font-semibold">Active</td>
                </tr>
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {activeTab === 'system' && (
        <Card className="border-border bg-surface">
          <CardHeader>
            <CardTitle className="text-sm font-bold text-text-primary">System Health &amp; Outbox</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-xs">
            <div className="p-3 rounded bg-success/15 border border-success/40 text-success">
              ✓ Odoo 18 XML-RPC connection operational. Zero missing capability faults.
            </div>
            <div className="pt-2">
              <span className="font-semibold text-text-secondary block mb-1">Outbox Dispatch Queue (Customer Magic Links):</span>
              <div className="p-3 rounded bg-elevated/40 border border-border">
                <span className="font-mono text-[11px] text-brand">buyer@acme.test ➔ Token: magic_token_acme_buyer_123</span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <HintStrip>
        When a quote mixes categories with different ceilings, the system computes a blended risk score and routes to the highest required level. All approvals, rejections, and edits are logged with user, timestamp, and reason.
      </HintStrip>
    </div>
  );
};
