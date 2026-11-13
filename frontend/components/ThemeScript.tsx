/*
 * Derived from sirchmunk `web/components/ThemeScript.tsx` (Apache-2.0). Modified:
 * project-owned storage key, no write-back of the system preference. See ../NOTICE.
 */
import { THEME_STORAGE_KEY } from "@/lib/theme";

/**
 * Runs before hydration to set the `dark` class from localStorage (falling back to the
 * system preference) so the page never flashes the wrong theme.
 */
export default function ThemeScript() {
  const key = JSON.stringify(THEME_STORAGE_KEY);
  const script = `(function(){try{var s=localStorage.getItem(${key});var d=s==="dark"||(s!=="light"&&window.matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d);}catch(e){}})();`;
  return <script dangerouslySetInnerHTML={{ __html: script }} />;
}
