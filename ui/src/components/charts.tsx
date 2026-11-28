/**
 * Charts, drawn as SVG in the page: nothing is fetched, so they work with no network.
 *
 * One axis per chart. A series that is a level (a balance) rather than a flow is drawn
 * on a chart of its own beneath, so two scales never share an axis. Colour follows the
 * series in the order the service gives them, from the theme's chart tokens; text never
 * wears it. Anything that is not an actual posted figure — a budget, a forecast, a
 * quotation's estimate — is drawn dashed or hatched and labelled with its basis in the
 * legend, so it cannot be taken for a result.
 *
 * Every chart has a table view and exports as CSV or SVG. Selecting a point, bar or row
 * calls back with the point, so the caller can open the records it is made of.
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { ChartData, ChartPoint, ChartSeries } from "@/api/types";
import { Button, EmptyState } from "@/components/primitives";
import { money } from "@/lib/format";
import type { CurrencyProfile } from "@/lib/format";

const SLOTS = 4;

function slot(index: number): string {
  return `var(--colour-chart-${(index % SLOTS) + 1})`;
}

function figure(text: string | undefined): number {
  const parsed = Number(text ?? "0");
  return Number.isFinite(parsed) ? parsed : 0;
}

function compact(v: number): string {
  const size = Math.abs(v);
  if (size >= 1_000_000) return `${(v / 1_000_000).toFixed(size >= 10_000_000 ? 0 : 1)}M`;
  if (size >= 10_000) return `${Math.round(v / 1_000)}K`;
  return Math.round(v).toLocaleString("en-GB");
}

/** Round ticks that cover the range and include zero. */
function ticks(low: number, high: number, count = 4): number[] {
  const floor = Math.min(0, low);
  const ceiling = Math.max(0, high);
  const span = ceiling - floor || 1;
  const rough = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((m) => m >= rough) ?? rough;
  const first = Math.floor(floor / step) * step;
  const last = Math.ceil(ceiling / step) * step;
  const marks: number[] = [];
  for (let t = first; t <= last + step / 2; t += step) marks.push(Number(t.toFixed(6)));
  return marks;
}

function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width;
      if (next && Math.abs(next - width) > 1) setWidth(Math.max(280, next));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [width]);
  return [ref, width];
}

function estimated(series: ChartSeries): boolean {
  return series.basis !== "actual";
}

export interface ChartCardProps {
  chart: ChartData;
  currency: CurrencyProfile;
  onSelect?: (point: ChartPoint, series: ChartSeries | null) => void;
  /** The series drawn as marks. Others appear in the tooltip and the table only. */
  plotted?: string[];
  height?: number;
}

/** A chart, its legend, its table view and its exports. */
export function ChartPanel({ chart, currency, onSelect, plotted, height = 240 }: ChartCardProps) {
  const [asTable, setAsTable] = useState(false);
  const svgHost = useRef<HTMLDivElement>(null);
  const flows = chart.series.filter((series) => series.role !== "level");
  const levels = chart.series.filter((series) => series.role === "level");
  const drawn =
    chart.kind === "bars"
      ? flows.filter((series, index) => (plotted ? plotted.includes(series.key) : index === 0))
      : flows.filter((series) => !plotted || plotted.includes(series.key));
  const empty =
    chart.points.length === 0 ||
    chart.points.every((point) => chart.series.every((s) => figure(point.values[s.key]) === 0));

  const download = (name: string, body: string, type: string) => {
    const blob = new Blob([body], { type });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
  };
  const csv = () => {
    const header = ["Point", ...chart.series.map((s) => `${s.label} (${s.basis_label})`)];
    const rows = chart.points.map((point) => [
      point.label,
      ...chart.series.map((s) => point.values[s.key] ?? ""),
    ]);
    const quote = (cell: string) => `"${cell.replace(/"/g, '""')}"`;
    download(
      `${chart.id}-${chart.starts_on}-${chart.ends_on}.csv`,
      [header, ...rows].map((row) => row.map(quote).join(",")).join("\n"),
      "text/csv",
    );
  };
  const svg = () => {
    const nodes = svgHost.current?.querySelectorAll("svg");
    if (!nodes || nodes.length === 0) return;
    const styles = getComputedStyle(svgHost.current ?? document.documentElement);
    const parts = Array.from(nodes).map((node) => {
      const copy = node.cloneNode(true) as SVGSVGElement;
      return new XMLSerializer()
        .serializeToString(copy)
        .replace(/var\((--[a-z0-9-]+)\)/g, (_, name: string) =>
          styles.getPropertyValue(name).trim(),
        );
    });
    download(`${chart.id}.svg`, parts.join("\n"), "image/svg+xml");
  };

  return (
    <div className="viz" style={palette(chart)}>
      <div className="viz__head">
        <Legend series={drawn.length > 1 || drawn.some(estimated) ? drawn : []} />
        <div className="viz__tools">
          <Button
            size="compact"
            tone="plain"
            aria-pressed={asTable}
            onClick={() => setAsTable((v) => !v)}
          >
            {asTable ? "Show the chart" : "Show as a table"}
          </Button>
          <Button size="compact" tone="plain" onClick={csv}>
            CSV
          </Button>
          {!asTable ? (
            <Button size="compact" tone="plain" onClick={svg}>
              Image
            </Button>
          ) : null}
        </div>
      </div>
      {empty ? (
        <EmptyState title="Nothing was recorded in this period." />
      ) : asTable ? (
        <ChartTable chart={chart} currency={currency} onSelect={onSelect} />
      ) : (
        <div ref={svgHost} className="stack gap-3">
          {chart.kind === "line" ? (
            <LinePlot
              chart={chart}
              series={drawn}
              currency={currency}
              onSelect={onSelect}
              height={height}
            />
          ) : chart.kind === "columns" ? (
            <ColumnPlot
              chart={chart}
              series={drawn}
              currency={currency}
              onSelect={onSelect}
              height={height}
            />
          ) : (
            <BarPlot chart={chart} series={drawn} currency={currency} onSelect={onSelect} />
          )}
          {levels.length > 0 ? (
            <>
              <Legend series={levels} heading />
              <LinePlot
                chart={chart}
                series={levels}
                currency={currency}
                onSelect={onSelect}
                height={160}
              />
            </>
          ) : null}
        </div>
      )}
      {chart.note ? <p className="caption">{chart.note}</p> : null}
    </div>
  );
}

function Legend({ series, heading }: { series: ChartSeries[]; heading?: boolean }) {
  if (series.length === 0) return <span />;
  return (
    <ul className={`viz__legend${heading ? " viz__legend--heading" : ""}`}>
      {series.map((item) => {
        return (
          <li key={item.key}>
            <svg width="18" height="10" aria-hidden="true">
              {estimated(item) ? (
                <line
                  x1="1"
                  y1="5"
                  x2="17"
                  y2="5"
                  stroke={`var(--viz-${item.key}, currentColor)`}
                  strokeWidth="2"
                  strokeDasharray="4 3"
                />
              ) : (
                <rect
                  x="1"
                  y="1"
                  width="16"
                  height="8"
                  rx="2"
                  fill={`var(--viz-${item.key}, currentColor)`}
                />
              )}
            </svg>
            <span>{item.label}</span>
            {item.basis !== "actual" ? (
              <span className="viz__basis">{item.basis_label}</span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/** Colour variables for the chart's series, by the order the service gave them. */
function palette(chart: ChartData): React.CSSProperties {
  const style: Record<string, string> = {};
  chart.series.forEach((series, index) => {
    style[`--viz-${series.key}`] = slot(index);
  });
  return style as React.CSSProperties;
}

interface PlotProps {
  chart: ChartData;
  series: ChartSeries[];
  currency: CurrencyProfile;
  onSelect?: (point: ChartPoint, series: ChartSeries | null) => void;
  height?: number;
}

const PAD = { top: 12, right: 16, bottom: 28, left: 56 };

function Tooltip({
  point,
  series,
  currency,
  x,
  y,
  width,
}: {
  point: ChartPoint;
  series: ChartSeries[];
  currency: CurrencyProfile;
  x: number;
  y: number;
  width: number;
}) {
  const left = Math.min(Math.max(x + 12, 0), width - 220);
  return (
    <div className="viz__tooltip" style={{ left, top: Math.max(0, y - 8) }} role="status">
      <strong>{point.label}</strong>
      {series.map((item) => (
        <div key={item.key} className="viz__tooltip-row">
          <span className="viz__swatch" style={{ background: `var(--viz-${item.key})` }} />
          <span>{item.label}</span>
          <span className="numeric">{money(point.values[item.key] ?? "0", currency)}</span>
        </div>
      ))}
    </div>
  );
}

function LinePlot({ chart, series, currency, onSelect, height = 240 }: PlotProps) {
  const [host, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const titleId = useId();
  const all = chart.points.flatMap((p) => series.map((s) => figure(p.values[s.key])));
  const marks = ticks(Math.min(...all), Math.max(...all));
  const lowest = marks[0] ?? 0;
  const highest = marks[marks.length - 1] ?? 1;
  const inner = { w: width - PAD.left - PAD.right, h: height - PAD.top - PAD.bottom };
  const count = chart.points.length;
  const xOf = (i: number) => PAD.left + (count <= 1 ? inner.w / 2 : (i * inner.w) / (count - 1));
  const yOf = (v: number) => PAD.top + inner.h - ((v - lowest) / (highest - lowest || 1)) * inner.h;
  const every = Math.max(1, Math.ceil(count / Math.max(1, Math.floor(inner.w / 72))));

  const nearest = (clientX: number, rect: DOMRect) => {
    const local = clientX - rect.left - PAD.left;
    const i = count <= 1 ? 0 : Math.round((local / inner.w) * (count - 1));
    return Math.min(Math.max(i, 0), count - 1);
  };

  return (
    <div ref={host} className="viz__plot" style={palette(chart)}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-labelledby={titleId}
        onMouseMove={(event) =>
          setHover(nearest(event.clientX, event.currentTarget.getBoundingClientRect()))
        }
        onMouseLeave={() => setHover(null)}
        onClick={() => {
          const point = hover !== null ? chart.points[hover] : undefined;
          if (point && onSelect) onSelect(point, null);
        }}
        className={onSelect ? "viz__svg viz__svg--live" : "viz__svg"}
      >
        <title id={titleId}>
          {chart.title}: {series.map((s) => s.label).join(", ")}
        </title>
        {marks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={yOf(t)}
              y2={yOf(t)}
              className={t === 0 ? "viz__zero" : "viz__grid"}
            />
            <text
              x={PAD.left - 8}
              y={yOf(t)}
              className="viz__tick"
              textAnchor="end"
              dominantBaseline="middle"
            >
              {compact(t)}
            </text>
          </g>
        ))}
        {chart.points.map((point, i) =>
          i % every === 0 ? (
            <text
              key={point.key}
              x={xOf(i)}
              y={height - 8}
              className="viz__tick"
              textAnchor="middle"
            >
              {point.label}
            </text>
          ) : null,
        )}
        {hover !== null ? (
          <line
            x1={xOf(hover)}
            x2={xOf(hover)}
            y1={PAD.top}
            y2={PAD.top + inner.h}
            className="viz__crosshair"
          />
        ) : null}
        {series.map((item) => {
          const path = chart.points
            .map(
              (point, i) =>
                `${i === 0 ? "M" : "L"}${xOf(i).toFixed(1)},${yOf(figure(point.values[item.key])).toFixed(1)}`,
            )
            .join(" ");
          const lastIndex = count - 1;
          const lastPoint = chart.points[lastIndex];
          return (
            <g key={item.key}>
              <path
                d={path}
                fill="none"
                stroke={`var(--viz-${item.key})`}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
                strokeDasharray={estimated(item) ? "6 4" : undefined}
              />
              {lastPoint ? (
                <circle
                  cx={xOf(lastIndex)}
                  cy={yOf(figure(lastPoint.values[item.key]))}
                  r={4}
                  fill={`var(--viz-${item.key})`}
                  className="viz__dot"
                />
              ) : null}
              {hover !== null && chart.points[hover] ? (
                <circle
                  cx={xOf(hover)}
                  cy={yOf(figure(chart.points[hover]!.values[item.key]))}
                  r={5}
                  fill={`var(--viz-${item.key})`}
                  className="viz__dot"
                />
              ) : null}
            </g>
          );
        })}
      </svg>
      {hover !== null && chart.points[hover] ? (
        <Tooltip
          point={chart.points[hover]!}
          series={series}
          currency={currency}
          x={xOf(hover)}
          y={PAD.top}
          width={width}
        />
      ) : null}
    </div>
  );
}

function ColumnPlot({ chart, series, currency, onSelect, height = 240 }: PlotProps) {
  const [host, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const titleId = useId();
  const hatch = useId().replace(/:/g, "");
  const all = chart.points.flatMap((p) => series.map((s) => figure(p.values[s.key])));
  const marks = ticks(Math.min(...all), Math.max(...all));
  const lowest = marks[0] ?? 0;
  const highest = marks[marks.length - 1] ?? 1;
  const inner = { w: width - PAD.left - PAD.right, h: height - PAD.top - PAD.bottom };
  const count = Math.max(1, chart.points.length);
  const band = inner.w / count;
  const thick = Math.min(24, Math.max(4, (band * 0.7 - 2 * (series.length - 1)) / series.length));
  const group = thick * series.length + 2 * (series.length - 1);
  const yOf = (v: number) => PAD.top + inner.h - ((v - lowest) / (highest - lowest || 1)) * inner.h;
  const zero = yOf(0);
  const every = Math.max(1, Math.ceil(count / Math.max(1, Math.floor(inner.w / 72))));

  return (
    <div ref={host} className="viz__plot" style={palette(chart)}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-labelledby={titleId}
        className="viz__svg"
        onMouseLeave={() => setHover(null)}
      >
        <title id={titleId}>
          {chart.title}: {series.map((s) => s.label).join(", ")}
        </title>
        <defs>
          <pattern
            id={hatch}
            width="6"
            height="6"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <line
              x1="0"
              y1="0"
              x2="0"
              y2="6"
              stroke="var(--colour-surface-raised)"
              strokeWidth="3"
            />
          </pattern>
        </defs>
        {marks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={yOf(t)}
              y2={yOf(t)}
              className={t === 0 ? "viz__zero" : "viz__grid"}
            />
            <text
              x={PAD.left - 8}
              y={yOf(t)}
              className="viz__tick"
              textAnchor="end"
              dominantBaseline="middle"
            >
              {compact(t)}
            </text>
          </g>
        ))}
        {chart.points.map((point, i) => {
          const start = PAD.left + i * band + (band - group) / 2;
          return (
            <g
              key={point.key}
              className={onSelect ? "viz__band viz__band--live" : "viz__band"}
              onMouseEnter={() => setHover(i)}
              onClick={() => onSelect?.(point, null)}
            >
              <rect
                x={PAD.left + i * band}
                y={PAD.top}
                width={band}
                height={inner.h}
                className="viz__hit"
              />
              {series.map((item, s) => {
                const v = figure(point.values[item.key]);
                const top = Math.min(yOf(v), zero);
                const tall = Math.max(Math.abs(yOf(v) - zero), v === 0 ? 0 : 1);
                const x = start + s * (thick + 2);
                const up = v >= 0;
                const r = Math.min(4, tall);
                const d = up
                  ? `M${x},${zero} V${top + r} Q${x},${top} ${x + r},${top} H${x + thick - r} Q${x + thick},${top} ${x + thick},${top + r} V${zero} Z`
                  : `M${x},${zero} V${zero + tall - r} Q${x},${zero + tall} ${x + r},${zero + tall} H${x + thick - r} Q${x + thick},${zero + tall} ${x + thick},${zero + tall - r} V${zero} Z`;
                return (
                  <g key={item.key}>
                    <path
                      d={d}
                      fill={`var(--viz-${item.key})`}
                      opacity={estimated(item) ? 0.55 : 1}
                    />
                    {estimated(item) ? <path d={d} fill={`url(#${hatch})`} /> : null}
                  </g>
                );
              })}
              {i % every === 0 ? (
                <text
                  x={PAD.left + i * band + band / 2}
                  y={height - 8}
                  className="viz__tick"
                  textAnchor="middle"
                >
                  {point.label}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {hover !== null && chart.points[hover] ? (
        <Tooltip
          point={chart.points[hover]!}
          series={chart.series.filter((s) => s.role !== "level")}
          currency={currency}
          x={PAD.left + hover * band + band / 2}
          y={PAD.top}
          width={width}
        />
      ) : null}
    </div>
  );
}

/** Horizontal bars, ranked: one row per point, one bar per plotted series. */
function BarPlot({ chart, series, currency, onSelect }: PlotProps) {
  const [host, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const titleId = useId();
  const hatch = useId().replace(/:/g, "");
  const shown = chart.points.slice(0, 12);
  const all = shown.flatMap((p) => series.map((s) => figure(p.values[s.key])));
  const low = Math.min(0, ...all);
  const high = Math.max(0, ...all) || 1;
  const labelWidth = Math.min(220, Math.max(120, width * 0.3));
  const valueWidth = 96;
  const track = width - labelWidth - valueWidth - 16;
  const thick = series.length > 1 ? 12 : 16;
  const rowHeight = series.length * (thick + 2) + 14;
  const height = shown.length * rowHeight + 8;
  const xOf = (v: number) => labelWidth + ((v - low) / (high - low)) * track;
  const zero = xOf(0);

  return (
    <div ref={host} className="viz__plot" style={palette(chart)}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-labelledby={titleId}
        className="viz__svg"
        onMouseLeave={() => setHover(null)}
      >
        <title id={titleId}>{chart.title}</title>
        <defs>
          <pattern
            id={hatch}
            width="6"
            height="6"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <line
              x1="0"
              y1="0"
              x2="0"
              y2="6"
              stroke="var(--colour-surface-raised)"
              strokeWidth="3"
            />
          </pattern>
        </defs>
        <line x1={zero} x2={zero} y1={0} y2={height} className="viz__zero" />
        {shown.map((point, i) => {
          const top = i * rowHeight + 6;
          return (
            <g
              key={point.key}
              className={onSelect ? "viz__band viz__band--live" : "viz__band"}
              onMouseEnter={() => setHover(i)}
              onClick={() => onSelect?.(point, series[0] ?? null)}
            >
              <rect x={0} y={top - 4} width={width} height={rowHeight} className="viz__hit" />
              <text
                x={labelWidth - 10}
                y={top + (rowHeight - 14) / 2 + 1}
                className="viz__label"
                textAnchor="end"
                dominantBaseline="middle"
              >
                {point.label.length > 30 ? `${point.label.slice(0, 29)}…` : point.label}
              </text>
              {series.map((item, s) => {
                const v = figure(point.values[item.key]);
                const y = top + s * (thick + 2);
                const left = Math.min(xOf(v), zero);
                const long = Math.max(Math.abs(xOf(v) - zero), v === 0 ? 0 : 1);
                return (
                  <g key={item.key}>
                    <rect
                      x={left}
                      y={y}
                      width={long}
                      height={thick}
                      rx={Math.min(4, long / 2)}
                      fill={`var(--viz-${item.key})`}
                      opacity={estimated(item) ? 0.55 : 1}
                    />
                    {estimated(item) ? (
                      <rect x={left} y={y} width={long} height={thick} fill={`url(#${hatch})`} />
                    ) : null}
                  </g>
                );
              })}
              <text
                x={width - 4}
                y={top + (rowHeight - 14) / 2 + 1}
                className="viz__value"
                textAnchor="end"
                dominantBaseline="middle"
              >
                {money(point.values[series[series.length - 1]?.key ?? ""] ?? "0", currency)}
              </text>
            </g>
          );
        })}
      </svg>
      {hover !== null && shown[hover] ? (
        <Tooltip
          point={shown[hover]!}
          series={chart.series}
          currency={currency}
          x={labelWidth}
          y={hover * rowHeight}
          width={width}
        />
      ) : null}
      {chart.points.length > shown.length ? (
        <p className="caption">
          The largest 12 are drawn; the table view lists all {chart.points.length}.
        </p>
      ) : null}
    </div>
  );
}

function ChartTable({
  chart,
  currency,
  onSelect,
}: {
  chart: ChartData;
  currency: CurrencyProfile;
  onSelect?: (point: ChartPoint, series: ChartSeries | null) => void;
}) {
  const rows = useMemo(() => chart.points, [chart.points]);
  return (
    <div className="table-frame">
      <table className="table">
        <caption className="visually-hidden">{chart.title}</caption>
        <thead>
          <tr>
            <th scope="col">{chart.interval ? "Period" : "Item"}</th>
            {chart.series.map((series) => (
              <th key={series.key} scope="col" className="numeric">
                {series.label}
                {series.basis !== "actual" ? (
                  <div className="cell-note">{series.basis_label}</div>
                ) : null}
              </th>
            ))}
            {onSelect ? <th /> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((point) => (
            <tr key={point.key}>
              <th scope="row">{point.label}</th>
              {chart.series.map((series) => (
                <td key={series.key} className="numeric">
                  {money(point.values[series.key] ?? "0", currency)}
                </td>
              ))}
              {onSelect ? (
                <td>
                  <Button size="compact" tone="plain" onClick={() => onSelect(point, null)}>
                    Records
                  </Button>
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
