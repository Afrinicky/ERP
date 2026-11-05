/** The settings workspace and its sub-navigation. */

import { NavLink, Route, Routes } from 'react-router-dom';

import { ContextBar, PageHeading, WorkSurface } from '@/components/shell';
import { EmptyState } from '@/components/primitives';
import { Appearance } from '@/modules/settings/Appearance';
import { BusinessSettings } from '@/modules/settings/BusinessSettings';
import { CustomFields } from '@/modules/settings/CustomFields';
import { DataImport } from '@/modules/settings/DataImport';
import { Numbering } from '@/modules/settings/Numbering';
import { AuditTrail, BackupSettings, SystemHealth } from '@/modules/settings/Platform';
import { Roles, Users } from '@/modules/settings/Users';
import { useSession } from '@/lib/session';

interface Area {
  path: string;
  label: string;
  permission: string;
  element: JSX.Element;
}

const AREAS: Area[] = [
  { path: '', label: 'Business', permission: 'business.view', element: <BusinessSettings /> },
  { path: 'users', label: 'Users', permission: 'user.view', element: <Users /> },
  { path: 'roles', label: 'Roles and permissions', permission: 'user.view', element: <Roles /> },
  { path: 'appearance', label: 'Appearance', permission: 'business.view', element: <Appearance /> },
  { path: 'numbering', label: 'Document numbering', permission: 'business.view', element: <Numbering /> },
  { path: 'custom-fields', label: 'Custom fields', permission: 'business.view', element: <CustomFields /> },
  { path: 'import', label: 'Import', permission: 'product.view', element: <DataImport /> },
  { path: 'backup', label: 'Backup and restore', permission: 'health.view', element: <BackupSettings /> },
  { path: 'health', label: 'System health', permission: 'health.view', element: <SystemHealth /> },
  { path: 'audit', label: 'Audit trail', permission: 'audit.view', element: <AuditTrail /> },
];

export function Settings() {
  const { can } = useSession();
  const permitted = AREAS.filter((area) => can(area.permission));

  return (
    <>
      <ContextBar trail={['Settings']} />
      <WorkSurface>
        <PageHeading
          title="Settings"
          detail="Business configuration, users and permissions, appearance, numbering, custom fields and platform administration."
        />

        <div className="settings">
          <nav className="settings__nav" aria-label="Settings sections">
            {permitted.map((area) => (
              <NavLink
                key={area.path}
                to={area.path ? `/settings/${area.path}` : '/settings'}
                end
                className="settings__item"
              >
                {area.label}
              </NavLink>
            ))}
          </nav>

          <Routes>
            {permitted.map((area) => (
              <Route key={area.path} path={area.path || '/'} element={area.element} />
            ))}
            <Route
              path="*"
              element={
                <EmptyState
                  title="This section is not available"
                  detail="It may not exist, or your role may not permit access to it."
                />
              }
            />
          </Routes>
        </div>
      </WorkSurface>
    </>
  );
}
