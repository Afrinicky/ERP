/** Users, roles and permissions. */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Permission, Role, User } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  Dialog,
  EmptyState,
  Field,
  SectionHeader,
  Skeleton,
  StatusChip,
  TextInput,
  useToast,
} from '@/components/primitives';
import { describe, fieldError } from '@/lib/errors';
import { humanise, relative } from '@/lib/format';
import { useSession } from '@/lib/session';

export function Users() {
  const { actor, can } = useSession();
  const client = useQueryClient();
  const toast = useToast();

  const users = useQuery({ queryKey: ['users'], queryFn: () => api.users.list() });
  const roles = useQuery({ queryKey: ['roles'], queryFn: api.roles.list });

  const [creating, setCreating] = useState(false);
  const [deactivating, setDeactivating] = useState<User | null>(null);
  const [resetting, setResetting] = useState<User | null>(null);

  const invalidate = () => void client.invalidateQueries({ queryKey: ['users'] });

  const setActive = useMutation({
    mutationFn: ({ user, reason }: { user: User; reason: string }) =>
      api.users.setActive(user.id, !user.is_active, reason),
    onSuccess: (updated) => {
      invalidate();
      setDeactivating(null);
      toast.show('success', `${updated.full_name} ${updated.is_active ? 'reactivated' : 'deactivated'}.`);
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const unlock = useMutation({
    mutationFn: (user: User) => api.users.unlock(user.id),
    onSuccess: (updated) => {
      invalidate();
      toast.show('success', `${updated.full_name} unlocked.`);
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  if (!can('user.view')) {
    return (
      <EmptyState
        title="User administration is restricted"
        detail="You do not hold the permission to view users."
      />
    );
  }

  return (
    <div className="settings__panel">
      <SectionHeader
        title="Users"
        detail="Each user holds one or more roles. Permissions are enforced by the service, not by the interface."
        actions={
          can('user.manage') ? (
            <Button tone="primary" size="compact" onClick={() => setCreating(true)}>
              Add user
            </Button>
          ) : null
        }
      />

      <Card flush>
        {users.isLoading ? (
          <div style={{ padding: 'var(--space-5)' }}>
            <Skeleton rows={4} height={16} />
          </div>
        ) : users.data && users.data.length > 0 ? (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th style={{ width: '14%' }}>Username</th>
                  <th style={{ width: '20%' }}>Roles</th>
                  <th style={{ width: '14%' }}>Standing</th>
                  <th style={{ width: '14%' }}>Last signed in</th>
                  {can('user.manage') ? <th style={{ width: '16%' }} /> : null}
                </tr>
              </thead>
              <tbody>
                {users.data.map((user) => (
                  <tr key={user.id}>
                    <td>
                      {user.full_name}
                      {user.id === actor?.user_id ? (
                        <span className="caption"> · you</span>
                      ) : null}
                    </td>
                    <td className="numeric" style={{ textAlign: 'left' }}>
                      {user.username}
                    </td>
                    <td className="table__cell--wrap">
                      {user.role_codes.map((code) => humanise(code)).join(', ')}
                    </td>
                    <td>
                      {!user.is_active ? (
                        <StatusChip tone="neutral">Inactive</StatusChip>
                      ) : user.locked_until && new Date(user.locked_until) > new Date() ? (
                        <StatusChip tone="warning">Locked</StatusChip>
                      ) : user.must_change_password ? (
                        <StatusChip tone="info">Password change due</StatusChip>
                      ) : (
                        <StatusChip tone="ok">Active</StatusChip>
                      )}
                    </td>
                    <td className="muted">{relative(user.last_signed_in_at)}</td>
                    {can('user.manage') ? (
                      <td>
                        <div className="table__actions">
                          {user.locked_until && new Date(user.locked_until) > new Date() ? (
                            <Button
                              tone="plain"
                              size="compact"
                              onClick={() => unlock.mutate(user)}
                            >
                              Unlock
                            </Button>
                          ) : null}
                          <Button
                            tone="plain"
                            size="compact"
                            onClick={() => setResetting(user)}
                          >
                            Reset password
                          </Button>
                          {user.id !== actor?.user_id ? (
                            <Button
                              tone="plain"
                              size="compact"
                              onClick={() => setDeactivating(user)}
                            >
                              {user.is_active ? 'Deactivate' : 'Reactivate'}
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No users recorded" />
        )}
      </Card>

      {creating ? (
        <UserDialog
          roles={roles.data ?? []}
          onClose={() => setCreating(false)}
          onSaved={() => {
            invalidate();
            setCreating(false);
            toast.show('success', 'User created.');
          }}
        />
      ) : null}

      {deactivating ? (
        <ConfirmDialog
          title={deactivating.is_active ? 'Deactivate user' : 'Reactivate user'}
          record={`${deactivating.full_name} (${deactivating.username})`}
          consequence={
            deactivating.is_active
              ? 'Every live session of this user is revoked immediately. The account and its history are retained.'
              : 'The account may sign in again. Any lockout is cleared.'
          }
          confirmLabel={deactivating.is_active ? 'Deactivate' : 'Reactivate'}
          requireReason
          busy={setActive.isPending}
          onConfirm={(reason) => setActive.mutate({ user: deactivating, reason })}
          onClose={() => setDeactivating(null)}
        />
      ) : null}

      {resetting ? (
        <ResetPasswordDialog
          user={resetting}
          onClose={() => setResetting(null)}
          onSaved={() => {
            invalidate();
            setResetting(null);
            toast.show('success', 'Password reset. The user must change it at next sign-in.');
          }}
        />
      ) : null}
    </div>
  );
}

function UserDialog({
  roles,
  onClose,
  onSaved,
}: {
  roles: Role[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [fullName, setFullName] = useState('');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);

  const create = useMutation({
    mutationFn: () =>
      api.users.create({
        username,
        full_name: fullName,
        password,
        role_codes: selected,
        email: email || null,
        must_change_password: true,
      }),
    onSuccess: onSaved,
    onError: setError,
  });

  const ready =
    fullName.trim().length > 0 &&
    username.trim().length >= 3 &&
    password.length > 0 &&
    selected.length > 0;

  return (
    <Dialog
      title="Add user"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={create.isPending}
            disabled={!ready}
            onClick={() => create.mutate()}
          >
            Create user
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {error ? <Alert tone="error">{describe(error).message}</Alert> : null}

        <div className="form-grid form-grid--two">
          <Field label="Full name" required error={fieldError(error, 'full_name')}>
            {(id) => (
              <TextInput
                id={id}
                value={fullName}
                onChange={(event) => setFullName(event.target.value)}
                autoFocus
              />
            )}
          </Field>
          <Field
            label="Username"
            required
            hint="Three to thirty-two characters."
            error={fieldError(error, 'username')}
          >
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={username}
                onChange={(event) => setUsername(event.target.value.toLowerCase())}
                autoCapitalize="none"
                spellCheck={false}
              />
            )}
          </Field>
          <Field label="Electronic mail address" optional>
            {(id) => (
              <TextInput
                id={id}
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            )}
          </Field>
          <Field
            label="Initial password"
            required
            hint="The user must change it at first sign-in."
            error={fieldError(error, 'password')}
          >
            {(id, describedBy) => (
              <TextInput
                id={id}
                type="password"
                aria-describedby={describedBy}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="new-password"
              />
            )}
          </Field>
        </div>

        <fieldset className="fieldset">
          <legend className="fieldset__legend">Roles</legend>
          {error && fieldError(error, 'role_codes') ? (
            <span className="field__error">{fieldError(error, 'role_codes')}</span>
          ) : null}
          <div className="stack gap-3">
            {roles.map((role) => (
              <Checkbox
                key={role.code}
                label={role.name}
                hint={role.description ?? undefined}
                checked={selected.includes(role.code)}
                onChange={(checked) =>
                  setSelected((current) =>
                    checked
                      ? [...current, role.code]
                      : current.filter((code) => code !== role.code),
                  )
                }
              />
            ))}
          </div>
        </fieldset>
      </div>
    </Dialog>
  );
}

function ResetPasswordDialog({
  user,
  onClose,
  onSaved,
}: {
  user: User;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [password, setPassword] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState<unknown>(null);

  const reset = useMutation({
    mutationFn: () => api.users.resetPassword(user.id, password, reason),
    onSuccess: onSaved,
    onError: setError,
  });

  return (
    <Dialog
      title="Reset password"
      size="sm"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={reset.isPending}
            disabled={!password || !reason.trim()}
            onClick={() => reset.mutate()}
          >
            Reset password
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {error ? <Alert tone="error">{describe(error).message}</Alert> : null}
        <p className="muted">
          Resetting the password for <strong>{user.full_name}</strong> revokes every live
          session and requires a change at next sign-in.
        </p>
        <Field label="New password" required error={fieldError(error, 'password')}>
          {(id) => (
            <TextInput
              id={id}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              autoFocus
            />
          )}
        </Field>
        <Field label="Reason" required hint="Recorded in the audit trail.">
          {(id, describedBy) => (
            <TextInput
              id={id}
              aria-describedby={describedBy}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

export function Roles() {
  const { can } = useSession();
  const client = useQueryClient();
  const toast = useToast();

  const roles = useQuery({ queryKey: ['roles'], queryFn: api.roles.list });
  const permissions = useQuery({ queryKey: ['permissions'], queryFn: api.permissions.list });

  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [granted, setGranted] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState('');

  const selected = roles.data?.find((role) => role.code === selectedCode) ?? null;

  const grouped = useMemo(() => {
    const result = new Map<string, Permission[]>();
    for (const permission of permissions.data ?? []) {
      const list = result.get(permission.group) ?? [];
      list.push(permission);
      result.set(permission.group, list);
    }
    return result;
  }, [permissions.data]);

  const save = useMutation({
    mutationFn: () =>
      api.roles.setPermissions(selected!.code, Array.from(granted), reason),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['roles'] });
      setSelectedCode(null);
      setReason('');
      toast.show('success', 'Role permissions saved.');
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  function open(role: Role) {
    setSelectedCode(role.code);
    setGranted(new Set(role.permissions));
    setReason('');
  }

  if (!can('user.view')) {
    return <EmptyState title="Role administration is restricted" />;
  }

  return (
    <div className="settings__panel">
      <SectionHeader
        title="Roles and permissions"
        detail="The eight roles of the requirements are supplied and may be amended. A permission is a verb against a resource."
      />

      <Card flush>
        {roles.isLoading ? (
          <div style={{ padding: 'var(--space-5)' }}>
            <Skeleton rows={5} height={16} />
          </div>
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: '22%' }}>Role</th>
                  <th>Responsibilities</th>
                  <th style={{ width: '12%' }} className="numeric">
                    Permissions
                  </th>
                  <th style={{ width: '12%' }}>Standing</th>
                  {can('role.manage') ? <th style={{ width: '10%' }} /> : null}
                </tr>
              </thead>
              <tbody>
                {(roles.data ?? []).map((role) => (
                  <tr key={role.code}>
                    <td>{role.name}</td>
                    <td className="table__cell--wrap muted">{role.description}</td>
                    <td className="numeric">{role.permissions.length}</td>
                    <td>
                      {role.is_administrator ? (
                        <StatusChip tone="accent">Administrator</StatusChip>
                      ) : role.is_read_only ? (
                        <StatusChip tone="info">Read only</StatusChip>
                      ) : (
                        <StatusChip tone="neutral">Standard</StatusChip>
                      )}
                    </td>
                    {can('role.manage') ? (
                      <td>
                        <div className="table__actions">
                          <Button tone="plain" size="compact" onClick={() => open(role)}>
                            {role.is_administrator ? 'View' : 'Amend'}
                          </Button>
                        </div>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {selected ? (
        <Dialog
          title={`Permissions — ${selected.name}`}
          size="lg"
          onClose={() => setSelectedCode(null)}
          footer={
            <>
              <Button tone="plain" onClick={() => setSelectedCode(null)}>
                Cancel
              </Button>
              <Button
                tone="primary"
                busy={save.isPending}
                disabled={selected.is_administrator || !reason.trim()}
                onClick={() => save.mutate()}
              >
                Save permissions
              </Button>
            </>
          }
        >
          <div className="stack gap-4">
            {selected.is_administrator ? (
              <Alert tone="info" title="The administrator role cannot be narrowed">
                This role holds every permission by definition. To grant a restricted set of
                capabilities, create a separate role.
              </Alert>
            ) : (
              <Field label="Reason" required hint="Recorded in the audit trail.">
                {(id, describedBy) => (
                  <TextInput
                    id={id}
                    aria-describedby={describedBy}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                  />
                )}
              </Field>
            )}

            {Array.from(grouped.entries()).map(([group, items]) => (
              <fieldset className="fieldset" key={group}>
                <legend className="fieldset__legend">{group}</legend>
                <div className="form-grid">
                  {items.map((permission) => (
                    <Checkbox
                      key={permission.code}
                      label={permission.name}
                      hint={
                        permission.sensitive
                          ? `${permission.description} Sensitive.`
                          : permission.description
                      }
                      checked={granted.has(permission.code)}
                      disabled={selected.is_administrator}
                      onChange={(checked) =>
                        setGranted((current) => {
                          const next = new Set(current);
                          if (checked) next.add(permission.code);
                          else next.delete(permission.code);
                          return next;
                        })
                      }
                    />
                  ))}
                </div>
              </fieldset>
            ))}
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}

