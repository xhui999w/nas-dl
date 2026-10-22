const formatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function TaskDate({ value }: { value?: string }) {
  if (!value) return null;
  // Existing database timestamps are UTC even when the API omits the suffix.
  const timestamp = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(timestamp);
  if (!Number.isFinite(date.getTime())) return null;
  return <time className="task-date" dateTime={date.toISOString()} title="任务提交时间（北京时间）">下载日期：{formatter.format(date).replaceAll("/", "-")}</time>;
}
