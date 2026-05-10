import { NextRequest } from "next/server";
import http from "http";

const BACKEND_HOST = "localhost";
const BACKEND_PORT = 8000;

export const dynamic = "force-dynamic";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> }
) {
  const { sessionId } = await params;
  const body = await request.text();
  const bodyBuf = Buffer.from(body);

  const stream = new ReadableStream({
    start(controller) {
      const options: http.RequestOptions = {
        hostname: BACKEND_HOST,
        port: BACKEND_PORT,
        path: `/api/sessions/${sessionId}/generate`,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": bodyBuf.byteLength,
        },
      };

      const req = http.request(options, (res) => {
        res.on("data", (chunk: Buffer) => {
          controller.enqueue(new Uint8Array(chunk));
        });
        res.on("end", () => controller.close());
        res.on("error", (err) => controller.error(err));
      });

      req.on("error", (err) => controller.error(err));
      req.write(bodyBuf);
      req.end();
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
