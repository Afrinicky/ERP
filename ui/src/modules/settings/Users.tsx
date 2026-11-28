/** Users, roles and permissions. */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Role, User } from '@/api/types';
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
        detail="Each user holds one or more roles. Exceptions for an individual are set under Access control."
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
