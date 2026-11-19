/** How numbers, sizes, speeds and moments are written on the page. */

export const num = (value: number, digits = 0): string =>
  value.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });

const BYTE_UNITS = ["Б", "КБ", "МБ", "ГБ", "ТБ"] as const;

export function fmtBytes(bytes: number | null | undefined): string {
  if (!bytes) return "0";
  let amount = bytes;
  let index = 0;
  while (amount >= 1024 && index < BYTE_UNITS.length - 1) {
    amount /= 1024;
    index++;
  }
  return num(amount, amount >= 100 || index === 0 ? 0 : 1) + " " + BYTE_UNITS[index];
}

export function fmtBps(value: number | null | undefined): string {
  if (!value || value < 1000) return "0 Кбит/с";
  if (value < 1e6) return num(value / 1e3) + " Кбит/с";
  return num(value / 1e6, value < 1e7 ? 1 : 0) + " Мбит/с";
}

/** `ts` and `now` in seconds. */
export function ago(moment: number | null | undefined, now: number): string {
  if (!moment) return "никогда";
  const seconds = Math.max(0, Math.round(now - moment));
  if (seconds < 90) return "только что";
  if (seconds < 3600) return Math.round(seconds / 60) + " мин назад";
  if (seconds < 86400) return Math.round(seconds / 3600) + " ч назад";
  return Math.round(seconds / 86400) + " дн назад";
}

/** The right one of three word forms for a count: [1, 2…4, 5…]. */
export function plural(count: number, forms: readonly [string, string, string]): string {
  const lastTwoDigits = Math.abs(count) % 100;
  const lastDigit = lastTwoDigits % 10;
  if (lastTwoDigits > 10 && lastTwoDigits < 20) return forms[2];
  if (lastDigit > 1 && lastDigit < 5) return forms[1];
  return lastDigit === 1 ? forms[0] : forms[2];
}

/** Day and month, "07.03"; a number is seconds, a string is an ISO date. */
export function ddmm(moment: number | string): string {
  const date = new Date(typeof moment === "number" ? moment * 1000 : moment);
  return isNaN(date.getTime())
    ? ""
    : String(date.getDate()).padStart(2, "0") + "." + String(date.getMonth() + 1).padStart(2, "0");
}

export const hostOf = (endpoint: string | null | undefined): string =>
  endpoint ? String(endpoint).replace(/:\d+$/, "") : "—";

const MONTHS = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
] as const;

/** "сегодня, 14:05", "вчера, 09:30" or "3 марта"; takes milliseconds or an ISO date. */
export function whenText(when: number | string | null | undefined): string {
  if (!when) return "";
  const date = new Date(when);
  if (isNaN(date.getTime())) return "";
  const clock = String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0");
  const day = (day: Date) => new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime();
  const diff = Math.round((day(new Date()) - day(date)) / 86400000);
  if (diff === 0) return "сегодня, " + clock;
  if (diff === 1) return "вчера, " + clock;
  return date.getDate() + " " + MONTHS[date.getMonth()];
}

export const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
