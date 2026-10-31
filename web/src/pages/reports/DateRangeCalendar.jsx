// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useMemo, useState } from "react";
import { Modal } from "./ReportControls";
import { daysBetween, EARLIEST_CUSTOM_DATE, formatPeriod, todayIso, validateCustomRange } from "./reportModel";

const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
const iso = (date) => date.toISOString().slice(0, 10);
const parse = (value) => new Date(`${value}T00:00:00Z`);
const addDays = (value, amount) => iso(new Date(parse(value).getTime() + amount * 86400000));
const monthStart = (value) => `${value.slice(0, 7)}-01`;
const addMonths = (value, amount) => {
  const date = parse(monthStart(value));
  date.setUTCMonth(date.getUTCMonth() + amount);
  return iso(date);
};

function monthCells(monthFirst) {
  const first = parse(monthFirst);
  const lead = first.getUTCDay();
  const count = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  const cells = Array(lead).fill(null);
  for (let day = 1; day <= count; day += 1) cells.push(iso(new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), day))));
  return cells;
}

const longDate = (value) => new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: "UTC" }).format(parse(value));

// A month grid for picking an inclusive date range: click a start day, then an
// end day (clicking an earlier day restarts the range). Days after `max` or
// before `min` are disabled. Arrow keys move between days, PageUp/PageDown
// change month.
export function DateRangeCalendar({ start, end, onChange, min = EARLIEST_CUSTOM_DATE, max = todayIso() }) {
  const [view, setView] = useState(() => monthStart(end || start || max));
  const [focusDay, setFocusDay] = useState(() => end || start || max);
  const [hover, setHover] = useState("");
  const cells = useMemo(() => monthCells(view), [view]);
  const monthLabel = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" }).format(parse(view));
  const awaitingEnd = Boolean(start && !end);
  const previewEnd = awaitingEnd && hover && hover >= start ? hover : end;

  useEffect(() => {
    if (focusDay.slice(0, 7) !== view.slice(0, 7)) setView(monthStart(focusDay));
  }, [focusDay]); // eslint-disable-line react-hooks/exhaustive-deps

  const disabled = (day) => day > max || day < min;
  const pick = (day) => {
    if (disabled(day)) return;
    if (!start || end || day < start) onChange({ start: day, end: "" });
    else onChange({ start, end: day });
  };
  const onKey = (event, day) => {
    const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    if (moves[event.key]) {
      event.preventDefault();
      const next = addDays(day, moves[event.key]);
      if (!disabled(next)) { setFocusDay(next); requestAnimationFrame(() => document.querySelector(`[data-day="${next}"]`)?.focus()); }
    } else if (event.key === "PageUp" || event.key === "PageDown") {
      event.preventDefault();
      const next = addMonths(day, event.key === "PageUp" ? -1 : 1);
      const clamped = next > max ? max : next < min ? min : next;
      setFocusDay(clamped);
      requestAnimationFrame(() => document.querySelector(`[data-day="${clamped}"]`)?.focus());
    }
  };

  const canGoBack = addMonths(view, -1) >= monthStart(min);
  const canGoForward = addMonths(view, 1) <= monthStart(max);

  return (
    <div className="rp-cal" role="group" aria-label="Choose a date range">
      <div className="rp-cal-head">
        <button type="button" className="rp-icon-btn sm" onClick={() => setView(addMonths(view, -1))} disabled={!canGoBack} aria-label="Previous month"><i className="ti ti-chevron-left" aria-hidden="true" /></button>
        <div className="rp-cal-title" aria-live="polite">{monthLabel}</div>
        <button type="button" className="rp-icon-btn sm" onClick={() => setView(addMonths(view, 1))} disabled={!canGoForward} aria-label="Next month"><i className="ti ti-chevron-right" aria-hidden="true" /></button>
      </div>
      <div className="rp-cal-week" aria-hidden="true">{WEEKDAYS.map((day) => <span key={day}>{day}</span>)}</div>
      <div className="rp-cal-grid" role="grid" aria-label={monthLabel} onMouseLeave={() => setHover("")}>
        {cells.map((day, index) => {
          if (!day) return <span key={`blank-${index}`} className="rp-cal-blank" />;
          const isStart = day === start;
          const isEnd = day === end;
          const inRange = start && previewEnd && day > start && day < previewEnd;
          const roving = day === focusDay || (!cells.includes(focusDay) && day === cells.find(Boolean));
          return (
            <button
              key={day}
              type="button"
              role="gridcell"
              data-day={day}
              tabIndex={roving ? 0 : -1}
              disabled={disabled(day)}
              aria-label={longDate(day)}
              aria-selected={isStart || isEnd}
              className={`rp-cal-day${isStart ? " start" : ""}${isEnd ? " end" : ""}${inRange ? " between" : ""}${day === todayIso() ? " today" : ""}${awaitingEnd && day === hover ? " hover" : ""}`}
              onClick={() => { setFocusDay(day); pick(day); }}
              onMouseEnter={() => setHover(day)}
              onFocus={() => setHover("")}
              onKeyDown={(event) => onKey(event, day)}
            >
              {Number(day.slice(8))}
            </button>
          );
        })}
      </div>
    </div>
  );
}

const shift = (days, from = todayIso()) => addDays(from, days);
function quickRanges() {
  const today = todayIso();
  const thisMonthStart = monthStart(today);
  const lastMonthEnd = addDays(thisMonthStart, -1);
  return [
    { label: "This month so far", start: thisMonthStart, end: today },
    { label: "Last month", start: monthStart(lastMonthEnd), end: lastMonthEnd },
    { label: "Last 60 days", start: shift(-59), end: today },
  ];
}

export function CustomRangeModal({ initial, onApply, onClose }) {
  const [range, setRange] = useState({ start: initial?.start || "", end: initial?.end || "" });
  const complete = Boolean(range.start && range.end);
  const error = complete ? validateCustomRange(range.start, range.end) : "";
  const span = complete && !error ? daysBetween(range.start, range.end) + 1 : 0;
  return (
    <Modal
      icon="ti-calendar-event"
      title="Custom range"
      description="Pick the first and last day to include."
      onClose={onClose}
      footer={(
        <>
          <span className="rp-foot-summary" role="status">
            {error || (complete ? `${formatPeriod(range.start, range.end)} · ${span} ${span === 1 ? "day" : "days"}` : range.start ? "Now pick the last day" : "Pick the first day")}
          </span>
          <button type="button" className="ghost-btn" onClick={onClose}>Cancel</button>
          <button type="button" className="primary-btn" disabled={!complete || Boolean(error)} onClick={() => onApply(range)}>
            <i className="ti ti-check" aria-hidden="true" /> Apply range
          </button>
        </>
      )}
    >
      <div className="rp-cal-wrap">
        <div className="rp-chips rp-chips--wrap" role="group" aria-label="Shortcuts">
          {quickRanges().map((item) => (
            <button key={item.label} type="button" className="rp-chip" onClick={() => setRange({ start: item.start, end: item.end })}>{item.label}</button>
          ))}
        </div>
        <DateRangeCalendar start={range.start} end={range.end} onChange={setRange} />
        {error && <div className="rp-inline-error" role="alert">{error}</div>}
      </div>
    </Modal>
  );
}
