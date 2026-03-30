/**
 * Auth layout: centered card layout for login/signup pages.
 * Dark background with music-themed branding.
 */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4">
      <div className="mb-8 flex flex-col items-center text-center">
        <img src="/logo-color.svg" alt="SmarTaste" className="mb-4 h-16 w-16" />
        <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl" style={{ fontFamily: "var(--font-heading)" }}>
          SmarTaste
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Your music, understood
        </p>
      </div>
      <div className="w-full max-w-md">{children}</div>
    </div>
  );
}
