"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Menu, X } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { Button } from "@/components/ui/Button";

function cx(...parts: Array<string | undefined | false>) {
  return parts.filter(Boolean).join(" ");
}

function NavLink({ href, label }: { href: string; label: string }) {
  const pathname = usePathname();
  const active = pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      className={cx(
        "relative rounded-xl px-3 py-2 text-sm font-medium transition",
        active ? "text-slate-900" : "text-slate-600 hover:text-slate-900"
      )}
    >
      {label}
      {active ? (
        <motion.span
          layoutId="nav-underline"
          className="absolute inset-x-2 -bottom-1 h-[2px] rounded-full bg-gradient-to-r from-emerald-500 via-blue-500 to-fuchsia-500"
        />
      ) : null}
    </Link>
  );
}

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/content", label: "Content" },
  { href: "/queue", label: "Queue" },
  { href: "/analytics", label: "Analytics" },
  { href: "/connections", label: "Connections" }
] as const;

function apiDocsHref(): string {
  const explicit = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  return explicit ? `${explicit.replace(/\/$/, "")}/docs` : "/ingest-reforge/docs";
}

export function TopNav() {
  const { user, isAuthenticated, logout, isLoading } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  return (
    <div className="flex flex-shrink-0 items-center gap-2 sm:gap-3">
      {isAuthenticated && mobileOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-slate-900/20 lg:hidden"
          aria-label="Dismiss menu"
          onClick={() => setMobileOpen(false)}
        />
      ) : null}
      {isAuthenticated ? (
        <>
          <nav className="hidden items-center gap-0.5 lg:flex xl:gap-1" aria-label="Main">
            {NAV_ITEMS.map((item) => (
              <NavLink key={item.href} href={item.href} label={item.label} />
            ))}
          </nav>

          <div className="relative z-50 lg:hidden">
            <button
              type="button"
              className="inline-flex min-h-10 min-w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-900 shadow-sm transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-500/25"
              aria-expanded={mobileOpen}
              aria-controls="mobile-nav-menu"
              aria-label={mobileOpen ? "Close menu" : "Open menu"}
              onClick={() => setMobileOpen((o) => !o)}
            >
              {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>

            <AnimatePresence>
              {mobileOpen ? (
                <motion.div
                  id="mobile-nav-menu"
                  initial={{ opacity: 0, y: -6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{ duration: 0.2 }}
                  className="absolute right-0 z-50 mt-2 w-[min(100vw-2rem,20rem)] rounded-2xl border border-slate-200 bg-white p-2 shadow-xl"
                >
                  <nav className="flex flex-col gap-0.5" aria-label="Mobile main">
                    {NAV_ITEMS.map((item) => {
                      const active =
                        pathname === item.href || pathname.startsWith(`${item.href}/`);
                      return (
                        <Link
                          key={item.href}
                          href={item.href}
                          className={
                            active
                              ? "rounded-xl bg-slate-900 px-4 py-3 text-sm font-semibold text-white"
                              : "rounded-xl px-4 py-3 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                          }
                        >
                          {item.label}
                        </Link>
                      );
                    })}
                    <a
                      href={apiDocsHref()}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-1 rounded-xl px-4 py-3 text-sm font-semibold text-slate-600 hover:bg-slate-50"
                    >
                      API Docs
                    </a>
                  </nav>
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
        </>
      ) : null}

      <div className="ml-1 flex items-center gap-2 sm:ml-2">
        {isAuthenticated ? (
          <>
            <div className="hidden max-w-[10rem] truncate text-sm text-slate-600 sm:max-w-[14rem] md:block">
              {user?.display_name || user?.email}
            </div>
            <Button
              onClick={() => void logout()}
              disabled={isLoading}
              className="min-h-10 shrink-0"
            >
              Logout
            </Button>
          </>
        ) : (
          <Button asChild variant="primary" className="min-h-10">
            <Link href="/login">Sign in</Link>
          </Button>
        )}
      </div>
    </div>
  );
}

