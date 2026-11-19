/** The sections of the side menu, in their order. */
import { Route, Section } from "@/lib/types";
import { IconName } from "@/ui/Icon";

export interface SectionInfo {
  section: Section;
  route: Route;
  title: string;
  icon: IconName;
}

export const SECTIONS: readonly SectionInfo[] = [
  { section: Section.Overview, route: Route.Overview, title: "Обзор", icon: IconName.Grid },
  { section: Section.Users, route: Route.Users, title: "Пользователи", icon: IconName.Users },
  { section: Section.Sites, route: Route.Sites, title: "Сайты", icon: IconName.Globe },
  { section: Section.Dns, route: Route.Dns, title: "DNS без VPN", icon: IconName.Screen },
  { section: Section.Servers, route: Route.Servers, title: "Серверы", icon: IconName.Server },
  { section: Section.Journal, route: Route.Journal, title: "Журнал", icon: IconName.Clock },
];

export const PRODUCT = "awgscale";
