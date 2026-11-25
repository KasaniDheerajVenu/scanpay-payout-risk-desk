import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { Card } from "@/components/ui/card"
import { formatCents, formatDate, formatNumber } from "@/lib/format"
import type { Historical } from "@/services/risk"

export type DistributionPoint = { label: string; short: string; count: number; color: string }
const chartMoney = (value: number) => formatCents(value)

export default function DashboardCharts({ distribution, history }: { distribution: DistributionPoint[]; history: Historical | null }) {
  const daily = history?.days.map((day) => ({
    date: formatDate(day.date_ist).split(" ").slice(0, 2).join(" "),
    auto: day.totals?.AUTO_APPROVED.cents ?? null,
    analyst: day.totals?.MANUAL_APPROVED.cents ?? null,
    rejected: day.totals?.REJECTED.cents ?? null,
  }))
  return <div className="risk-charts">
    <Card className="content-card">
      <div className="section-heading"><h2>Decision distribution</h2><span className="section-count">Payout count</span></div>
      <div className="chart-frame" role="region" aria-label="Effective decision distribution">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <BarChart data={distribution} accessibilityLayer margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="var(--border)" />
            <XAxis dataKey="short" tickLine={false} axisLine={false} tick={{ fontSize: 11 }} interval={0} />
            <YAxis allowDecimals={false} width={30} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
            <Tooltip labelFormatter={(label) => distribution.find((point) => point.short === label)?.label ?? label}
              formatter={(value) => [formatNumber(Number(value)), "Payouts"]} />
            <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={52} isAnimationActive={false}>
              {distribution.map((point) => <Cell key={point.short} fill={point.color} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
    <Card className="content-card">
      <div className="section-heading"><h2>Historical payout trends</h2><span className="section-count">USD per day</span></div>
      {history && daily && history.recorded_days > 0 ? <>
        <div className="chart-frame" role="region" aria-label="Historical payout amounts by disposition">
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <LineChart data={daily} accessibilityLayer margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="var(--border)" />
              <XAxis dataKey="date" minTickGap={25} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
              <YAxis width={59} tickFormatter={chartMoney} tickLine={false} axisLine={false} tick={{ fontSize: 10 }} />
              <Tooltip formatter={(value) => chartMoney(Number(value))} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line name="Automatic approval" dataKey="auto" stroke="var(--decision-auto)" isAnimationActive={false} />
              <Line name="Resolved analyst approval" dataKey="analyst" stroke="var(--decision-analyst)" isAnimationActive={false} />
              <Line name="Rejected" dataKey="rejected" stroke="var(--decision-reject)" isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <p className="text-xs text-muted-foreground">Gaps indicate unknown coverage. Known non-settlement days show zero.</p>
      </> : <p className="text-sm text-muted-foreground">No recorded historical payout days are available for this run.</p>}
    </Card>
  </div>
}
