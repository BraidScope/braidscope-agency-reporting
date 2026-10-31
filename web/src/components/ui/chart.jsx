// SPDX-License-Identifier: AGPL-3.0-only
import * as React from "react"
import * as RechartsPrimitive from "recharts"

import { cn } from "@/lib/utils"

const ChartContext = React.createContext(null)

function useChart() {
  const context = React.useContext(ChartContext)
  if (!context) {
    throw new Error("useChart must be used within a <ChartContainer />")
  }
  return context
}

function ChartContainer({ id, className, children, config, ...props }) {
  const uniqueId = React.useId()
  const chartId = `chart-${id || uniqueId.replace(/:/g, "")}`
  const ref = React.useRef(null)

  React.useEffect(() => {
    if (!ref.current) return
    const svg = ref.current.querySelector("svg")
    if (svg) {
      svg.setAttribute("overflow", "visible")
      svg.style.overflow = "visible"
    }
    const wrapper = ref.current.querySelector(".recharts-wrapper")
    if (wrapper) wrapper.style.overflow = "visible"
  })

  return (
    <ChartContext.Provider value={{ config }}>
      <div
        ref={ref}
        data-chart={chartId}
        className={cn(
          "flex aspect-video justify-center text-xs overflow-visible [&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground [&_.recharts-grid_line]:stroke-border/70 [&_.recharts-tooltip-cursor]:stroke-border [&_.recharts-wrapper]:outline-none [&_.recharts-wrapper]:overflow-visible [&_.recharts-surface]:overflow-visible [&_svg]:overflow-visible",
          className
        )}
        {...props}
      >
        <ChartStyle id={chartId} config={config} />
        <RechartsPrimitive.ResponsiveContainer>
          {children}
        </RechartsPrimitive.ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  )
}

function ChartStyle({ id, config }) {
  const colorConfig = Object.entries(config).filter(([, itemConfig]) => itemConfig.color)
  if (!colorConfig.length) return null

  return (
    <style
      dangerouslySetInnerHTML={{
        __html: `
[data-chart=${id}] {
${colorConfig.map(([key, itemConfig]) => `  --color-${key}: ${itemConfig.color};`).join("\n")}
}
`,
      }}
    />
  )
}

const ChartTooltip = RechartsPrimitive.Tooltip
const ChartLegend = RechartsPrimitive.Legend

function ChartTooltipContent({ active, payload, className, labelFormatter, formatter }) {
  const { config } = useChart()

  if (!active || !payload?.length) return null

  return (
    <div
      className={cn("inline-block rounded-xl border border-border bg-popover py-3 text-xs shadow-xl backdrop-blur", className)}
      style={{ paddingLeft: 16, paddingRight: 24 }}
    >
      {payload[0]?.payload?.date && (
        <div className="font-semibold text-foreground whitespace-nowrap mb-2">
          {labelFormatter ? labelFormatter(payload[0].payload.date) : payload[0].payload.date}
        </div>
      )}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto auto",
          columnGap: "20px",
          rowGap: "6px",
          alignItems: "center",
        }}
      >
        {payload.map((item) => {
          const key = item.dataKey
          const itemConfig = config?.[key] || {}
          return (
            <React.Fragment key={key}>
              <div className="flex items-center gap-2 text-muted-foreground whitespace-nowrap">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: item.color || itemConfig.color }}
                />
                {itemConfig.label || key}
              </div>
              <div
                className="font-mono font-semibold tabular-nums text-foreground whitespace-nowrap text-right"
                style={{ paddingRight: 8 }}
              >
                {formatter ? formatter(item.value, key) : item.value}
              </div>
            </React.Fragment>
          )
        })}
      </div>
    </div>
  )
}

function ChartLegendContent({ payload, className }) {
  const { config } = useChart()

  if (!payload?.length) return null

  return (
    <div className={cn("flex flex-wrap items-center justify-center gap-4 text-xs", className)}>
      {payload.map((item) => {
        const key = item.dataKey || item.value
        const itemConfig = config?.[key] || {}
        return (
          <div key={key} className="flex items-center gap-2 font-medium text-muted-foreground">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: item.color || itemConfig.color }}
            />
            {itemConfig.label || item.value}
          </div>
        )
      })}
    </div>
  )
}

export { ChartContainer, ChartTooltip, ChartTooltipContent, ChartLegend, ChartLegendContent }