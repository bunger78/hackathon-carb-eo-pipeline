import type { APIRoute } from 'astro';
import { getOverviewData } from '../../lib/overview';

export const GET: APIRoute = async () => {
  const data = await getOverviewData();
  return new Response(JSON.stringify(data), {
    headers: { 'Content-Type': 'application/json' },
  });
};
