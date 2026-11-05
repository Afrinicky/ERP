/**
 * Sign-in.
 *
 * Every failure returns one message, so the screen does not disclose whether a
 * username exists.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { api, ServiceError } from '@/api/client';
import { Alert, Button, Card, Field, TextInput } from '@/components/primitives';
import { describe } from '@/lib/errors';
import { useSession } from '@/lib/session';

export function SignIn() {
  const { signIn } = useSession();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const health = useQuery({
    queryKey: ['health', 'public'],
    queryFn: api.platform.health,
    refetchInterval: 30_000,
  });

  const failure = health.data?.checks.find(
    (check) => check.state === 'failed' && check.name !== 'Network',
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await signIn(username.trim(), password);
    } catch (caught) {
      setError(caught);
      setPassword('');
    } finally {
      setBusy(false);
    }
  }

  const locked = error instanceof ServiceError && error.code === 'account_locked';

  return (
    <div className="centred">
      <div className="centred__panel">
        <div className="brand-lockup">
          <span className="brand-lockup__mark" aria-hidden="true">
            N
          </span>
          <span className="brand-lockup__text">
            <span className="brand-lockup__product">Nickland ERP</span>
            <span className="brand-lockup__edition">Enterprise edition</span>
          </span>
        </div>

        {failure ? (
          <div style={{ paddingBottom: 'var(--space-4)' }}>
            <Alert tone="error" title={`${failure.name} is not available`}>
              {failure.detail}
            </Alert>
          </div>
        ) : null}

        <form onSubmit={submit}>
          <Card
            title="Sign in"
            footer={
              <Button
                type="submit"
                tone="primary"
                block
                busy={busy}
                disabled={!username.trim() || !password}
              >
                Sign in
              </Button>
            }
          >
            <div className="stack gap-4">
              {error ? (
                <Alert tone={locked ? 'warning' : 'error'} title={locked ? 'Account locked' : undefined}>
                  {describe(error).message}
                </Alert>
              ) : null}

              <Field label="Username" required>
                {(id) => (
                  <TextInput
                    id={id}
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    autoComplete="username"
                    autoFocus
                    autoCapitalize="none"
                    spellCheck={false}
                  />
                )}
              </Field>

              <Field label="Password" required>
                {(id) => (
                  <TextInput
                    id={id}
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete="current-password"
                  />
                )}
              </Field>
            </div>
          </Card>
        </form>

        <p className="caption" style={{ paddingTop: 'var(--space-4)', textAlign: 'center' }}>
          Core workflows operate without an internet connection.
        </p>
      </div>
    </div>
  );
}
