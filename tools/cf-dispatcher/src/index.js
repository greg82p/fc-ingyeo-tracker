// collect 워크플로를 GitHub REST API(workflow_dispatch)로 실행시키는 Worker.
// Cloudflare Cron Trigger 가 scheduled() 를 호출하면 GitHub 에 dispatch 이벤트를 보낸다.
async function dispatch(env, reason) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "fc-ingyeo-dispatcher",          // GitHub API 는 User-Agent 필수
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: env.GH_REF || "main" }),
  });
  const msg = `[dispatch:${reason}] ${res.status} ${res.status === 204 ? "ok" : await res.text()}`;
  console.log(msg);
  return msg;
}

export default {
  // Cron Trigger
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(dispatch(env, controller.cron));
  },
  // 수동 점검용: GET https://<worker>.workers.dev/ → 상태만, dispatch 하지 않음.  ?run=1 을 붙이면 1회 실행 (토큰 없이 누구나 호출 가능하므로 필요 없으면 지울 것)
  async fetch(request, env) {
    const u = new URL(request.url);
    if (u.searchParams.get("run") === "1") return new Response(await dispatch(env, "manual"), { status: 200 });
    return new Response(`fc-ingyeo-dispatcher · crons → ${env.GH_OWNER}/${env.GH_REPO}/${env.GH_WORKFLOW}`, { status: 200 });
  },
};
