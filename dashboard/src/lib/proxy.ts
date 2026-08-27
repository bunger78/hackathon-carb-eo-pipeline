// Server-to-server call from carb-dash to carb-api, authenticated via the Cloud Run
// metadata server (OIDC identity token). Zero secrets stored here: the admin token
// travels through only, supplied by the browser caller on each request.

export async function callPipeline(path: string, body: unknown, adminToken: string): Promise<Response> {
  const base = process.env.PIPELINE_URL!; // e.g. https://carb-api-cx5tppcuda-uc.a.run.app
  const meta = `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${base}`;
  let idToken = '';
  try {
    idToken = await (await fetch(meta, { headers: { 'Metadata-Flavor': 'Google' } })).text();
  } catch {
    /* local dev: no metadata server; pipeline will 403 */
  }
  const res = await fetch(`${base}${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${idToken}`, 'X-Admin-Token': adminToken, 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  return new Response(await res.text(), { status: res.status });
}
