/**
 * The application shell: navigation rail, header, context bar and status strip.
 */

import { useEffect, useState, type ReactNode } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { HealthState } from '@/api/types';
import { Mark as BrandMark } from '@/components/brand';
import { Icons, type IconName } from '@/components/icons';
import { Button } from '@/components/primitives';
import { initials, relative } from '@/lib/format';
import { useSession } from '@/lib/session';

interface Destination {
  to: string;
  label: string;
  icon: IconName;
  permission?: string;
  group: string;
}

/** The fourteen top-level destinations of PRD 7.3 and PRD 23. */
const DESTINATIONS: Destination[] = [
  { to: '/', label: 'Dashboard', icon: 'dashboard', group: 'Overview' },
  { to: '/sales', label: 'Sales', icon: 'sales', permission: 'quotation.view', group: 'Commerce' },
  { to: '/register', label: 'Point of Sale', icon: 'register', permission: 'pos.operate', group: 'Commerce' },
  { to: '/purchases', label: 'Purchases', icon: 'purchases', permission: 'purchase_order.manage', group: 'Commerce' },
  { to: '/products', label: 'Products', icon: 'products', permission: 'product.view', group: 'Operations' },
  { to: '/inventory', label: 'Inventory', icon: 'inventory', permission: 'stock.view', group: 'Operations' },
  { to: '/dispatch', label: 'Dispatch', icon: 'dispatch', permission: 'dispatch.view', group: 'Operations' },
  { to: '/parties', label: 'Customers and Suppliers', icon: 'parties', permission: 'customer.view', group: 'Operations' },
  { to: '/affiliates', label: 'Affiliates', icon: 'affiliates', permission: 'affiliate.view', group: 'Operations' },
  { to: '/taxation', label: 'Taxation', icon: 'taxation', permission: 'tax.view', group: 'Financial' },
  { to: '/finance', label: 'Financial Management', icon: 'finance', permission: 'account.view', group: 'Financial' },
  { to: '/documents', label: 'Documents', icon: 'documents', permission: 'document.view', group: 'Records' },
  { to: '/reports', label: 'Reports', icon: 'reports', permission: 'report.view', group: 'Records' },
  { to: '/settings', label: 'Settings', icon: 'settings', permission: 'business.view', group: 'Administration' },
];

const RAIL_KEY = 'nickland.rail';

export function Shell({ children }: { children: ReactNode }) {
  const { actor, business, signOut, can } = useSession();
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(RAIL_KEY) === 'collapsed';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(RAIL_KEY, collapsed ? 'collapsed' : 'expanded');
    } catch {
      // A remembered preference is a convenience; its absence changes nothing.
    }
  }, [collapsed]);

  const permitted = DESTINATIONS.filter(
    (item) => !item.permission || can(item.permission),
  );
  const groups = permitted.reduce<Record<string, Destination[]>>((accumulated, item) => {
    (accumulated[item.group] ??= []).push(item);
    return accumulated;
  }, {});

  return (
    <div className="shell" data-rail={collapsed ? 'collapsed' : 'expanded'}>
      <nav className="rail" id="main-navigation" aria-label="Main navigation">
        <div className="rail__brand">
          <span className="rail__mark">
            <BrandMark size={26} onDark title="Nickland" />
          </span>
          <span className="rail__wordmark">
            <span className="rail__product">Nickland</span>
            <span className="rail__edition">Enterprise edition</span>
          </span>
          <button
            type="button"
            className="rail__toggle"
            onClick={() => setCollapsed((current) => !current)}
            aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
            aria-expanded={!collapsed}
            aria-controls="main-navigation"
            title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          >
            <Icons.collapse />
          </button>
        </div>

        <div className="rail__nav">
          {Object.entries(groups).map(([group, items]) => (
            <div key={group}>
              <p className="rail__group-label">{group}</p>
              {items.map((item) => {
                const Icon = Icons[item.icon];
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === '/'}
                    className="rail__item"
                    title={collapsed ? item.label : undefined}
                  >
                    <span className="rail__icon">
                      <Icon />
                    </span>
                    <span className="rail__label">{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          ))}
        </div>

      </nav>

      <header className="header">
        <div className="header__business">
          <span className="header__name">{business?.name ?? 'Nickland ERP'}</span>
          <span className="header__context">
            {business?.base_currency} · {business?.timezone}
          </span>
        </div>

        <div className="grow" />

        <div className="header__search">
          <Icons.search />
          <input
            type="search"
            placeholder="Search records"
            aria-label="Search records"
            disabled
          />
        </div>

        <button type="button" className="header__identity" aria-label="Account">
          <span className="header__initials" aria-hidden="true">
            {initials(actor?.full_name ?? '')}
          </span>
          <span className="header__identity-body">
            <span className="header__identity-name">{actor?.full_name}</span>
            <span className="header__identity-role">
              {actor?.role_codes.map((code) => code.replace(/_/g, ' ')).join(', ')}
            </span>
          </span>
        </button>

        <Button tone="plain" size="compact" className="header__control" onClick={() => void signOut()}>
          Sign out
        </Button>
      </header>

      <main className="main">{children}</main>

      <StatusStrip />
    </div>
  );
}

export function ContextBar({
  trail,
  actions,
}: {
  trail: string[];
  actions?: ReactNode;
}) {
  return (
    <div className="context-bar">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        {trail.map((item, index) => (
          <span key={item} className="row gap-2">
            {index > 0 ? (
              <span className="breadcrumb__separator" aria-hidden="true">
                /
              </span>
            ) : null}
            <span
              className={`breadcrumb__item${
                index === trail.length - 1 ? ' breadcrumb__item--current' : ''
              }`}
              aria-current={index === trail.length - 1 ? 'page' : undefined}
            >
              {item}
            </span>
          </span>
        ))}
      </nav>
      {actions ? <div className="context-bar__actions">{actions}</div> : null}
    </div>
  );
}

export function WorkSurface({ children }: { children: ReactNode }) {
  const location = useLocation();
  useEffect(() => {
    document.querySelector('.work-surface')?.scrollTo({ top: 0 });
  }, [location.pathname]);
  return (
    <div className="work-surface">
      <div className="work-surface__inner">{children}</div>
    </div>
  );
}

export function PageHeading({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="page-heading">
      <h1 className="page-heading__title">{title}</h1>
      {detail ? <p className="page-heading__detail">{detail}</p> : null}
    </div>
  );
}

const MODE_LABEL: Record<string, string> = {
  standalone: 'Standalone',
  lan_host: 'Network host',
  lan_client: 'Network client',
};

/** Deployment mode, service health, backup position and accounting period state. */
function StatusStrip() {
  const { can } = useSession();
  const detailed = can('health.view');

  const health = useQuery({
    queryKey: ['health', detailed],
    queryFn: () => (detailed ? api.platform.healthDetail() : api.platform.health()),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const report = health.data;
  const backup = report?.checks.find((check) => check.name === 'Backup');
  const network = report?.checks.find((check) => check.name === 'Network');
  const database = report?.checks.find((check) => check.name === 'Database');

  return (
    <footer className="status-strip">
      <span className="status-strip__item">
        <Mark state={report?.state ?? 'unavailable'} />
        {MODE_LABEL[report?.deployment_mode ?? ''] ?? 'Standalone'}
      </span>

      <span className="status-strip__divider" aria-hidden="true" />

      <span className="status-strip__item">
        <Mark state={database?.state ?? 'unavailable'} />
        Database {database?.state === 'ok' ? 'connected' : (database?.state ?? 'unknown')}
      </span>

      <span className="status-strip__divider" aria-hidden="true" />

      <span className="status-strip__item">
        <Mark state={network?.state ?? 'unavailable'} />
        {network?.state === 'ok' ? 'Online' : 'Offline — core workflows unaffected'}
      </span>

      {backup ? (
        <>
          <span className="status-strip__divider" aria-hidden="true" />
          <span className="status-strip__item">
            <Mark state={backup.state} />
            {backup.state === 'ok'
              ? `Backup ${relative(String(backup.measurements.taken_at ?? ''))}`
              : 'No verified backup'}
          </span>
        </>
      ) : null}

      <div className="grow" />

      <OpenPeriod />
    </footer>
  );
}

/**
 * Which accounting period a posting made now would land in.
 *
 * It sits at the end of the strip because it governs every transaction the operator
 * is about to record: posting into a closed period, or into no period at all, fails
 * at the point of saving, and the strip is where that is noticed first.
 */
function OpenPeriod() {
  const { can } = useSession();
  const summary = useQuery({
    queryKey: ['ledger', 'summary'],
    queryFn: () => api.financialReports.summary(),
    enabled: can('account.view'),
    staleTime: 60_000,
  });

  if (!can('account.view')) return null;
  const period = summary.data?.open_period_label;
  return (
    <span className="status-strip__item caption">
      <Mark state={period ? 'ok' : 'degraded'} />
      {period ? `Period ${period}` : 'No accounting period covers today'}
    </span>
  );
}

function Mark({ state }: { state: HealthState }) {
  return <span className={`status-strip__mark status-strip__mark--${state}`} aria-hidden="true" />;
}
