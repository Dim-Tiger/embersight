import { redirect } from "next/navigation";

// Root URL serves the marketing landing. The static site lives in
// `public/landing/` and uses relative asset paths (assets/logo.png,
// styles.css, intro-fire.js), so we redirect rather than rewrite —
// rewriting would leave the browser URL at `/` and break those
// relative references. The "Enter app" CTAs on the landing send
// users to `/dashboard`.
export default function Page() {
  redirect("/landing/index.html");
}
