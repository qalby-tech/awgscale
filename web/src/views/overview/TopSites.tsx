/** What was opened most over the last day: through the exits, and directly. */
import { num } from "@/lib/format";
import type { DnsSummary, TopSite } from "@/lib/types";
import { Icon, IconName } from "@/ui/Icon";

const SHOWN = 7;

const visible = (list: TopSite[] | undefined): TopSite[] =>
  (list ?? []).filter(site => site && site.name && site.count > 0).slice(0, SHOWN);

function TopList({
  id,
  label,
  icon,
  list,
  total,
}: {
  id: string;
  label: string;
  icon: IconName;
  list: TopSite[];
  total: number;
}) {
  return (
    <div className="top-col">
      <h3 className="top-h">
        <Icon name={icon} size={16} />
        {label}
      </h3>
      <ol className="top" id={id} aria-label={label}>
        {list.length ? (
          list.map((site, index) => (
            <li
              key={site.name}
              className="top-row"
              title={
                site.name + " — " + num(site.count) + (total ? " · " + num((100 * site.count) / total, 1) + "%" : "")
              }
            >
              <span className="top-pos">{index + 1}</span>
              <span className="top-name">{site.name}</span>
              <span className="top-n">{num(site.count)}</span>
              <span className="top-bar" aria-hidden="true">
                <i style={{ width: Math.max(1, (100 * site.count) / list[0].count) + "%" }} />
              </span>
            </li>
          ))
        ) : (
          <li className="top-none">нет данных</li>
        )}
      </ol>
    </div>
  );
}

export function TopSites({ dns }: { dns: DnsSummary }) {
  const abroad = visible(dns.top_exit);
  const direct = visible(dns.top_direct);
  const total = (value: number | undefined) => (value && value > 0 ? value : 0);
  return (
    <section
      className="card pad top-card"
      id="topCard"
      aria-label="Популярные сайты за сутки"
      hidden={!abroad.length && !direct.length}
    >
      <h2 className="card-title">Популярные сайты за сутки</h2>
      <div className="top-cols">
        <TopList
          id="topExit"
          label="Через заграницу"
          icon={IconName.Globe}
          list={abroad}
          total={total(dns.top_exit_total)}
        />
        <TopList
          id="topDirect"
          label="Напрямую из России"
          icon={IconName.Home}
          list={direct}
          total={total(dns.top_direct_total)}
        />
      </div>
    </section>
  );
}
