/** The home country: whose networks go direct. ISO 3166 codes, lower case, as ipdeny names its lists. */
export const DEFAULT_COUNTRY = "ru";

/** The countries offered on the page, by code; any other code can be set from the agent's shell. */
export const COUNTRIES: ReadonlyArray<readonly [string, string]> = [
  ["ru", "Россия"],
  ["by", "Беларусь"],
  ["kz", "Казахстан"],
  ["uz", "Узбекистан"],
  ["kg", "Киргизия"],
  ["tj", "Таджикистан"],
  ["tm", "Туркмения"],
  ["am", "Армения"],
  ["az", "Азербайджан"],
  ["ge", "Грузия"],
  ["md", "Молдова"],
  ["ua", "Украина"],
  ["ir", "Иран"],
  ["tr", "Турция"],
  ["cn", "Китай"],
  ["ae", "ОАЭ"],
  ["rs", "Сербия"],
  ["de", "Германия"],
  ["nl", "Нидерланды"],
  ["fi", "Финляндия"],
  ["pl", "Польша"],
  ["us", "США"],
];

/** The name of a country on the page: its Russian name, or its code when it is not offered. */
export function countryName(code: string): string {
  return COUNTRIES.find(([offered]) => offered === code)?.[1] ?? code.toUpperCase();
}
