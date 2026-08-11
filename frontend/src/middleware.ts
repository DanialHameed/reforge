import { NextResponse, type NextRequest } from "next/server";

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const isLogin = pathname === "/login";
  const isRegister = pathname === "/register";
  const isPublic = pathname === "/" || isLogin || isRegister;

  const isAuthenticated = req.cookies.get("is_authenticated")?.value === "1";

  if (!isAuthenticated && !isPublic) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  if (isAuthenticated && (isLogin || isRegister)) {
    const url = req.nextUrl.clone();
    url.pathname = "/";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|favicon-32.png|apple-touch-icon.png|icon-192.png|icon-512.png|icon-512-maskable.png|manifest.json|robots.txt|sitemap.xml|ingest-reforge/).*)",
  ],
};

