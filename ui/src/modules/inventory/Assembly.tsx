/**
 * Order assembly: reserve stock for each line, stage it at the assembly bay, dispatch.
 *
 * Quantities are entered with the transactional stepper, and each step is one large
 * action labelled for what it does, per the design system's transactional controls.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { AssemblyLine, AssemblyWorkspace } from "@/api/types";
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
  Stepper,
  TextArea,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { quantity } from "@/lib/format";
import { useSession } from "@/lib/session";

import { serialList } from "./shared";

type Step = "reserve" | "release" | "assemble" | "dispatch";

const STEP: Record<Step, { title: string; action: string; limit: (line: AssemblyLine) => string }> =
  {
    reserve: { title: "Reserve stock", action: "Reserve", limit: (line) => line.to_reserve },
    release: { title: "Release reservation", action: "Release", limit: (line) => line.stageable },
    assemble: { title: "Stage for dispatch", action: "Stage", limit: (line) => line.stageable },
    dispatch: { title: "Dispatch to customer", action: "Dispatch", limit: (line) => line.staged },
  };

export function Assembly({ canAssemble }: { canAssemble: boolean }) {
  const [orderId, setOrderId] = useState("");
  const orders = useQuery({
    queryKey: ["sales", "orders", "open"],
    queryFn: async () => {
      const [open, partial] = await Promise.all([
        api.orders.list("open", 100),
        api.orders.list("partially_fulfilled", 100),
      ]);
      return [...open, ...partial];
    },
  });
  const workspace = useQuery({
    queryKey: ["inventory", "assembly", orderId],
    queryFn: () => api.inventory.assembly(orderId),
    enabled: orderId !== "",
  });

  return (
    <div className="stack gap-5">
      <Card title="Customer order">
        {orders.isPending ? (
          <Skeleton rows={1} />
        ) : (orders.data ?? []).length === 0 ? (
          <EmptyState title="No customer order is awaiting assembly." />
        ) : (
          <div className="form-grid">
            <Field label="Order" required>
              {(id) => (
                <Select
                  id={id}
                  value={orderId}
                  onChange={(event) => setOrderId(event.target.value)}
                >
                  <option value="">Select an order</option>
                  {(orders.data ?? []).map((order) => (
                    <option key={order.id} value={order.id}>
                      {order.number} · {order.customer_name ?? "Customer"}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
        )}
      </Card>

      {orderId && workspace.isPending ? <Skeleton rows={4} height={48} /> : null}
      {workspace.isError ? <Alert tone="error" title={describe(workspace.error).message} /> : null}
      {workspace.data ? <Lines space={workspace.data} canAssemble={canAssemble} /> : null}
    </div>
  );
}

function Lines({ space, canAssemble }: { space: AssemblyWorkspace; canAssemble: boolean }) {
  const [active, setActive] = useState<{ line: AssemblyLine; step: Step } | null>(null);

  return (
    <Card
      title={`${space.order_number ?? "Order"} · ${space.customer ?? ""}`}
      subtitle={`Assembled at ${space.warehouse}`}
      actions={
        <StatusChip tone={space.ready ? "ok" : "neutral"}>
          {space.ready ? "Ready for dispatch" : "In assembly"}
        </StatusChip>
      }
      flush
    >
      {space.lines.map((line) => (
        <div className="assembly-line" key={line.line_id}>
          <div>
            <span className="cell-strong">{line.description}</span>
            <span className="cell-note">
              {line.sku ?? "Not a stock item"}
              {line.holds_stock ? "" : " · procured and delivered direct"}
            </span>
          </div>
          <Figure label="Required" value={line.required} />
          <Figure label="Available" value={line.available} />
          <Figure label="Reserved" value={line.reserved} />
          <Figure label="Incoming" value={line.incoming} />
          <Figure label="Staged" value={line.staged} />
          <Figure label="Dispatched" value={line.dispatched} />
          <div className="assembly-line__actions">
            {canAssemble && line.holds_stock ? (
              <>
                {Number(line.to_reserve) > 0 ? (
                  <Button
                    size="transactional"
                    tone="secondary"
                    disabled={Number(line.available) <= 0}
                    onClick={() => setActive({ line, step: "reserve" })}
                  >
                    Reserve
                  </Button>
                ) : null}
                {Number(line.stageable) > 0 ? (
                  <Button
                    size="transactional"
                    tone="secondary"
                    onClick={() => setActive({ line, step: "assemble" })}
                  >
                    Stage
                  </Button>
                ) : null}
                {Number(line.staged) > 0 ? (
                  <Button
                    tone="primary"
                    size="transactional"
                    onClick={() => setActive({ line, step: "dispatch" })}
                  >
                    Dispatch
                  </Button>
                ) : null}
                {Number(line.stageable) > 0 ? (
                  <Button tone="plain" onClick={() => setActive({ line, step: "release" })}>
                    Release
                  </Button>
                ) : null}
              </>
            ) : null}
            {line.holds_stock && Number(line.outstanding) === 0 ? (
              <StatusChip tone="ok">Dispatched</StatusChip>
            ) : null}
          </div>
        </div>
      ))}
      {active ? (
        <StepDialog
          orderId={space.order_id}
          line={active.line}
          step={active.step}
          onClose={() => setActive(null)}
        />
      ) : null}
    </Card>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="assembly-line__figure">
      <span>{label}</span>
      <span>{quantity(value)}</span>
    </div>
  );
}

function StepDialog({
  orderId,
  line,
  step,
  onClose,
}: {
  orderId: string;
  line: AssemblyLine;
  step: Step;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const { can } = useSession();
  const spec = STEP[step];
  const limit = Number(spec.limit(line));
  const [amount, setAmount] = useState(String(limit));
  const [serialText, setSerialText] = useState("");
  const [override, setOverride] = useState("");
  const [needsOverride, setNeedsOverride] = useState(false);

  const run = useMutation({
    mutationFn: () => {
      const payload = {
        quantity: amount,
        serials: serialList(serialText),
        override_reason: override.trim() || null,
      };
      if (step === "reserve") return api.inventory.assemblyReserve(orderId, line.line_id, payload);
      if (step === "release") return api.inventory.assemblyRelease(orderId, line.line_id, payload);
      if (step === "assemble") return api.inventory.assemble(orderId, line.line_id, payload);
      return api.inventory.assemblyDispatch(orderId, line.line_id, payload);
    },
    onSuccess: (space) => {
      client.setQueryData(["inventory", "assembly", orderId], space);
      void client.invalidateQueries({ queryKey: ["inventory"] });
      toast.show(
        "success",
        `${spec.action}: ${quantity(amount)} × ${line.sku ?? line.description}.`,
      );
      onClose();
    },
    onError: (error) => {
      if (step === "dispatch" && describe(error).message.includes("expired"))
        setNeedsOverride(true);
    },
  });

  return (
    <Dialog
      size="sm"
      title={spec.title}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="counter"
            block
            onClick={() => run.mutate()}
            busy={run.isPending}
            disabled={
              Number(amount) <= 0 || Number(amount) > limit || (needsOverride && !override.trim())
            }
          >
            {spec.action} {quantity(amount)}
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        <p className="caption">
          Line {line.position} · {line.description}
        </p>
        {run.isError ? <Alert tone="error" title={describe(run.error).message} /> : null}
        <Field label="Quantity">
          {(id) => (
            <Stepper
              id={id}
              min={0}
              max={limit}
              value={amount}
              onChange={setAmount}
              size="counter"
            />
          )}
        </Field>
        {step === "dispatch" && line.tracking === "serial" ? (
          <Field label="Serial numbers" required hint="One per line">
            {(id) => (
              <TextArea
                id={id}
                rows={3}
                value={serialText}
                onChange={(event) => setSerialText(event.target.value)}
              />
            )}
          </Field>
        ) : null}
        {needsOverride ? (
          can("expiry.override") ? (
            <Field label="Reason for issuing expired stock" required>
              {(id) => (
                <TextInput
                  id={id}
                  value={override}
                  onChange={(event) => setOverride(event.target.value)}
                  placeholder="Customer accepts for training use"
                />
              )}
            </Field>
          ) : (
            <Alert
              tone="warning"
              title="An authorised override is required to issue expired stock."
            />
          )
        ) : null}
      </div>
    </Dialog>
  );
}
