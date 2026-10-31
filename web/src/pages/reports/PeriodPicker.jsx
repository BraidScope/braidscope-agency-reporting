// SPDX-License-Identifier: AGPL-3.0-only
import { useState } from "react";
import { Chips } from "./ReportControls";
import { CustomRangeModal } from "./DateRangeCalendar";
import { daysBetween, formatPeriod, PERIOD_OPTIONS } from "./reportModel";

// Period control shared by "New report" and the builder:
// 7 days / 30 days / 90 days / Custom range. Choosing Custom range opens the
// calendar; the chosen range is shown underneath. `period` is
// { mode: "preset" | "custom", days, start, end }.
export default function PeriodPicker({ period, options = PERIOD_OPTIONS, onPreset, onCustom, disabled = false, label = "Period" }) {
  const [calendarOpen, setCalendarOpen] = useState(false);
  const chips = [
    ...options.map((option) => ({ value: option.days, label: option.label.replace("Last ", "") })),
    { value: "custom", label: "Custom range" },
  ];
  const selected = period.mode === "custom" ? "custom" : period.days;

  return (
    <div className="rp-period">
      <Chips
        label={label}
        value={selected}
        options={chips}
        disabled={disabled}
        onChange={(value) => {
          if (value === "custom") setCalendarOpen(true);
          else onPreset(value);
        }}
      />
      {period.mode === "custom" ? (
        <button type="button" className="rp-range-summary" disabled={disabled} onClick={() => setCalendarOpen(true)} aria-label="Change custom range">
          <i className="ti ti-calendar-event" aria-hidden="true" />
          <span>{formatPeriod(period.start, period.end)}</span>
          <em>{daysBetween(period.start, period.end) + 1} days</em>
          {!disabled && <i className="ti ti-pencil rp-range-edit" aria-hidden="true" />}
        </button>
      ) : (
        <small className="rp-period-hint">{formatPeriod(period.start, period.end)}</small>
      )}
      {calendarOpen && (
        <CustomRangeModal
          initial={period.mode === "custom" ? { start: period.start, end: period.end } : undefined}
          onClose={() => setCalendarOpen(false)}
          onApply={(range) => { setCalendarOpen(false); onCustom(range); }}
        />
      )}
    </div>
  );
}
