/** The sections of the side menu, in their order. */
import { Route, Section } from "@/lib/types";
import { IconName } from "@/ui/Icon";

export interface SectionInfo {
  section: Section;
  route: Route;
  title: string;
  /** Under the icon in the bar of a phone. */
  short: string;
  icon: IconName;
}

export const SECTIONS: readonly SectionInfo[] = [
  { section: Section.Overview, route: Route.Overview, title: "Обзор", short: "Обзор", icon: IconName.Grid },
  { section: Section.Users, route: Route.Users, title: "Пользователи", short: "Люди", icon: IconName.Users },
  { section: Section.Sites, route: Route.Sites, title: "Сайты", short: "Сайты", icon: IconName.Globe },
  { section: Section.Dns, route: Route.Dns, title: "DNS без VPN", short: "DNS", icon: IconName.Screen },
  { section: Section.Servers, route: Route.Servers, title: "Серверы", short: "Серверы", icon: IconName.Server },
  { section: Section.Journal, route: Route.Journal, title: "Журнал", short: "Журнал", icon: IconName.Clock },
];

export const PRODUCT = "awgscale";
