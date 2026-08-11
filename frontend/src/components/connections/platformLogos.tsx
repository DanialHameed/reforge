"use client";

export function YouTubeLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path
        d="M21.6 7.2a3 3 0 0 0-2.1-2.1C17.8 4.6 12 4.6 12 4.6s-5.8 0-7.5.5A3 3 0 0 0 2.4 7.2 31.2 31.2 0 0 0 2 12a31.2 31.2 0 0 0 .4 4.8 3 3 0 0 0 2.1 2.1c1.7.5 7.5.5 7.5.5s5.8 0 7.5-.5a3 3 0 0 0 2.1-2.1A31.2 31.2 0 0 0 22 12a31.2 31.2 0 0 0-.4-4.8Z"
        fill="currentColor"
        opacity="0.9"
      />
      <path d="M10 15.5v-7l6 3.5-6 3.5Z" fill="white" />
    </svg>
  );
}

export function InstagramLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="ig" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#f59e0b" />
          <stop offset="0.5" stopColor="#a855f7" />
          <stop offset="1" stopColor="#3b82f6" />
        </linearGradient>
      </defs>
      <rect x="3" y="3" width="18" height="18" rx="5" fill="url(#ig)" />
      <path
        d="M12 16.6a4.6 4.6 0 1 0 0-9.2 4.6 4.6 0 0 0 0 9.2Z"
        fill="white"
        opacity="0.9"
      />
      <circle cx="17.2" cy="6.8" r="1.1" fill="white" />
      <circle cx="12" cy="12" r="2.6" fill="url(#ig)" opacity="0.25" />
    </svg>
  );
}

export function FacebookLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path
        d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Z"
        fill="currentColor"
        opacity="0.9"
      />
      <path
        d="M13.6 12.1h2.2l.3-2.3h-2.5V8.4c0-.7.2-1.2 1.3-1.2h1.3V5.1c-.2 0-.9-.1-1.8-.1-1.8 0-3 1.1-3 3.1v1.7H9.6v2.3h1.8V19h2.2v-6.9Z"
        fill="white"
      />
    </svg>
  );
}

export function XLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path
        d="M18.2 2H21l-6.1 7 7.1 13H16l-4.7-8.2L4.5 22H2l6.6-7.7L1.8 2H8l4.2 7.4L18.2 2Zm-1.4 18h1.6L7.2 3.9H5.5L16.8 20Z"
        fill="currentColor"
        opacity="0.9"
      />
    </svg>
  );
}

export function LinkedInLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path
        d="M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"
        fill="currentColor"
        opacity="0.9"
      />
      <path d="M7.2 9.6h2.2V18H7.2V9.6Zm1.1-3.6a1.3 1.3 0 1 1 0 2.6 1.3 1.3 0 0 1 0-2.6ZM11 9.6h2.1v1.1h.1c.3-.6 1.1-1.3 2.4-1.3 2.6 0 3.1 1.7 3.1 3.9V18h-2.2v-4c0-1 0-2.2-1.4-2.2-1.4 0-1.6 1-1.6 2.2V18H11V9.6Z" fill="white" />
    </svg>
  );
}

