import { NextRequest, NextResponse } from "next/server";

const backendBaseUrl = () =>
  (process.env.BACKEND_BASE_URL || process.env.NEXT_PUBLIC_BACKEND_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = `${backendBaseUrl()}/${path.join("/")}${request.nextUrl.search}`;
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("connection");

  const apiKey = process.env.BACKEND_API_KEY || process.env.INGEST_API_KEY || "";
  if (apiKey) headers.set("Authorization", `Bearer ${apiKey}`);

  const init: RequestInit = {
    method: request.method,
    headers,
    cache: "no-store"
  };
  if (!["GET", "HEAD"].includes(request.method)) {
    init.body = await request.text();
  }

  const response = await fetch(target, init);
  const responseHeaders = new Headers(response.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");
  return new NextResponse(await response.arrayBuffer(), {
    status: response.status,
    headers: responseHeaders
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
