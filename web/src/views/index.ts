/** Which component draws which route. */
import type { ComponentType } from "react";

import { Route } from "@/lib/types";

import { BlockedPage } from "./blocked/BlockedPage";
import { DnsPage } from "./dns/DnsPage";
import { JournalPage } from "./journal/JournalPage";
import { NetworkPage } from "./network/NetworkPage";
import { OverviewPage } from "./overview/OverviewPage";
import { ServersPage } from "./servers/ServersPage";
import { SitesPage } from "./sites/SitesPage";
import { SpacesPage } from "./spaces/SpacesPage";
import { UsersPage } from "./users/UsersPage";

export const PAGES: Record<Route, ComponentType> = {
  [Route.Overview]: OverviewPage,
  [Route.Network]: NetworkPage,
  [Route.Users]: UsersPage,
  [Route.Spaces]: SpacesPage,
  [Route.Sites]: SitesPage,
  [Route.Blocked]: BlockedPage,
  [Route.Dns]: DnsPage,
  [Route.Servers]: ServersPage,
  [Route.Journal]: JournalPage,
};
