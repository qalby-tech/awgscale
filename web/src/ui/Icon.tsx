/** The page's own line icons, drawn on a 24×24 grid. */
import type { ReactNode } from "react";

export enum IconName {
  Star = "star",
  Grid = "grid",
  Network = "network",
  Users = "users",
  Globe = "globe",
  Screen = "screen",
  Clock = "clock",
  Pause = "pause",
  Server = "server",
  Logout = "logout",
  Plus = "plus",
  Minus = "minus",
  Fit = "fit",
  More = "more",
  X = "x",
  Search = "search",
  Copy = "copy",
  Down = "down",
  Chevron = "chev",
  Sync = "sync",
  Link = "link",
  Home = "home",
  Alert = "alert",
  Ban = "ban",
  Qr = "qr",
}

const SHAPES: Record<IconName, ReactNode> = {
  [IconName.Star]: (
    <>
      <path d="M12 4v16" />
      <path d="M5.1 8l13.8 8" />
      <path d="M18.9 8L5.1 16" />
    </>
  ),
  [IconName.Grid]: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>
  ),
  [IconName.Network]: (
    <>
      <circle cx="12" cy="12" r="2.5" />
      <circle cx="5" cy="6" r="2" />
      <circle cx="19" cy="6" r="2" />
      <circle cx="5" cy="18" r="2" />
      <circle cx="19" cy="18" r="2" />
      <path d="M6.6 7.3 10 10.3M17.4 7.3 14 10.3M6.6 16.7 10 13.7M17.4 16.7 14 13.7" />
    </>
  ),
  [IconName.Users]: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20c.6-3.4 3.2-5.5 6.5-5.5s5.9 2.1 6.5 5.5" />
      <circle cx="17.5" cy="9" r="2.5" />
      <path d="M16.5 14.6c2.6.2 4.4 1.9 5 4.9" />
    </>
  ),
  [IconName.Globe]: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3c2.8 3 2.8 15 0 18" />
      <path d="M12 3c-2.8 3-2.8 15 0 18" />
    </>
  ),
  [IconName.Screen]: (
    <>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8" />
      <path d="M12 16v4" />
    </>
  ),
  [IconName.Clock]: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  [IconName.Pause]: <path d="M9 5v14M15 5v14" />,
  [IconName.Server]: (
    <>
      <rect x="3" y="4" width="18" height="7" rx="2" />
      <rect x="3" y="13" width="18" height="7" rx="2" />
      <path d="M7 7.5h.01" />
      <path d="M7 16.5h.01" />
    </>
  ),
  [IconName.Logout]: (
    <>
      <path d="M9 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3" />
      <path d="M15 16l4-4-4-4" />
      <path d="M19 12H9" />
    </>
  ),
  [IconName.Plus]: (
    <>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </>
  ),
  [IconName.Minus]: <path d="M5 12h14" />,
  [IconName.Fit]: (
    <>
      <path d="M4 9V4h5" />
      <path d="M20 9V4h-5" />
      <path d="M4 15v5h5" />
      <path d="M20 15v5h-5" />
    </>
  ),
  [IconName.More]: (
    <>
      <path d="M12 5h.01" />
      <path d="M12 12h.01" />
      <path d="M12 19h.01" />
    </>
  ),
  [IconName.X]: (
    <>
      <path d="M6 6l12 12" />
      <path d="M18 6L6 18" />
    </>
  ),
  [IconName.Search]: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" />
    </>
  ),
  [IconName.Copy]: (
    <>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V6a2 2 0 0 1 2-2h9" />
    </>
  ),
  [IconName.Down]: (
    <>
      <path d="M12 5v14" />
      <path d="M6 13l6 6 6-6" />
    </>
  ),
  [IconName.Chevron]: <path d="M9 6l6 6-6 6" />,
  [IconName.Sync]: (
    <>
      <path d="M20 12a8 8 0 1 1-2.3-5.7" />
      <path d="M20 4v5h-5" />
    </>
  ),
  [IconName.Link]: (
    <>
      <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.2 1.2" />
      <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.2-1.2" />
    </>
  ),
  [IconName.Home]: (
    <>
      <path d="M4 11l8-7 8 7" />
      <path d="M6 10v9h12v-9" />
    </>
  ),
  [IconName.Alert]: (
    <>
      <path d="M12 4l9 16H3z" />
      <path d="M12 10v4" />
      <path d="M12 17h.01" />
    </>
  ),
  [IconName.Ban]: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M5.7 5.7l12.6 12.6" />
    </>
  ),
  [IconName.Qr]: (
    <>
      <rect x="4" y="4" width="6" height="6" rx="1" />
      <rect x="14" y="4" width="6" height="6" rx="1" />
      <rect x="4" y="14" width="6" height="6" rx="1" />
      <path d="M14 14h2v2h-2z" />
      <path d="M18 18h2v2h-2z" />
      <path d="M18 14h2" />
      <path d="M14 18v2" />
    </>
  ),
};

const DEFAULT_SIZE = 18;

interface IconProps {
  name: IconName;
  size?: number;
  /** Stroke width, when the default of the stylesheet does not suit. */
  stroke?: number;
  /** With a label the icon is an image that says something; without, decoration. */
  label?: string;
}

export function Icon({ name, size = DEFAULT_SIZE, stroke, label }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className="ic"
      style={stroke ? { strokeWidth: stroke } : undefined}
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
    >
      {SHAPES[name]}
    </svg>
  );
}
