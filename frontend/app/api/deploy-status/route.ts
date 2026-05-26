export const dynamic = "force-dynamic";

export interface DeployStatusPayload {
  state: "BUILDING" | "QUEUED" | "READY" | "ERROR" | "CANCELED" | "UNKNOWN";
  buildTime: string | null;       // ISO — when this running instance was compiled
  deployedAt: string | null;      // ISO — when the latest Vercel deployment went live
  commitSha: string | null;
  commitMsg: string | null;
}

export async function GET() {
  const buildTime  = process.env.BUILD_TIME  ?? null;
  const commitSha  = process.env.VERCEL_GIT_COMMIT_SHA?.slice(0, 7) ?? null;
  const commitMsg  = process.env.VERCEL_GIT_COMMIT_MESSAGE ?? null;

  const token     = process.env.VERCEL_TOKEN;
  const projectId = process.env.VERCEL_PROJECT_ID;

  if (token && projectId) {
    try {
      const res = await fetch(
        `https://api.vercel.com/v9/deployments?projectId=${projectId}&limit=3&target=production`,
        { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
      );
      if (res.ok) {
        const json = await res.json();
        const latest = json.deployments?.[0];
        const state  = latest?.state ?? "UNKNOWN";
        const readyTs = latest?.ready ?? latest?.createdAt ?? null;
        return Response.json({
          state,
          buildTime,
          deployedAt: readyTs ? new Date(readyTs).toISOString() : null,
          commitSha: latest?.meta?.githubCommitSha?.slice(0, 7) ?? commitSha,
          commitMsg: latest?.meta?.githubCommitMessage ?? commitMsg,
        } satisfies DeployStatusPayload);
      }
    } catch {
      // fall through to basic response
    }
  }

  // No Vercel token — return build-time info only
  return Response.json({
    state: "UNKNOWN",
    buildTime,
    deployedAt: buildTime,
    commitSha,
    commitMsg,
  } satisfies DeployStatusPayload);
}
