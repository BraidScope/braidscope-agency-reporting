// SPDX-License-Identifier: AGPL-3.0-only
import {
  categoryColumns, categoryRows, chartBars, chartLine, chartRows, chartSectionParts, chartTypeFor, formatDay, formatMetric, formatPeriod, formatTrendTotal,
  isChartSection, isCommentary, sectionLabel, seriesOf, seriesSummary, SOURCE_BY_ID,
} from "./reportModel";

// The on-screen report. Renders ONLY from the report definition (config +
// narratives + saved data snapshot) - the PDF renderer (pdf/ReportDocument.jsx)
// renders the same definition with the same rules, section order, chart types
// and chart geometry, so what you review here is what gets exported.
//
// All text (narratives, campaign names, product names) is rendered as plain
// React text, never as HTML.

const CHART_W = 520;
const CHART_H = 88;

function SourceTag({ id }) {
  const source = SOURCE_BY_ID[id];
  return (
    <span className="rp-source-tag">
      <i className={`ti ${source?.icon || "ti-plug"}`} aria-hidden="true" style={{ color: source?.color }} /> {source?.label || id}
    </span>
  );
}

function SourceNote({ data }) {
  if (!data?.windowNote) return null;
  return <p className="rp-source-note"><i className="ti ti-info-circle" aria-hidden="true" /> {data.windowNote}</p>;
}

function SourceProblem({ id, data, mode, onRetry }) {
  const label = SOURCE_BY_ID[id]?.label || id;
  const failed = data && data.ok === false;
  return (
    <div className="rp-note" role="status">
      <SourceTag id={id} />
      <span>
        {failed ? `${label} could not be loaded.` : data?.empty ? "No data available for this period." : "No data loaded yet."}
      </span>
      {failed && mode === "edit" && onRetry && (
        <button type="button" className="rp-link" onClick={() => onRetry(id)}>Retry</button>
      )}
    </div>
  );
}

// One metric over time, drawn as the saved chart type. Only the drawing
// differs between types - the data and the scale are the same.
function SeriesChart({ series, type, currency }) {
  // A breakdown (by campaign, platform, device, stage, status...) has one point
  // per label. Horizontal bars are its default; the same points can also be
  // drawn as columns, a line or an area with the labels underneath.
  if (series.kind === "category" && type === "hbar") {
    const { rows } = categoryRows(series.points);
    return (
      <div className="rp-hbars rp-hbars--cat" role="img" aria-label={`${series.label}: ${rows.map((row) => `${row.label} ${formatMetric(series, row.value, currency)}`).join(", ")}`}>
        {rows.map((row) => (
          <div key={row.label} className="rp-hbar-row">
            <span className="rp-hbar-label" title={row.label}>{row.label}</span>
            <span className="rp-hbar-track"><span className="rp-hbar-fill" style={{ width: `${Math.max(row.value > 0 ? 2 : 0, row.pct * 100)}%` }} /></span>
            <span className="rp-hbar-value">{formatMetric(series, row.value, currency)}</span>
          </div>
        ))}
      </div>
    );
  }
  if (series.kind === "category") {
    const geometry = categoryColumns(series.points, { width: CHART_W, height: CHART_H });
    const label = `${series.label}: ${series.points.map((point) => `${point.label} ${formatMetric(series, point.value, currency)}`).join(", ")}`;
    return (
      <figure className="rp-chart rp-chart--cat">
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} role="img" aria-label={label} preserveAspectRatio="none">
          {[0.25, 0.5, 0.75].map((level) => <line key={level} x1="0" x2={CHART_W} y1={CHART_H * level} y2={CHART_H * level} className="rp-grid-line" vectorEffect="non-scaling-stroke" />)}
          {type === "bar" && geometry.bars.map((bar, index) => <rect key={`${bar.label}-${index}`} x={bar.x} y={bar.y} width={bar.width} height={bar.height} rx="1.5" className="rp-bar" />)}
          {type === "area" && <path d={geometry.area} className="rp-area" />}
          {(type === "line" || type === "area") && <path d={geometry.line} className="rp-line" vectorEffect="non-scaling-stroke" />}
        </svg>
        <div className="rp-cat-labels" style={{ gridTemplateColumns: `repeat(${series.points.length}, minmax(0, 1fr))` }}>
          {series.points.map((point, index) => (
            <div key={`${point.label}-${index}`} className="rp-cat-label" title={`${point.label}: ${formatMetric(series, point.value, currency)}`}>
              <span>{point.label}</span>
              <b>{formatMetric(series, point.value, currency)}</b>
            </div>
          ))}
        </div>
      </figure>
    );
  }
  const summary = `${series.label}, total ${formatTrendTotal(series, currency)}`;
  if (type === "hbar") {
    const { rows } = chartRows(series.points, { maxRows: 10 });
    return (
      <div className="rp-hbars" role="img" aria-label={`${summary}, by period`}>
        {rows.map((row) => (
          <div key={row.label} className="rp-hbar-row">
            <span className="rp-hbar-label">{formatDay(row.label)}</span>
            <span className="rp-hbar-track"><span className="rp-hbar-fill" style={{ width: `${Math.max(row.value > 0 ? 2 : 0, row.pct * 100)}%` }} /></span>
            <span className="rp-hbar-value">{formatMetric(series, row.value, currency)}</span>
          </div>
        ))}
      </div>
    );
  }
  if (type === "line" || type === "area") {
    const geometry = chartLine(series.points, { width: CHART_W, height: CHART_H });
    return (
      <figure className="rp-chart">
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} role="img" aria-label={summary} preserveAspectRatio="none">
          {[0.25, 0.5, 0.75].map((level) => <line key={level} x1="0" x2={CHART_W} y1={CHART_H * level} y2={CHART_H * level} className="rp-grid-line" vectorEffect="non-scaling-stroke" />)}
          {type === "area" && <path d={geometry.area} className="rp-area" />}
          <path d={geometry.line} className="rp-line" vectorEffect="non-scaling-stroke" />
        </svg>
        <figcaption><span>{formatDay(geometry.first)}</span><span>{formatDay(geometry.last)}</span></figcaption>
      </figure>
    );
  }
  const geometry = chartBars(series.points, { width: CHART_W, height: CHART_H });
  return (
    <figure className="rp-chart">
      <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} role="img" aria-label={summary} preserveAspectRatio="none">
        {geometry.bars.map((bar) => (
          <rect key={bar.label} x={bar.x} y={bar.y} width={bar.width} height={bar.height} rx="1.5" className="rp-bar" />
        ))}
      </svg>
      <figcaption><span>{formatDay(geometry.first)}</span><span>{formatDay(geometry.last)}</span></figcaption>
    </figure>
  );
}

function Narrative({ id, text, mode }) {
  if (!text?.trim()) {
    if (mode !== "edit") return null;
    return <p className="rp-empty">Not written yet. This section stays out of the PDF until it has text.</p>;
  }
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  // Same rule as the PDF: recommendations written as "- item" lines become a list.
  if (id === "recommendations" && lines.some((line) => /^[-*]\s+/.test(line))) {
    return <ul className="rp-list-plain">{lines.map((line, index) => <li key={index}>{line.replace(/^[-*]\s+/, "")}</li>)}</ul>;
  }
  return <p className="rp-text">{text}</p>;
}

export default function ReportPreview({ branding, clientName, name, config, narratives = {}, snapshot, period, mode = "edit", loading = false, currency = "usd", onRetrySource, generatedAt, wide = false }) {
  const sources = config?.sources || [];
  const sections = (config?.sections || []).filter((section) => section.enabled);
  const dataFor = (id) => snapshot?.sources?.[id] || null;
  const usable = (id) => dataFor(id)?.ok && !dataFor(id)?.empty;
  const sourceLabels = sources.map((id) => SOURCE_BY_ID[id]?.label || id).join(" · ");
  const accent = branding?.accent;

  const renderSection = (section, index) => {
    const { id } = section;
    if (id === "summary" || id === "recommendations") {
      const text = narratives[id];
      if (!text?.trim() && mode !== "edit") return null;
      return (
        <section key={id} className={`rp-block${id === "summary" ? " rp-block--callout" : ""}`} aria-label={sectionLabel(id)}>
          <h3 className="rp-h3">{sectionLabel(id)}</h3>
          <Narrative id={id} text={text} mode={mode} />
        </section>
      );
    }
    if (isCommentary(id)) {
      const sourceId = id.split(":")[1];
      const text = narratives[id];
      if (!text?.trim() && mode !== "edit") return null;
      return (
        <section key={id} className="rp-block" aria-label={sectionLabel(id)}>
          <h3 className="rp-h3"><SourceTag id={sourceId} /></h3>
          <Narrative id={id} text={text} mode={mode} />
        </section>
      );
    }
    if (id === "kpis") {
      return (
        <section key={id} className="rp-block" aria-label="KPI summary">
          <h3 className="rp-h3">Key metrics</h3>
          {sources.map((sourceId) => (usable(sourceId) ? (
            <div key={sourceId} className="rp-group">
              <SourceTag id={sourceId} />
              <SourceNote data={dataFor(sourceId)} />
              <div className="rp-kpis">
                {dataFor(sourceId).cards.slice(0, 6).map((card) => (
                  <div key={card.label} className="rp-kpi">
                    <div className="l">{card.label}</div>
                    <div className="v">{card.value}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : (loading ? null : <SourceProblem key={sourceId} id={sourceId} data={dataFor(sourceId)} mode={mode} onRetry={onRetrySource} />)))}
        </section>
      );
    }
    if (isChartSection(id)) {
      if (id === "chart") return null; // legacy single chart section: expanded per metric once data is loaded
      const { sourceId, key } = chartSectionParts(id);
      const series = usable(sourceId) ? seriesOf(dataFor(sourceId)).find((item) => item.key === key) : null;
      if (!series) return null;
      const previous = sections[index - 1];
      const first = !previous || !isChartSection(previous.id);
      return (
        <section key={id} className="rp-block rp-block--chart" aria-label={sectionLabel(id)}>
          {first && <h3 className="rp-h3">Performance</h3>}
          <div className="rp-group">
            <div className="rp-chart-head">
              <SourceTag id={sourceId} />
              <span className="rp-chart-title">{series.label} <b>{seriesSummary(series, currency)}</b></span>
            </div>
            <SourceNote data={dataFor(sourceId)} />
            <SeriesChart series={series} type={chartTypeFor(config, id, series)} currency={currency} />
          </div>
        </section>
      );
    }
    if (id === "tables") {
      const tabled = sources.filter((sourceId) => usable(sourceId) && dataFor(sourceId).table);
      if (!tabled.length && (loading || !snapshot)) return null;
      return (
        <section key={id} className="rp-block" aria-label="Top campaigns and products">
          <h3 className="rp-h3">Top performers</h3>
          {tabled.length === 0 && <div className="rp-note" role="status">No campaign or product rows are available for the selected sources and period.</div>}
          {tabled.map((sourceId) => {
            const { table } = dataFor(sourceId);
            return (
              <div key={sourceId} className="rp-group">
                <SourceTag id={sourceId} />
                <table className="rp-table">
                  <thead>
                    <tr>{table.columns.map((column, columnIndex) => <th key={column.key} scope="col" className={columnIndex ? "num" : undefined}>{column.label}</th>)}</tr>
                  </thead>
                  <tbody>
                    {table.rows.map((row, rowIndex) => (
                      <tr key={rowIndex}>{table.columns.map((column, columnIndex) => <td key={column.key} className={columnIndex ? "num" : undefined}>{row[column.key]}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
        </section>
      );
    }
    return null;
  };

  return (
    <article className={`rp-paper${wide ? " wide" : ""}`} aria-label={`Report preview: ${name || "Untitled report"}`} style={accent ? { "--rp-accent": accent } : undefined}>
      <header className="rp-cover">
        <div className="rp-agency">
          <div className="rp-mark" style={branding?.logo ? { background: "transparent" } : undefined}>
            {branding?.logo ? <img src={branding.logo} alt="" /> : (branding?.name || "A").trim().slice(0, 1).toUpperCase()}
          </div>
          <div>
            <div className="rp-agency-name">{branding?.name || "Your agency"}</div>
            <div className="rp-muted">Prepared for</div>
          </div>
        </div>
        <div className="rp-client">
          <div className="rp-client-name">{clientName}</div>
          <div className="rp-muted">{formatPeriod(period?.start, period?.end)}</div>
        </div>
      </header>

      <div className="rp-body">
        <h2 className="rp-title">{name || "Untitled report"}</h2>
        <div className="rp-sub">{sourceLabels || "No data sources selected"}</div>

        {loading && (
          <div className="rp-loading" role="status" aria-live="polite">
            <span className="rp-spinner" aria-hidden="true" /> Loading data…
          </div>
        )}
        {!loading && !snapshot && (
          <div className="rp-note" role="status">
            {mode === "edit"
              ? "This report has no data yet. Use “Load data” to pull the latest numbers from your connected sources."
              : "This report has no data yet. Choose Edit to load it."}
          </div>
        )}
        {sections.length === 0 && <div className="rp-note">All sections are turned off. Turn one on in the section list.</div>}
        {sections.map(renderSection)}
      </div>

      <footer className="rp-foot">
        <span>{branding?.name || "Your agency"} — confidential</span>
        <span>{generatedAt ? `Data as of ${formatDay(generatedAt, { month: "short", day: "numeric", year: "numeric" })}` : ""}</span>
      </footer>
    </article>
  );
}
