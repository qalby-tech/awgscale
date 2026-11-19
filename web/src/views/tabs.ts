/** The tabs of the sections that have them. */
import { Route } from "@/lib/types";
import type { Tabs } from "@/ui/PageHead";

export const USER_TABS: Tabs = [
  [Route.Users, "Список"],
  [Route.Spaces, "Пространства"],
];
export const SITE_TABS: Tabs = [
  [Route.Sites, "Через заграницу"],
  [Route.Blocked, "Блокируется"],
];
