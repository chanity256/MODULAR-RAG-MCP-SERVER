export interface NavItem {
  href: string;
  label: string;
  icon:
    | "chat"
    | "overview"
    | "ingestion-traces"
    | "query-traces"
    | "data-browser"
    | "ingestion-manager"
    | "evaluation"
    | "playground";
  /** Spec task that delivers the real page; remove once the page is live. */
  comingIn?: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/", label: "Chat", icon: "chat", comingIn: "WC8" },
  { href: "/overview", label: "Overview", icon: "overview", comingIn: "WC3.3" },
  { href: "/ingestion-traces", label: "Ingestion Traces", icon: "ingestion-traces", comingIn: "WC3.3" },
  { href: "/query-traces", label: "Query Traces", icon: "query-traces", comingIn: "WC3.3" },
  { href: "/data-browser", label: "Data Browser", icon: "data-browser", comingIn: "WC9" },
  { href: "/ingestion-manager", label: "Ingestion Manager", icon: "ingestion-manager", comingIn: "WC9" },
  { href: "/evaluation", label: "Evaluation", icon: "evaluation", comingIn: "WC9" },
  { href: "/query-playground", label: "Query Playground", icon: "playground", comingIn: "WC9" },
];

/** Strip the trailing slash that `trailingSlash: true` adds to pathnames (root stays "/"). */
export function normalizePathname(pathname: string): string {
  return pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
}

export function isActive(itemHref: string, pathname: string): boolean {
  return normalizePathname(pathname) === itemHref;
}
