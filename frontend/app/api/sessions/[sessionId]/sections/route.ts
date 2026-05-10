import { NextRequest } from "next/server";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> }
) {
  const { sessionId } = await params;

  const backendRes = await fetch(
    `${BACKEND}/api/sessions/${sessionId}/sections`
  );

  const data = await backendRes.json();
  return Response.json(data, { status: backendRes.status });
}
