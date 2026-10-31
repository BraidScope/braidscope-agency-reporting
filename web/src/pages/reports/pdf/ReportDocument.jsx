// SPDX-License-Identifier: AGPL-3.0-only
import { Fragment } from "react";
import { Document, Image, Page, Path, Rect, StyleSheet, Svg, Text, View } from "@react-pdf/renderer";
import {
  categoryColumns, categoryRows, chartBars, chartLine, chartRows, chartSectionParts, chartTypeFor, formatDay, formatMetric, formatPeriod, formatTrendTotal,
  isChartSection, isCommentary, sectionLabel, seriesOf, seriesSummary, SOURCE_BY_ID,
} from "../reportModel";

// PDF renderer for a report definition. Mirrors ReportPreview.jsx rule for
// rule: same section order, same "omit unwritten narrative" behaviour, same
// per-source KPI/chart/table selection and the same chartBars() geometry.

const INK = "#14141a";
const BODY = "#3a3a42";
const MUTED = "#767680";
const LINE = "#e6e6ec";
const WASH = "#f5f5f8";
const ZEBRA = "#fafafc";
const PAGE_W = 595;
const MARGIN = 42;
const CONTENT_W = PAGE_W - MARGIN * 2;
const CHART_H = 84;

// The built-in PDF fonts cover Latin-1 + common typographic punctuation.
// Anything else (emoji, CJK, arrows) would render as garbage, so it is
// replaced rather than emitted.
const PDF_EXTRA = new Set([0x2013, 0x2014, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2026, 0x20ac, 0x2122]);

export function pdfSafe(value) {
  return Array.from(String(value ?? "").replace(/→/g, "->"))
    .map((char) => {
      const code = char.codePointAt(0);
      if (code === 0x0a || (code >= 0x20 && code <= 0x7e) || (code >= 0xa0 && code <= 0xff) || PDF_EXTRA.has(code)) return char;
      if (code === 0x2028 || code === 0x2029) return " ";
      return "?";
    })
    .join("");
}

const isHex = (value) => /^#[0-9a-fA-F]{6}$/.test(value || "");

// Mixes a hex colour with white: tint("#0f766e", 0.9) is a very light wash.
function tint(hex, amount) {
  const channel = (offset) => parseInt(hex.slice(offset, offset + 2), 16);
  const mix = (value) => Math.round(value + (255 - value) * amount).toString(16).padStart(2, "0");
  return `#${mix(channel(1))}${mix(channel(3))}${mix(channel(5))}`;
}

const s = StyleSheet.create({
  page: { paddingTop: 44, paddingBottom: 58, paddingHorizontal: MARGIN, fontFamily: "Helvetica", fontSize: 10, color: BODY },
  topBar: { position: "absolute", top: 0, left: 0, right: 0, height: 7 },
  cover: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 22 },
  agency: { flexDirection: "row", alignItems: "center", flexShrink: 1 },
  mark: { width: 34, height: 34, borderRadius: 9, alignItems: "center", justifyContent: "center", marginRight: 10 },
  markText: { color: "#ffffff", fontFamily: "Helvetica-Bold", fontSize: 16 },
  logo: { width: 34, height: 34, objectFit: "contain", marginRight: 10 },
  agencyName: { fontFamily: "Helvetica-Bold", fontSize: 13, color: INK, lineHeight: 1.25 },
  eyebrow: { fontFamily: "Helvetica-Bold", fontSize: 7.5, letterSpacing: 1, color: MUTED, marginBottom: 2 },
  right: { alignItems: "flex-end", maxWidth: 220 },
  clientName: { fontFamily: "Helvetica-Bold", fontSize: 13, color: INK, lineHeight: 1.25, textAlign: "right" },
  muted: { color: MUTED, fontSize: 8.5, lineHeight: 1.4 },
  title: { fontFamily: "Helvetica-Bold", fontSize: 24, color: INK, lineHeight: 1.2, marginBottom: 6 },
  sub: { color: MUTED, fontSize: 10, marginBottom: 12 },
  rule: { width: 44, height: 3, borderRadius: 2, marginBottom: 22 },
  block: { marginBottom: 18 },
  gap: { height: 6 },
  h3: { fontFamily: "Helvetica-Bold", fontSize: 12.5, color: INK, marginBottom: 9, paddingLeft: 8, borderLeftWidth: 3, lineHeight: 1.25 },
  text: { fontSize: 10, lineHeight: 1.65 },
  callout: { borderRadius: 8, padding: 13, borderLeftWidth: 3 },
  bullet: { flexDirection: "row", marginBottom: 5 },
  bulletDot: { width: 4, height: 4, borderRadius: 2, marginTop: 6, marginRight: 8 },
  group: { marginBottom: 12 },
  tagRow: { flexDirection: "row", alignItems: "center", marginBottom: 6 },
  tagDot: { width: 6, height: 6, borderRadius: 3, marginRight: 5 },
  tag: { fontFamily: "Helvetica-Bold", fontSize: 9.5, color: INK, lineHeight: 1.3 },
  kpis: { flexDirection: "row", flexWrap: "wrap" },
  kpi: { width: "31.6%", marginRight: "1.4%", marginBottom: 8, backgroundColor: WASH, borderRadius: 7, paddingVertical: 9, paddingHorizontal: 11, borderLeftWidth: 3 },
  kpiL: { color: MUTED, fontSize: 8, lineHeight: 1.3 },
  kpiV: { fontFamily: "Helvetica-Bold", fontSize: 16, color: INK, marginTop: 2, lineHeight: 1.2 },
  chartHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 6 },
  chartBox: { borderWidth: 1, borderColor: LINE, borderRadius: 8, paddingTop: 10, paddingBottom: 6, paddingHorizontal: 10 },
  catLabel: { fontSize: 7, color: MUTED, textAlign: "center", lineHeight: 1.3 },
  catValue: { fontSize: 7.5, fontFamily: "Helvetica-Bold", color: INK, textAlign: "center", lineHeight: 1.3 },
  hRow: { flexDirection: "row", alignItems: "center", marginBottom: 4 },
  hLabelWide: { width: 124, fontSize: 8.5, color: INK, lineHeight: 1.3 },
  hLabel: { width: 44, fontSize: 8, color: MUTED, lineHeight: 1.3 },
  hTrack: { flex: 1, height: 8, backgroundColor: WASH, borderRadius: 4, marginHorizontal: 6 },
  hFill: { height: 8, borderRadius: 4 },
  hValue: { width: 64, textAlign: "right", fontFamily: "Helvetica-Bold", fontSize: 8.5, color: INK, lineHeight: 1.3 },
  axis: { flexDirection: "row", justifyContent: "space-between", marginTop: 4 },
  tableWrap: { borderWidth: 1, borderColor: LINE, borderRadius: 8, overflow: "hidden" },
  tableHead: { flexDirection: "row", backgroundColor: WASH, paddingVertical: 5, paddingHorizontal: 9 },
  tableRow: { flexDirection: "row", paddingVertical: 5, paddingHorizontal: 9, borderTopWidth: 1, borderTopColor: LINE },
  th: { fontFamily: "Helvetica-Bold", fontSize: 7.5, color: MUTED, letterSpacing: 0.6, lineHeight: 1.3 },
  td: { fontSize: 9, color: INK, lineHeight: 1.35 },
  note: { color: MUTED, fontSize: 9, marginBottom: 6, lineHeight: 1.4 },
  footerRule: { position: "absolute", left: MARGIN, right: MARGIN, bottom: 40, height: 1, backgroundColor: LINE },
  footerLeft: { position: "absolute", left: MARGIN, bottom: 24, width: CONTENT_W - 110, fontSize: 8, color: MUTED, lineHeight: 1.3 },
  footerRight: { position: "absolute", right: MARGIN, bottom: 24, width: 100 },
});

function Tag({ id }) {
  const source = SOURCE_BY_ID[id];
  return (
    <View style={s.tagRow}>
      <View style={{ ...s.tagDot, backgroundColor: source?.color || MUTED }} />
      <Text style={s.tag}>{pdfSafe(source?.label || id)}</Text>
    </View>
  );
}

function Heading({ children, accent }) {
  // Rendered INSIDE the first group of its section (which is wrap={false}), so
  // a heading can never be stranded at the bottom of a page without content.
  return <Text style={{ ...s.h3, borderLeftColor: accent }}>{children}</Text>;
}

function Narrative({ text, accent, bullets = false, callout = false }) {
  const lines = String(text).split("\n").map((line) => line.trim()).filter(Boolean);
  const isList = bullets && lines.some((line) => /^[-•*]\s+/.test(line));
  const body = isList
    ? lines.map((line, index) => (
      <View key={index} style={s.bullet} wrap={false}>
        <View style={{ ...s.bulletDot, backgroundColor: accent }} />
        <Text style={{ ...s.text, flex: 1 }}>{pdfSafe(line.replace(/^[-•*]\s+/, ""))}</Text>
      </View>
    ))
    : <Text style={s.text}>{pdfSafe(text)}</Text>;
  return callout ? <View style={{ ...s.callout, backgroundColor: tint(accent, 0.93), borderLeftColor: accent }}>{body}</View> : body;
}

// `meta` is a plain object the caller reads after rendering: the footer's
// `render` callback receives the true total page count during layout.
export default function ReportDocument({ branding, clientName, name, config, narratives = {}, snapshot, period, logoDataUrl, generatedAt, currency = "usd", meta = {} }) {
  const accent = isHex(branding?.accent) ? branding.accent : "#0f766e";
  const sources = config?.sources || [];
  const sections = (config?.sections || []).filter((section) => section.enabled);
  const dataFor = (id) => snapshot?.sources?.[id] || null;
  const usable = (id) => dataFor(id)?.ok && !dataFor(id)?.empty;
  const agency = pdfSafe(branding?.name || "Your agency");

  const renderSection = (section, index) => {
    const { id } = section;
    if (id === "summary" || id === "recommendations" || isCommentary(id)) {
      const text = narratives[id];
      if (!text?.trim()) return null;
      if (isCommentary(id)) {
        return (
          <View key={id} style={s.block} wrap={false}>
            <Tag id={id.split(":")[1]} />
            <Narrative text={text} accent={accent} />
          </View>
        );
      }
      return (
        <View key={id} style={s.block}>
          <Heading accent={accent}>{sectionLabel(id)}</Heading>
          <Narrative text={text} accent={accent} bullets={id === "recommendations"} callout={id === "summary"} />
        </View>
      );
    }
    if (id === "kpis") {
      return (
        <Fragment key={id}>
          {!sources.some(usable) && <Heading accent={accent}>Key metrics</Heading>}
          {sources.map((sourceId) => (usable(sourceId) ? (
            <View key={sourceId} style={s.group} wrap={false}>
              {sourceId === sources.find(usable) && <Heading accent={accent}>Key metrics</Heading>}
              <Tag id={sourceId} />
              {dataFor(sourceId).windowNote ? <Text style={s.note}>{pdfSafe(dataFor(sourceId).windowNote)}</Text> : null}
              <View style={s.kpis}>
                {dataFor(sourceId).cards.slice(0, 6).map((card) => (
                  <View key={card.label} style={{ ...s.kpi, borderLeftColor: accent }}>
                    <Text style={s.kpiL}>{pdfSafe(card.label)}</Text>
                    <Text style={s.kpiV}>{pdfSafe(card.value)}</Text>
                  </View>
                ))}
              </View>
            </View>
          ) : (
            <Text key={sourceId} style={s.note}>
              {pdfSafe(SOURCE_BY_ID[sourceId]?.label || sourceId)}: {dataFor(sourceId)?.ok === false ? "data was not available when this report was generated." : "no data available for this period."}
            </Text>
          )))}
          <View style={s.gap} />
        </Fragment>
      );
    }
    if (isChartSection(id)) {
      if (id === "chart") return null; // legacy single chart section: expanded per metric once data is loaded
      const { sourceId, key } = chartSectionParts(id);
      const series = usable(sourceId) ? seriesOf(dataFor(sourceId)).find((item) => item.key === key) : null;
      if (!series) return null;
      const type = chartTypeFor(config, id, series);
      const previous = sections[index - 1];
      const first = !previous || !isChartSection(previous.id);
      const innerW = CONTENT_W - 22;
      let body;
      const category = series.kind === "category";
      if (category && type !== "hbar") {
        // A breakdown drawn as columns / line / area: labels and values under each point.
        const geometry = categoryColumns(series.points, { width: innerW, height: CHART_H });
        const maxChars = Math.max(4, Math.floor(geometry.slot / 4.4));
        const clip = (text) => (text.length > maxChars ? `${text.slice(0, maxChars - 1)}…` : text);
        body = (
          <>
            <Svg width={innerW} height={CHART_H} viewBox={`0 0 ${innerW} ${CHART_H}`}>
              {[0, 0.5, 1].map((level) => <Rect key={level} x={0} y={Math.min(CHART_H - 0.5, level * CHART_H)} width={innerW} height={0.5} fill={LINE} />)}
              {type === "bar" && geometry.bars.map((bar, barIndex) => <Rect key={`${bar.label}-${barIndex}`} x={bar.x} y={bar.y} width={bar.width} height={bar.height} rx={1.5} fill={accent} />)}
              {type === "area" && <Path d={geometry.area} fill={tint(accent, 0.8)} />}
              {(type === "line" || type === "area") && <Path d={geometry.line} stroke={accent} strokeWidth={1.6} fill="none" />}
            </Svg>
            <View style={{ flexDirection: "row", marginTop: 4 }}>
              {series.points.map((point, pointIndex) => (
                <View key={`${point.label}-${pointIndex}`} style={{ width: geometry.slot }}>
                  <Text style={s.catLabel}>{pdfSafe(clip(point.label))}</Text>
                  <Text style={s.catValue}>{pdfSafe(formatMetric(series, point.value, currency))}</Text>
                </View>
              ))}
            </View>
          </>
        );
      } else if (type === "hbar") {
        const { rows } = category ? categoryRows(series.points) : chartRows(series.points, { maxRows: 10 });
        body = rows.map((row) => (
          <View key={row.label} style={s.hRow}>
            <Text style={category ? s.hLabelWide : s.hLabel}>{category ? pdfSafe(row.label.length > 26 ? `${row.label.slice(0, 25)}\u2026` : row.label) : formatDay(row.label)}</Text>
            <View style={s.hTrack}><View style={{ ...s.hFill, width: `${Math.max(row.value > 0 ? 2 : 0, row.pct * 100)}%`, backgroundColor: accent }} /></View>
            <Text style={s.hValue}>{pdfSafe(formatMetric(series, row.value, currency))}</Text>
          </View>
        ));
      } else if (type === "line" || type === "area") {
        const geometry = chartLine(series.points, { width: innerW, height: CHART_H });
        body = (
          <>
            <Svg width={innerW} height={CHART_H} viewBox={`0 0 ${innerW} ${CHART_H}`}>
              {[0, 0.5, 1].map((level) => <Rect key={level} x={0} y={Math.min(CHART_H - 0.5, level * CHART_H)} width={innerW} height={0.5} fill={LINE} />)}
              {type === "area" && <Path d={geometry.area} fill={tint(accent, 0.8)} />}
              <Path d={geometry.line} stroke={accent} strokeWidth={1.6} fill="none" />
            </Svg>
            <View style={s.axis}>
              <Text style={s.muted}>{formatDay(geometry.first)}</Text>
              <Text style={s.muted}>{formatDay(geometry.last)}</Text>
            </View>
          </>
        );
      } else {
        const geometry = chartBars(series.points, { width: innerW, height: CHART_H });
        body = (
          <>
            <Svg width={innerW} height={CHART_H} viewBox={`0 0 ${innerW} ${CHART_H}`}>
              {[0, 0.5, 1].map((level) => <Rect key={level} x={0} y={Math.min(CHART_H - 0.5, level * CHART_H)} width={innerW} height={0.5} fill={LINE} />)}
              {geometry.bars.map((bar) => <Rect key={bar.label} x={bar.x} y={bar.y} width={bar.width} height={bar.height} rx={1.5} fill={accent} />)}
            </Svg>
            <View style={s.axis}>
              <Text style={s.muted}>{formatDay(geometry.first)}</Text>
              <Text style={s.muted}>{formatDay(geometry.last)}</Text>
            </View>
          </>
        );
      }
      return (
        <View key={id} style={s.group} wrap={false}>
          {first && <Heading accent={accent}>Performance</Heading>}
          <View style={s.chartHead}>
            <Tag id={sourceId} />
            <Text style={s.muted}>{pdfSafe(series.label)}  <Text style={{ fontFamily: "Helvetica-Bold", color: INK }}>{pdfSafe(seriesSummary(series, currency))}</Text></Text>
          </View>
          {dataFor(sourceId).windowNote ? <Text style={s.note}>{pdfSafe(dataFor(sourceId).windowNote)}</Text> : null}
          <View style={s.chartBox}>{body}</View>
        </View>
      );
    }
    if (id === "tables") {
      const tabled = sources.filter((sourceId) => usable(sourceId) && dataFor(sourceId).table);
      if (!tabled.length) return null;
      return (
        <Fragment key={id}>
          {tabled.map((sourceId, index) => {
            const { table } = dataFor(sourceId);
            const widths = table.columns.map((_, index) => (index === 0 ? 2.4 : 1));
            return (
              <View key={sourceId} style={s.group} wrap={false}>
                {index === 0 && <Heading accent={accent}>Top performers</Heading>}
                <Tag id={sourceId} />
                <View style={s.tableWrap}>
                  <View style={s.tableHead}>
                    {table.columns.map((column, index) => (
                      <Text key={column.key} style={{ ...s.th, flex: widths[index], textAlign: index ? "right" : "left" }}>{pdfSafe(column.label).toUpperCase()}</Text>
                    ))}
                  </View>
                  {table.rows.map((row, rowIndex) => (
                    <View key={rowIndex} style={{ ...s.tableRow, backgroundColor: rowIndex % 2 ? ZEBRA : "#ffffff" }}>
                      {table.columns.map((column, index) => (
                        <Text key={column.key} style={{ ...s.td, flex: widths[index], textAlign: index ? "right" : "left" }}>{pdfSafe(row[column.key])}</Text>
                      ))}
                    </View>
                  ))}
                </View>
              </View>
            );
          })}
          <View style={s.gap} />
        </Fragment>
      );
    }
    return null;
  };

  return (
    <Document title={pdfSafe(name)} author={agency} subject={`${pdfSafe(clientName)} report`}>
      <Page size="A4" style={s.page}>
        <View fixed style={{ ...s.topBar, backgroundColor: accent }} />

        <View style={s.cover}>
          <View style={s.agency}>
            {logoDataUrl
              ? <Image src={logoDataUrl} style={s.logo} />
              : <View style={{ ...s.mark, backgroundColor: accent }}><Text style={s.markText}>{agency.slice(0, 1).toUpperCase()}</Text></View>}
            <View>
              <Text style={s.agencyName}>{agency}</Text>
              <Text style={s.muted}>Performance report</Text>
            </View>
          </View>
          <View style={s.right}>
            <Text style={s.eyebrow}>PREPARED FOR</Text>
            <Text style={s.clientName}>{pdfSafe(clientName)}</Text>
            <Text style={s.muted}>{pdfSafe(formatPeriod(period?.start, period?.end))}</Text>
          </View>
        </View>

        <Text style={s.title}>{pdfSafe(name)}</Text>
        <Text style={s.sub}>{pdfSafe(sources.map((id) => SOURCE_BY_ID[id]?.label || id).join("  ·  "))}</Text>
        <View style={{ ...s.rule, backgroundColor: accent }} />

        {sections.map(renderSection)}

        <View fixed style={s.footerRule} />
        <Text fixed style={s.footerLeft}>{agency} - confidential{generatedAt ? `   |   Data as of ${formatDay(generatedAt, { month: "short", day: "numeric", year: "numeric" })}` : ""}</Text>
        <View fixed style={s.footerRight}>
          {/* no lineHeight here: react-pdf drops dynamic render text when it is set */}
          <Text style={{ textAlign: "right", fontSize: 8, color: MUTED }} render={({ pageNumber, totalPages }) => { meta.pages = totalPages; return `Page ${pageNumber} of ${totalPages}`; }} />
        </View>
      </Page>
    </Document>
  );
}
