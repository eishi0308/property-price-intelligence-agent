'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const LINKS = [
  { href: '/', label: 'Analyse', short: 'Analyse' },
  { href: '/how-it-works', label: 'How it works', short: 'How' },
];

/**
 * Primary navigation. Client-side only so the current destination can be marked
 * — an unmarked nav leaves the reader guessing where they are, and screen
 * readers with nothing at all to announce.
 */
export function SiteNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Primary" className="flex shrink-0 items-center gap-0.5 text-sm sm:gap-1">
      {LINKS.map((link) => {
        const active = link.href === '/' ? pathname === '/' : pathname.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            aria-current={active ? 'page' : undefined}
            className={`relative rounded-lg px-2.5 py-1.5 font-medium transition-colors sm:px-3 ${
              active
                ? 'text-ink-900'
                : 'text-ink-500 hover:bg-ink-100 hover:text-ink-900'
            }`}
          >
            <span className="sm:hidden">{link.short}</span>
            <span className="hidden sm:inline">{link.label}</span>
            {active && (
              <span
                aria-hidden
                className="absolute inset-x-2.5 -bottom-[13px] h-[2px] rounded-full bg-[rgb(var(--accent-solid))] sm:inset-x-3"
              />
            )}
          </Link>
        );
      })}
    </nav>
  );
}
