// NEXT_OUTPUT_MODE=export builds a static site for AWS Amplify Hosting (see
// amplify.yml). Every route is client-rendered, so nothing is lost. Without it,
// the standalone server output feeds infra/docker/frontend.Dockerfile.
const staticExport = process.env.NEXT_OUTPUT_MODE === "export";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: staticExport ? "export" : "standalone",
  poweredByHeader: false,
  // A static export has no server to send headers from; Amplify sets them
  // instead (custom_headers in infra/terraform/modules/frontend).
  ...(staticExport
    ? {}
    : {
        async headers() {
          return [
            {
              source: "/:path*",
              headers: [
                { key: "X-Content-Type-Options", value: "nosniff" },
                { key: "X-Frame-Options", value: "DENY" },
                { key: "Referrer-Policy", value: "no-referrer" },
              ],
            },
          ];
        },
      }),
};
export default nextConfig;
