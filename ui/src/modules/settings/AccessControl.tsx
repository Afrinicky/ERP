/**
 * Access control: who may do what, and how people sign in.
 *
 * Roles carry the rule; an exception carries the person. A permission granted to or
 * withheld from one user is recorded with its reason, and nobody amends their own access.
 * Every decision is enforced by the service; this screen sets it.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { AccessPolicyTerms, Permission, Role, User, UserAccess } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  Dialog,
  EmptyState,
  Field,
  SearchField,
  SectionHeader,
  Select,
  Skeleton,
  StatusChip,
  Tabs,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { dateTime, relative } from "@/lib/format";
import { useSession } from "@/lib/session";

type View = "roles" | "users" | "policy";

export function AccessControl() {
  const { can } = useSession();
  const [view, setView] = useState<View>("roles");

  if (!can("user.view")) {
    return <EmptyState title="Access control is restricted to administrators." />;
  }

  return (
    <div className="settings__panel">
      <SectionHeader
        title="Access control"
        detail="Who may do what, the exceptions made for individual people, and how everyone signs in."
      />
      <Tabs
        items={[
          { id: "roles", label: "Roles" },
          { id: "users", label: "User access" },
          { id: "policy", label: "Sign-in policy" },
        ]}
        active={view}
        onChange={setView}
      />
      {view === "roles" ? <RolesView /> : view === "users" ? <UsersView /> : <PolicyView />}
    </div>
  );
}

// -- shared ---------------------------------------------------------------------------------

function usePermissions() {
  const permissions = useQuery({ queryKey: ["permissions"], queryFn: api.permissions.list });
  const grouped = useMemo(() => {
    const result = new Map<string, Permission[]>();
    for (const permission of permissions.data ?? []) {
      const list = result.get(permission.group) ?? [];
      list.push(permission);
      result.set(permission.group, list);
    }
    return result;
  }, [permissions.data]);
  return { permissions, grouped };
}

function matches(permission: Permission, term: string): boolean {
  const needle = term.trim().toLowerCase();
  if (!needle) return true;
  return (
    permission.name.toLowerCase().includes(needle) ||
    permission.description.toLowerCase().includes(needle) ||
    permission.code.includes(needle) ||
    permission.group.toLowerCase().includes(needle)
  );
}

// -- roles ----------------------------------------------------------------------------------

function RolesView() {
  const { can } = useSession();
  const toast = useToast();
  const queries = useQueryClient();
  const roles = useQuery({ queryKey: ["roles"], queryFn: api.roles.list });
  const [editing, setEditing] = useState<Role | null>(null);
  const [renaming, setRenaming] = useState<Role | null>(null);
  const [removing, setRemoving] = useState<Role | null>(null);
  const [creating, setCreating] = useState(false);
  const manage = can("role.manage");

  const remove = useMutation({
    mutationFn: ({ code, reason }: { code: string; reason: string }) =>
      api.roles.remove(code, reason),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["roles"] });
      toast.show("success", `${removing?.name ?? "Role"} removed.`);
      setRemoving(null);
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  return (
    <>
      <Card
        title="Roles"
        subtitle="Each user holds one or more roles; their permissions are the sum of their roles."
        actions={
          manage ? (
            <Button tone="primary" onClick={() => setCreating(true)}>
              Create role
            </Button>
          ) : undefined
        }
        flush
      >
        {roles.isPending ? (
          <div style={{ padding: "var(--space-5)" }}>
            <Skeleton rows={4} height={16} />
          </div>
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: "18%" }}>Role</th>
                  <th>Responsibilities</th>
                  <th className="numeric" style={{ width: "9%" }}>
                    Users
                  </th>
                  <th className="numeric" style={{ width: "11%" }}>
                    Permissions
                  </th>
                  <th style={{ width: "12%" }}>Standing</th>
                  {manage ? <th className="access__actions-heading" /> : null}
                </tr>
              </thead>
              <tbody>
                {(roles.data ?? []).map((role) => (
                  <tr key={role.code}>
                    <td>{role.name}</td>
                    <td className="table__cell--wrap muted">{role.description ?? "—"}</td>
                    <td className="numeric">{role.holders}</td>
                    <td className="numeric">
                      {role.is_administrator ? "All" : role.permissions.length}
                    </td>
                    <td>
                      {role.is_administrator ? (
                        <StatusChip tone="accent">Administrator</StatusChip>
                      ) : role.is_system ? (
                        <StatusChip tone="info">Supplied</StatusChip>
                      ) : (
                        <StatusChip tone="neutral">Custom</StatusChip>
                      )}
                    </td>
                    {manage ? (
                      <td className="access__actions">
                        <div className="table__actions">
                          <Button tone="plain" size="compact" onClick={() => setEditing(role)}>
                            {role.is_administrator ? "View" : "Permissions"}
                          </Button>
                          <Button tone="plain" size="compact" onClick={() => setRenaming(role)}>
                            Rename
                          </Button>
                          {role.is_system ? null : (
                            <Button tone="plain" size="compact" onClick={() => setRemoving(role)}>
                              Remove
                            </Button>
                          )}
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

      {creating ? (
        <CreateRoleDialog
          roles={roles.data ?? []}
          onClose={() => setCreating(false)}
          onCreated={(role) => {
            setCreating(false);
            setEditing(role);
          }}
        />
      ) : null}
      {editing ? <RolePermissionsDialog role={editing} onClose={() => setEditing(null)} /> : null}
      {renaming ? <RenameRoleDialog role={renaming} onClose={() => setRenaming(null)} /> : null}
      {removing ? (
        <ConfirmDialog
          title={`Remove ${removing.name}`}
          record={removing.name}
          consequence={
            removing.holders > 0
              ? `${removing.holders} user(s) hold this role. Move them to another role first.`
              : "The role and its permissions are removed. The audit trail keeps the record."
          }
          confirmLabel="Remove role"
          requireReason
          busy={remove.isPending}
          onConfirm={(reason) => remove.mutate({ code: removing.code, reason })}
          onClose={() => setRemoving(null)}
        />
      ) : null}
    </>
  );
}

function CreateRoleDialog({
  roles,
  onClose,
  onCreated,
}: {
  roles: Role[];
  onClose: () => void;
  onCreated: (role: Role) => void;
}) {
  const queries = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [copyFrom, setCopyFrom] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.roles.create({
        name,
        description: description.trim() || null,
        copy_from: copyFrom || null,
      }),
    onSuccess: (role) => {
      void queries.invalidateQueries({ queryKey: ["roles"] });
      onCreated(role);
    },
  });

  return (
    <Dialog
      title="Create role"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={create.isPending}
            disabled={name.trim().length < 2}
            onClick={() => create.mutate()}
          >
            Create and set permissions
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {create.isError ? <Alert tone="error" title={describe(create.error).message} /> : null}
        <Field label="Name" required>
          {(id) => (
            <TextInput
              id={id}
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Stores Clerk"
            />
          )}
        </Field>
        <Field label="Responsibilities" optional>
          {(id) => (
            <TextInput
              id={id}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Receives, counts and issues stock"
            />
          )}
        </Field>
        <Field label="Start from" hint="Copy another role's permissions, then adjust them.">
          {(id) => (
            <Select id={id} value={copyFrom} onChange={(event) => setCopyFrom(event.target.value)}>
              <option value="">No permissions</option>
              {roles
                .filter((role) => !role.is_administrator)
                .map((role) => (
                  <option key={role.code} value={role.code}>
                    {role.name}
                  </option>
                ))}
            </Select>
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function RenameRoleDialog({ role, onClose }: { role: Role; onClose: () => void }) {
  const queries = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(role.name);
  const [description, setDescription] = useState(role.description ?? "");
  const save = useMutation({
    mutationFn: () => api.roles.amend(role.code, { name, description: description.trim() || null }),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["roles"] });
      toast.show("success", "Role amended.");
      onClose();
    },
  });
  return (
    <Dialog
      title={`Rename ${role.name}`}
      size="sm"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={save.isPending}
            disabled={name.trim().length < 2}
            onClick={() => save.mutate()}
          >
            Save
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <Field label="Name" required>
          {(id) => (
            <TextInput id={id} value={name} onChange={(event) => setName(event.target.value)} />
          )}
        </Field>
        <Field label="Responsibilities" optional>
          {(id) => (
            <TextInput
              id={id}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function RolePermissionsDialog({ role, onClose }: { role: Role; onClose: () => void }) {
  const queries = useQueryClient();
  const toast = useToast();
  const { grouped } = usePermissions();
  const [granted, setGranted] = useState<Set<string>>(new Set(role.permissions));
  const [reason, setReason] = useState("");
  const [term, setTerm] = useState("");
  const locked = role.is_administrator;

  const save = useMutation({
    mutationFn: () => api.roles.setPermissions(role.code, Array.from(granted), reason),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["roles"] });
      toast.show("success", `${role.name}: permissions saved.`);
      onClose();
    },
  });

  const toggle = (codes: string[], on: boolean) =>
    setGranted((current) => {
      const next = new Set(current);
      for (const code of codes) {
        if (on) next.add(code);
        else next.delete(code);
      }
      return next;
    });

  const changed =
    granted.size !== role.permissions.length || role.permissions.some((code) => !granted.has(code));

  return (
    <Dialog
      title={`Permissions: ${role.name}`}
      size="lg"
      onClose={onClose}
      footer={
        <>
          <span className="caption access__count">{granted.size} granted</span>
          <Button tone="plain" onClick={onClose}>
            {locked ? "Close" : "Cancel"}
          </Button>
          {locked ? null : (
            <Button
              tone="primary"
              busy={save.isPending}
              disabled={!changed || !reason.trim()}
              onClick={() => save.mutate()}
            >
              Save permissions
            </Button>
          )}
        </>
      }
    >
      <div className="stack gap-4">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        {locked ? (
          <Alert tone="info" title="The administrator holds every permission">
            To give someone a narrower set, create a role for it.
          </Alert>
        ) : (
          <div className="access__toolbar">
            <SearchField
              label="Find a permission"
              placeholder="Find a permission"
              value={term}
              onChange={setTerm}
            />
            <Field label="Reason" required>
              {(id) => (
                <TextInput
                  id={id}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Recorded in the audit trail"
                />
              )}
            </Field>
          </div>
        )}
        {Array.from(grouped.entries()).map(([group, items]) => {
          const shown = items.filter((item) => matches(item, term));
          if (shown.length === 0) return null;
          const held = items.filter((item) => granted.has(item.code)).length;
          return (
            <fieldset className="fieldset access__group" key={group}>
              <legend className="fieldset__legend">
                {group}
                <span className="access__held">
                  {locked ? "All" : `${held} of ${items.length}`}
                </span>
              </legend>
              {locked ? null : (
                <div className="row gap-2">
                  <Button
                    size="compact"
                    tone="plain"
                    disabled={held === items.length}
                    onClick={() =>
                      toggle(
                        items.map((item) => item.code),
                        true,
                      )
                    }
                  >
                    Grant all
                  </Button>
                  <Button
                    size="compact"
                    tone="plain"
                    disabled={held === 0}
                    onClick={() =>
                      toggle(
                        items.map((item) => item.code),
                        false,
                      )
                    }
                  >
                    Withdraw all
                  </Button>
                </div>
              )}
              <div className="form-grid">
                {shown.map((permission) => (
                  <Checkbox
                    key={permission.code}
                    label={permission.name}
                    hint={
                      permission.sensitive
                        ? `Sensitive. ${permission.description}`
                        : permission.description
                    }
                    checked={locked || granted.has(permission.code)}
                    disabled={locked}
                    onChange={(checked) => toggle([permission.code], checked)}
                  />
                ))}
              </div>
            </fieldset>
          );
        })}
      </div>
    </Dialog>
  );
}

// -- a person's access ------------------------------------------------------------------------

function UsersView() {
  const users = useQuery({ queryKey: ["users", false], queryFn: () => api.users.list(false) });
  const roles = useQuery({ queryKey: ["roles"], queryFn: api.roles.list });
  const names = new Map((roles.data ?? []).map((role) => [role.code, role.name]));
  const [selected, setSelected] = useState<string | null>(null);
  const current = selected ?? users.data?.[0]?.id ?? null;

  return (
    <div className="access">
      <Card title="People" flush>
        {users.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={4} />
          </div>
        ) : (
          <div className="access__people" role="listbox" aria-label="People">
            {(users.data ?? []).map((user) => (
              <PersonRow
                key={user.id}
                user={user}
                roles={user.role_codes.map((code) => names.get(code) ?? code).join(", ")}
                selected={user.id === current}
                onSelect={() => setSelected(user.id)}
              />
            ))}
          </div>
        )}
      </Card>
      {current ? <PersonAccess userId={current} key={current} /> : null}
    </div>
  );
}

function PersonRow({
  user,
  roles,
  selected,
  onSelect,
}: {
  user: User;
  roles: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      className="access__person"
      onClick={onSelect}
    >
      <span className="access__person-name">{user.full_name}</span>
      <span className="cell-note">
        {user.username} · {roles}
      </span>
    </button>
  );
}

type Choice = "role" | "grant" | "withhold";

function PersonAccess({ userId }: { userId: string }) {
  const { actor, can } = useSession();
  const toast = useToast();
  const queries = useQueryClient();
  const { grouped } = usePermissions();
  const access = useQuery({
    queryKey: ["access", userId],
    queryFn: () => api.users.access(userId),
  });
  const sessions = useQuery({
    queryKey: ["access", userId, "sessions"],
    queryFn: () => api.users.sessions(userId),
  });
  const [draft, setDraft] = useState<Record<string, Choice> | null>(null);
  const [reason, setReason] = useState("");
  const [term, setTerm] = useState("");
  const [onlyExceptions, setOnlyExceptions] = useState(false);
  const [ending, setEnding] = useState(false);

  const data = access.data;
  const saved = useMemo(() => exceptionsOf(data), [data]);
  const choices = draft ?? saved;
  const self = actor?.user_id === userId;
  const locked = !can("role.manage") || self || Boolean(data?.is_administrator);

  const save = useMutation({
    mutationFn: () =>
      api.users.setExceptions(
        userId,
        Object.entries(choices)
          .filter(([, choice]) => choice !== "role")
          .map(([permission_code, choice]) => ({
            permission_code,
            effect: choice as "grant" | "withhold",
          })),
        reason,
      ),
    onSuccess: (next) => {
      queries.setQueryData(["access", userId], next);
      setDraft(null);
      setReason("");
      toast.show("success", `${next.full_name}: access saved.`);
    },
  });
  const end = useMutation({
    mutationFn: (why: string) => api.users.endSessions(userId, why),
    onSuccess: (result) => {
      void sessions.refetch();
      setEnding(false);
      toast.show("success", `${result.ended} session(s) ended.`);
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  if (!data) {
    return (
      <Card title="Access">
        <Skeleton rows={6} />
      </Card>
    );
  }

  const fromRoles = new Set(data.from_roles);
  const effective = new Set(data.effective);
  const changed = JSON.stringify(choices) !== JSON.stringify(saved);
  const exceptionCount = Object.values(choices).filter((choice) => choice !== "role").length;

  return (
    <div className="stack gap-5">
      <Card
        title={data.full_name}
        subtitle={`${data.role_names.join(", ")} · ${data.effective.length} permissions in force`}
        flush
      >
        <div className="access__body">
          {data.is_administrator ? (
            <Alert tone="info" title="Administrators hold every permission">
              To narrow what this person may do, give them a different role.
            </Alert>
          ) : self ? (
            <Alert tone="info" title="This is your own account">
              Your access is amended by another administrator.
            </Alert>
          ) : null}
          {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
          <div className="access__toolbar">
            <SearchField
              label="Find a permission"
              placeholder="Find a permission"
              value={term}
              onChange={setTerm}
            />
            <Checkbox
              label={`Exceptions only (${exceptionCount})`}
              checked={onlyExceptions}
              onChange={setOnlyExceptions}
            />
          </div>
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th>Permission</th>
                  <th style={{ width: "16%" }}>From roles</th>
                  <th style={{ width: "18%" }}>For this person</th>
                  <th style={{ width: "14%" }}>In force</th>
                </tr>
              </thead>
              <tbody>
                {Array.from(grouped.entries()).map(([group, items]) => {
                  const shown = items.filter(
                    (item) =>
                      matches(item, term) &&
                      (!onlyExceptions || (choices[item.code] ?? "role") !== "role"),
                  );
                  if (shown.length === 0) return null;
                  return [
                    <tr key={group} className="access__group-row">
                      <th colSpan={4} scope="colgroup">
                        {group}
                      </th>
                    </tr>,
                    ...shown.map((permission) => {
                      const inRoles = data.is_administrator || fromRoles.has(permission.code);
                      const choice = choices[permission.code] ?? "role";
                      const allowed = draft
                        ? choice === "grant" || (inRoles && choice !== "withhold")
                        : effective.has(permission.code);
                      return (
                        <tr key={permission.code}>
                          <td>
                            <div>{permission.name}</div>
                            <div className="cell-note">
                              {permission.sensitive ? "Sensitive. " : ""}
                              {permission.description}
                            </div>
                          </td>
                          <td>{inRoles ? "Yes" : "No"}</td>
                          <td>
                            <Select
                              className="access__choice"
                              aria-label={`${permission.name} for ${data.full_name}`}
                              value={choice}
                              disabled={locked}
                              onChange={(event) =>
                                setDraft({
                                  ...choices,
                                  [permission.code]: event.target.value as Choice,
                                })
                              }
                            >
                              <option value="role">Follow roles</option>
                              {inRoles ? (
                                <option value="withhold">Withhold</option>
                              ) : (
                                <option value="grant">Grant</option>
                              )}
                            </Select>
                          </td>
                          <td>
                            <StatusChip tone={allowed ? "ok" : "neutral"}>
                              {allowed ? "Allowed" : "Not allowed"}
                            </StatusChip>
                          </td>
                        </tr>
                      );
                    }),
                  ];
                })}
              </tbody>
            </table>
          </div>
          {locked ? null : (
            <div className="access__save">
              <Field
                label="Reason"
                required
                hint="Recorded with each exception and in the audit trail."
              >
                {(id) => (
                  <TextInput
                    id={id}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="Trusted with counter discounts from 1 December"
                  />
                )}
              </Field>
              <div className="row gap-2">
                <Button tone="plain" disabled={!changed} onClick={() => setDraft(null)}>
                  Discard changes
                </Button>
                <Button
                  tone="primary"
                  busy={save.isPending}
                  disabled={!changed || !reason.trim()}
                  onClick={() => save.mutate()}
                >
                  Save access
                </Button>
              </div>
            </div>
          )}
          {data.exceptions.length > 0 ? (
            <div className="stack gap-2">
              <h3 className="section-label">Exceptions on record</h3>
              <ul className="access__exceptions">
                {data.exceptions.map((item) => (
                  <li key={item.permission_code}>
                    <strong>{item.effect === "grant" ? "Granted" : "Withheld"}</strong>{" "}
                    {item.permission_code} · {item.reason}
                    <span className="cell-note">
                      {" "}
                      · {item.set_by ?? "System"}, {dateTime(item.set_at)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </Card>

      <Card
        title="Sessions in force"
        subtitle="Devices this person is signed in on."
        actions={
          can("user.manage") && (sessions.data?.length ?? 0) > 0 ? (
            <Button onClick={() => setEnding(true)}>Sign out everywhere</Button>
          ) : undefined
        }
        flush
      >
        {(sessions.data ?? []).length === 0 ? (
          <EmptyState title="Not signed in anywhere." />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Device</th>
                <th>Signed in</th>
                <th>Last active</th>
                <th>Expires</th>
              </tr>
            </thead>
            <tbody>
              {(sessions.data ?? []).map((item) => (
                <tr key={item.id}>
                  <td className="table__cell--wrap">{item.device ?? "Unknown device"}</td>
                  <td>{dateTime(item.issued_at)}</td>
                  <td>{relative(item.last_seen_at)}</td>
                  <td>{dateTime(item.expires_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {ending ? (
        <ConfirmDialog
          title={`Sign ${data.full_name} out everywhere`}
          record={data.full_name}
          consequence="Every session ends at once. They sign in again to continue."
          confirmLabel="Sign out everywhere"
          requireReason
          busy={end.isPending}
          onConfirm={(why) => end.mutate(why)}
          onClose={() => setEnding(false)}
        />
      ) : null}
    </div>
  );
}

function exceptionsOf(access: UserAccess | undefined): Record<string, Choice> {
  const result: Record<string, Choice> = {};
  for (const item of access?.exceptions ?? []) result[item.permission_code] = item.effect;
  return result;
}

// -- the sign-in policy -----------------------------------------------------------------------

const NUMBERS: { key: keyof AccessPolicyTerms; label: string; unit: string; hint: string }[] = [
  { key: "minimum_length", label: "Minimum length", unit: "characters", hint: "8 to 64." },
  {
    key: "reuse_window",
    label: "Earlier passwords refused",
    unit: "passwords",
    hint: "0 allows any earlier password again.",
  },
  {
    key: "lockout_threshold",
    label: "Lock after",
    unit: "failed attempts",
    hint: "3 to 20.",
  },
  { key: "lockout_minutes", label: "Lock for", unit: "minutes", hint: "Up to 24 hours." },
  {
    key: "idle_minutes",
    label: "Sign out when idle for",
    unit: "minutes",
    hint: "5 minutes to 8 hours.",
  },
  {
    key: "absolute_hours",
    label: "Sign out after",
    unit: "hours",
    hint: "However active; 1 to 24.",
  },
];

const RULES: { key: keyof AccessPolicyTerms; label: string }[] = [
  { key: "require_uppercase", label: "An uppercase letter" },
  { key: "require_lowercase", label: "A lowercase letter" },
  { key: "require_digit", label: "A digit" },
  { key: "require_symbol", label: "A symbol" },
];

function PolicyView() {
  const { can } = useSession();
  const toast = useToast();
  const queries = useQueryClient();
  const policy = useQuery({ queryKey: ["access-policy"], queryFn: api.accessPolicy.get });
  const [draft, setDraft] = useState<Record<string, string | boolean> | null>(null);
  const [reason, setReason] = useState("");
  const manage = can("role.manage");

  const save = useMutation({
    mutationFn: (terms: AccessPolicyTerms) => api.accessPolicy.set(terms, reason),
    onSuccess: (next) => {
      queries.setQueryData(["access-policy"], next);
      setDraft(null);
      setReason("");
      toast.show("success", "Sign-in policy saved.");
    },
  });

  if (!policy.data) {
    return (
      <Card title="Sign-in policy">
        <Skeleton rows={6} />
      </Card>
    );
  }
  const base = policy.data;
  const value = (key: keyof AccessPolicyTerms): string | number | boolean => {
    const edited = draft?.[key];
    return edited === undefined ? base[key] : edited;
  };
  const set = (key: keyof AccessPolicyTerms, next: string | boolean) =>
    setDraft((current) => ({ ...(current ?? {}), [key]: next }));

  const whole = (key: keyof AccessPolicyTerms) => Number.parseInt(String(value(key)), 10);
  const terms = (): AccessPolicyTerms => ({
    minimum_length: whole("minimum_length"),
    require_uppercase: Boolean(value("require_uppercase")),
    require_lowercase: Boolean(value("require_lowercase")),
    require_digit: Boolean(value("require_digit")),
    require_symbol: Boolean(value("require_symbol")),
    reuse_window: whole("reuse_window"),
    lockout_threshold: whole("lockout_threshold"),
    lockout_minutes: whole("lockout_minutes"),
    idle_minutes: whole("idle_minutes"),
    absolute_hours: whole("absolute_hours"),
  });

  return (
    <Card
      title="Sign-in policy"
      subtitle={
        base.is_default
          ? "The installation's defaults are in force until you set your own."
          : "Set by this business. A stricter password rule applies to the next change of password."
      }
      footer={
        manage ? (
          <div className="access__save">
            <Field label="Reason" required>
              {(id) => (
                <TextInput
                  id={id}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Annual security review"
                />
              )}
            </Field>
            <div className="row gap-2">
              <Button tone="plain" disabled={!draft} onClick={() => setDraft(null)}>
                Discard changes
              </Button>
              <Button
                tone="primary"
                busy={save.isPending}
                disabled={!draft || !reason.trim()}
                onClick={() => save.mutate(terms())}
              >
                Save policy
              </Button>
            </div>
          </div>
        ) : undefined
      }
    >
      <div className="stack gap-5">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <div className="access__policy">
          <fieldset className="fieldset">
            <legend className="fieldset__legend">Passwords</legend>
            <div className="stack gap-4">
              {NUMBERS.slice(0, 2).map((item) => (
                <NumberField
                  key={item.key}
                  item={item}
                  value={value(item.key)}
                  disabled={!manage}
                  onChange={(next) => set(item.key, next)}
                />
              ))}
              <div className="stack gap-2">
                <span className="field__label">Each password contains</span>
                {RULES.map((item) => (
                  <Checkbox
                    key={item.key}
                    label={item.label}
                    checked={Boolean(value(item.key))}
                    disabled={!manage}
                    onChange={(checked) => set(item.key, checked)}
                  />
                ))}
              </div>
            </div>
          </fieldset>
          <fieldset className="fieldset">
            <legend className="fieldset__legend">Failed sign-in attempts</legend>
            <div className="stack gap-4">
              {NUMBERS.slice(2, 4).map((item) => (
                <NumberField
                  key={item.key}
                  item={item}
                  value={value(item.key)}
                  disabled={!manage}
                  onChange={(next) => set(item.key, next)}
                />
              ))}
            </div>
          </fieldset>
          <fieldset className="fieldset">
            <legend className="fieldset__legend">Sessions</legend>
            <div className="stack gap-4">
              {NUMBERS.slice(4).map((item) => (
                <NumberField
                  key={item.key}
                  item={item}
                  value={value(item.key)}
                  disabled={!manage}
                  onChange={(next) => set(item.key, next)}
                />
              ))}
            </div>
          </fieldset>
        </div>
        <p className="caption">In force now: {base.requirements.join(" · ")}.</p>
      </div>
    </Card>
  );
}

function NumberField({
  item,
  value,
  disabled,
  onChange,
}: {
  item: (typeof NUMBERS)[number];
  value: string | number | boolean;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <Field label={item.label} hint={item.hint}>
      {(id, describedBy) => (
        <div className="input-group">
          <TextInput
            id={id}
            numeric
            inputMode="numeric"
            aria-describedby={describedBy}
            value={String(value)}
            disabled={disabled}
            onChange={(event) => onChange(event.target.value.replace(/[^0-9]/g, ""))}
          />
          <span className="input-group__affix">{item.unit}</span>
        </div>
      )}
    </Field>
  );
}
