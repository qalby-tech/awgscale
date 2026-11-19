/** The product's mark: a hub and four devices, the hub's wave for the protocol's disguise. Same drawing as docs/brand/mark.svg. */
export function BrandMark() {
  return (
    <svg className="mark" viewBox="0 0 64 64" width="28" height="28" aria-hidden="true">
      <rect width="64" height="64" rx="16" fill="currentColor" />
      <g stroke="#fff" strokeWidth="3" strokeLinecap="round" fill="none" opacity=".55">
        <path d="M32 32 L16 18 M32 32 L48 18 M32 32 L16 46 M32 32 L48 46" />
      </g>
      <path
        d="M16 18 Q32 10 48 18 M48 46 Q32 54 16 46"
        stroke="#fff"
        strokeWidth="3"
        strokeLinecap="round"
        fill="none"
        opacity=".35"
      />
      <g fill="#fff">
        <circle cx="32" cy="32" r="7" />
        <circle cx="16" cy="18" r="4.5" />
        <circle cx="48" cy="18" r="4.5" />
        <circle cx="16" cy="46" r="4.5" />
        <circle cx="48" cy="46" r="4.5" />
      </g>
      <path d="M27 32 q2.5 -3 5 0 t5 0" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" fill="none" />
    </svg>
  );
}
