// The few pictures of the app, drawn inline.

export function Logo() {
  return (
    <svg width="34" height="34" viewBox="0 0 32 32" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" rx="9" fill="var(--accent)" />
      <circle cx="16" cy="16" r="3.2" fill="var(--background)" />
      <circle cx="8.5" cy="9" r="2" fill="var(--background)" />
      <circle cx="23.5" cy="9" r="2" fill="var(--background)" />
      <circle cx="8.5" cy="23" r="2" fill="var(--background)" />
      <circle cx="23.5" cy="23" r="2" fill="var(--background)" />
    </svg>
  );
}

export function PowerIcon({ size }: { size: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M12 3v8" />
      <path d="M6.3 6.8a8 8 0 1 0 11.4 0" />
    </svg>
  );
}

export enum TabIcon {
  Connection = "M12 3v7M6.6 6.6a7.5 7.5 0 1 0 10.8 0",
  Network = "M12 9.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5M5 4a2 2 0 1 0 0 4a2 2 0 1 0 0-4M19 4a2 2 0 1 0 0 4a2 2 0 1 0 0-4M5 16a2 2 0 1 0 0 4a2 2 0 1 0 0-4M19 16a2 2 0 1 0 0 4a2 2 0 1 0 0-4",
  Journal = "M12 3.5a8.5 8.5 0 1 0 0 17a8.5 8.5 0 1 0 0-17M12 7.5V12l3 2",
  Settings = "M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1",
}

export function TabPicture({ path }: { path: TabIcon }) {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d={path} />
    </svg>
  );
}

export function LockIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  );
}

export function SearchIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}
