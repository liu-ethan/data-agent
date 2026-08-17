export function SettingsPage({
  timezone,
  error,
  onSave,
  onBack,
}: {
  timezone: string
  error?: string
  onSave: (value: string) => void
  onBack: () => void
}) {
  return (
    <main className="settings-page">
      <section className="settings-panel" aria-label="用户设置">
        <p className="settings-eyebrow">已确认的长期偏好</p>
        <h1>分析设置</h1>
        <label htmlFor="timezone">默认时区</label>
        <select
          id="timezone"
          value={timezone}
          onChange={event => onSave(event.target.value)}
        >
          <option>Asia/Shanghai</option>
          <option>UTC</option>
          <option>America/New_York</option>
          <option>Europe/London</option>
        </select>
        <p>显式查询条件始终优先于这里的默认值。</p>
        {error && <p className="settings-error" role="alert">{error}</p>}
        <button className="settings-back" onClick={onBack}>返回工作台</button>
      </section>
    </main>
  )
}
