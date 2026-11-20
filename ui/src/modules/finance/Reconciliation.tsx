/**
 * Reconciliation: a statement matched line by line to the ledger, and every source of money
 * checked against what it posted.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { StatementLineRecord, TreasuryMovementKindName } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Select,
  Skeleton,
  StatusChip,
  TextArea,
  TextInput,
  useToast,
  type Tone,
} from "@/components/primitives";
import { isNegative, isZero, magnitude } from "@/lib/decimal";
import { describe } from "@/lib/errors";
import { date as formatDate, money } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

const LINE_TONE: Record<StatementLineRecord["state"], Tone> = {
  unmatched: "warning",
  matched: "ok",
  duplicate: "neutral",
  excluded: "neutral",
};

const LINE_LABEL: Record<StatementLineRecord["state"], string> = {
  unmatched: "Unmatched",
  matched: "Matched",
  duplicate: "Duplicate",
  excluded: "Excluded",
};

export function Reconciliation() {
  const { can } = useSession();
  const currency = useCurrency();
  const [importing, setImporting] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const year = new Date().getFullYear();
  const statements = useQuery({
    queryKey: ["reconciliation", "statements"],
    queryFn: () => api.reconciliation.statements(),
  });
  const sources = useQuery({
    queryKey: ["reconciliation", "sources", year],
    queryFn: () => api.reconciliation.sources(`${year}-01-01`, `${year}-12-31`),
  });

  return (
    <div className="stack gap-6">
      <Card
        title="Every source of money"
        subtitle={`Records against their postings, ${year}.`}
        flush
      >
        {sources.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={5} />
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th className="numeric">Records</th>
                <th className="numeric">Recorded</th>
                <th className="numeric">In the ledger</th>
                <th className="numeric">Difference</th>
                <th>Standing</th>
              </tr>
            </thead>
            <tbody>
              {(sources.data ?? []).map((row) => (
                <tr key={row.source}>
                  <td>
                    {row.label}
                    {row.notes.length ? (
                      <div className="cell-note">{row.notes.join(" · ")}</div>
                    ) : null}
                  </td>
                  <td className="numeric">{row.records}</td>
                  <td className="numeric">{money(row.recorded, currency)}</td>
                  <td className="numeric">{money(row.posted, currency)}</td>
                  <td className="numeric">
                    {isZero(row.difference) ? "—" : money(row.difference, currency)}
                  </td>
                  <td>
                    <StatusChip tone={row.agrees ? "ok" : "warning"}>
                      {row.agrees ? "Agrees" : row.records === 0 ? "Not yet reconciled" : "Differs"}
                    </StatusChip>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card
        title="Statements"
        subtitle="Bank and Mobile Money statements, matched to the ledger."
        actions={
          can("reconciliation.manage") ? (
            <Button tone="primary" onClick={() => setImporting(true)}>
              Import a statement
            </Button>
          ) : undefined
        }
        flush
      >
        {(statements.data ?? []).length === 0 ? (
          <EmptyState title="No statement has been imported." />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Statement</th>
                <th>Account</th>
                <th>Period</th>
                <th className="numeric">Unmatched</th>
                <th className="numeric">Difference</th>
                <th>Standing</th>
              </tr>
            </thead>
            <tbody>
              {(statements.data ?? []).map((item) => (
                <tr key={item.id}>
                  <td>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => setOpenId(item.id)}
                    >
                      {item.reference}
                    </button>
                  </td>
                  <td>{item.account}</td>
                  <td>
                    {formatDate(item.starts_on)} to {formatDate(item.ends_on)}
                  </td>
                  <td className="numeric">{item.unmatched}</td>
                  <td className="numeric">
                    {isZero(item.difference) ? "—" : money(item.difference, currency)}
                  </td>
                  <td>
                    <StatusChip tone={item.reconciled ? "ok" : "warning"}>
                      {item.state === "reconciled" ? "Reconciled" : "Open"}
                    </StatusChip>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {importing ? (
        <ImportDialog
          onClose={() => setImporting(false)}
          onImported={(id) => {
            setImporting(false);
            setOpenId(id);
          }}
        />
      ) : null}
      {openId ? <StatementDialog statementId={openId} onClose={() => setOpenId(null)} /> : null}
    </div>
  );
}

function ImportDialog({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: (id: string) => void;
}) {
  const queries = useQueryClient();
  const accounts = useQuery({
    queryKey: ["treasury", "accounts"],
    queryFn: () => api.treasury.accounts(),
  });
  const [account, setAccount] = useState("");
  const [reference, setReference] = useState("");
  const [closing, setClosing] = useState("");
  const [content, setContent] = useState("");
  const banks = (accounts.data ?? []).filter(
    (item) => item.kind === "bank" || item.kind === "mobile_money",
  );

  const save = useMutation({
    mutationFn: () =>
      api.reconciliation.importStatement({
        financial_account_id: account || banks[0]!.id,
        content,
        closing_balance: closing,
        reference,
      }),
    onSuccess: (made) => {
      void queries.invalidateQueries({ queryKey: ["reconciliation"] });
      onImported(made.id);
    },
  });

  return (
    <Dialog
      title="Import a statement"
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={save.isPending}
            disabled={!content || !closing || !reference}
            onClick={() => save.mutate()}
          >
            Import
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <div className="form-grid">
          <Field label="Account" required>
            {(id) => (
              <Select id={id} value={account} onChange={(e) => setAccount(e.target.value)}>
                {banks.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Statement" required hint="As the bank numbers it.">
            {(id) => (
              <TextInput
                id={id}
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder="GCB 2026/11"
              />
            )}
          </Field>
          <Field label="Closing balance" required>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={closing}
                onChange={(e) => setClosing(e.target.value)}
                placeholder="0.00"
              />
            )}
          </Field>
          <Field label="File" optional hint="A CSV exported from the bank or provider.">
            {(id) => (
              <input
                id={id}
                type="file"
                accept=".csv,text/csv"
                className="input"
                onChange={async (event) => {
                  const file = event.target.files?.[0];
                  if (file) setContent(await file.text());
                }}
              />
            )}
          </Field>
        </div>
        <Field
          label="Statement lines"
          required
          hint="A header row naming date, description, reference, and either amount or debit and credit."
        >
          {(id) => (
            <TextArea
              id={id}
              rows={8}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={
                "Date,Description,Reference,Debit,Credit\n05/11/2026,Cheque 004512,CHQ-004512,250.00,"
              }
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function StatementDialog({ statementId, onClose }: { statementId: string; onClose: () => void }) {
  const currency = useCurrency();
  const toast = useToast();
  const queries = useQueryClient();
  const { can } = useSession();
  const statement = useQuery({
    queryKey: ["reconciliation", "statement", statementId],
    queryFn: () => api.reconciliation.statement(statementId),
  });
  const [working, setWorking] = useState<StatementLineRecord | null>(null);
  const refresh = () => {
    void queries.invalidateQueries({ queryKey: ["reconciliation"] });
    void queries.invalidateQueries({ queryKey: ["treasury"] });
  };
  const auto = useMutation({
    mutationFn: () => api.reconciliation.autoMatch(statementId),
    onSuccess: (result) => {
      refresh();
      toast.show("success", `${result.matched} line(s) matched.`);
    },
  });
  const complete = useMutation({
    mutationFn: () => api.reconciliation.complete(statementId),
    onSuccess: () => {
      refresh();
      toast.show("success", "Statement reconciled.");
    },
    onError: (error) => toast.show("error", describe(error).message),
  });
  const data = statement.data;
  const manage = can("reconciliation.manage") && data?.state !== "reconciled";

  return (
    <Dialog title={data ? `Statement ${data.reference}` : "Statement"} size="lg" onClose={onClose}>
      {!data ? (
        <Skeleton rows={8} />
      ) : (
        <div className="stack gap-5">
          <div className="recon__figures">
            <div>
              <span className="caption">Statement closes at</span>
              <strong>{money(data.closing_balance, currency)}</strong>
            </div>
            <div>
              <span className="caption">The ledger holds</span>
              <strong>{money(data.ledger_balance, currency)}</strong>
            </div>
            <div>
              <span className="caption">Unexplained</span>
              <strong>{money(data.difference, currency)}</strong>
            </div>
            <div>
              <span className="caption">Unmatched lines</span>
              <strong>{data.unmatched}</strong>
            </div>
          </div>
          {manage ? (
            <div className="row gap-2">
              <Button busy={auto.isPending} onClick={() => auto.mutate()}>
                Match what is certain
              </Button>
              <Button
                tone="primary"
                busy={complete.isPending}
                disabled={!data.reconciled}
                onClick={() => complete.mutate()}
              >
                Mark reconciled
              </Button>
            </div>
          ) : null}
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Description</th>
                  <th className="numeric">In</th>
                  <th className="numeric">Out</th>
                  <th>Standing</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.lines.map((line) => (
                  <tr key={line.id}>
                    <td>{formatDate(line.line_date)}</td>
                    <td className="table__cell--wrap">
                      {line.description}
                      {line.reference ? <div className="cell-note">{line.reference}</div> : null}
                    </td>
                    <td className="numeric">
                      {isNegative(line.amount) ? "" : money(line.amount, currency)}
                    </td>
                    <td className="numeric">
                      {isNegative(line.amount) ? money(magnitude(line.amount), currency) : ""}
                    </td>
                    <td>
                      <StatusChip tone={LINE_TONE[line.state]}>{LINE_LABEL[line.state]}</StatusChip>
                    </td>
                    <td>
                      {manage && line.state === "unmatched" ? (
                        <Button size="compact" tone="plain" onClick={() => setWorking(line)}>
                          Match
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {working ? (
        <MatchDialog
          line={working}
          onClose={() => setWorking(null)}
          onDone={() => {
            setWorking(null);
            refresh();
          }}
        />
      ) : null}
    </Dialog>
  );
}

function MatchDialog({
  line,
  onClose,
  onDone,
}: {
  line: StatementLineRecord;
  onClose: () => void;
  onDone: () => void;
}) {
  const currency = useCurrency();
  const suggestions = useQuery({
    queryKey: ["reconciliation", "suggestions", line.id],
    queryFn: () => api.reconciliation.suggestions(line.id),
  });
  const outward = isNegative(line.amount);
  const [kind, setKind] = useState<TreasuryMovementKindName>(outward ? "bank_charge" : "interest");
  const match = useMutation({
    mutationFn: (journalLineId: string) => api.reconciliation.match(line.id, [journalLineId]),
    onSuccess: onDone,
  });
  const post = useMutation({
    mutationFn: () => api.reconciliation.post(line.id, kind, line.description),
    onSuccess: onDone,
  });
  const failure = match.error ?? post.error;

  return (
    <Dialog title={`Match ${money(line.amount, currency)}`} onClose={onClose}>
      <div className="stack gap-4">
        {failure ? <Alert tone="error" title={describe(failure).message} /> : null}
        <p className="caption">
          {formatDate(line.line_date)} · {line.description}
        </p>
        <h3 className="section-label">Postings of the same amount within three days</h3>
        {(suggestions.data ?? []).length === 0 ? (
          <p className="cell-note">
            None. If the bank did something not yet recorded, record it here.
          </p>
        ) : (
          <div className="register__matches">
            {(suggestions.data ?? []).map((item) => (
              <button
                key={item.item.journal_line_id}
                type="button"
                className="register__match"
                onClick={() => match.mutate(item.item.journal_line_id)}
              >
                <span>
                  {item.item.entry_number} · {item.item.narration}
                  <span className="cell-note"> {formatDate(item.item.posting_date)}</span>
                </span>
                <span className="numeric">{money(item.item.amount, currency)}</span>
              </button>
            ))}
          </div>
        )}
        <h3 className="section-label">Or record it from the statement</h3>
        <div className="row gap-2">
          <Select
            aria-label="Record as"
            value={kind}
            onChange={(e) => setKind(e.target.value as TreasuryMovementKindName)}
          >
            {outward ? (
              <>
                <option value="bank_charge">Bank charge</option>
                <option value="adjustment_out">Adjustment out</option>
              </>
            ) : (
              <>
                <option value="interest">Interest received</option>
                <option value="adjustment_in">Adjustment in</option>
              </>
            )}
          </Select>
          <Button busy={post.isPending} onClick={() => post.mutate()}>
            Record and match
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
