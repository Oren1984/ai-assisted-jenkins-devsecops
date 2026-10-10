// Captures the README screenshots from the running stack with a Playwright-compatible
// driver. It default-exports `async (page) => result`, so any runner that hands it a
// Playwright Page works. Nothing is staged: every image is the live Jenkins or review UI,
// showing the latest analysed build of each scenario.
//
// It only reads pages. The one review decision shown in human-review.png must already
// have been recorded by a person (or by the E2E suite) before this runs.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const assets = join(root, "assets");
const env = Object.fromEntries(
  readFileSync(join(root, ".env"), "utf8").split(/\r?\n/)
    .filter((line) => line.includes("=") && !line.trim().startsWith("#"))
    .map((line) => [line.slice(0, line.indexOf("=")).trim(), line.slice(line.indexOf("=") + 1).trim()]));

const JENKINS = "http://localhost:8080";
const AI = "http://localhost:8001";

export default async function run(page) {
  const builds = await (await fetch(`${AI}/api/analyses`)).json();
  const latest = (scenario) => builds.find((b) => b.scenario === scenario && b.analysis_status === "completed");
  const success = latest("success");
  const failure = latest("test-failure");
  const taken = [];

  // ---- Jenkins (real controller, logged in as the local admin)
  await page.setViewportSize({ width: 1360, height: 820 });
  await page.goto(`${JENKINS}/login`);
  await page.fill('input[name="j_username"]', env.JENKINS_ADMIN_ID);
  await page.fill('input[name="j_password"]', env.JENKINS_ADMIN_PASSWORD);
  await Promise.all([page.waitForNavigation(), page.click('button[type="submit"], input[type="submit"]')]);
  for (const [build, file] of [[success, "pipeline-success.png"], [failure, "pipeline-failure.png"]]) {
    await page.goto(`${JENKINS}/job/devsecops-pipeline/${build.number}/stages/`);
    await page.getByText("Pipeline Summary").first().waitFor();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: join(assets, file) });
    taken.push(`${file}: build #${build.number} (${build.jenkins_result})`);
  }

  // ---- AI analysis and human review (review UI)
  await page.setViewportSize({ width: 1360, height: 900 });
  await page.goto(`${AI}/?build=${failure.build_id}`);
  await page.locator(".rec").first().waitFor();
  const panels = page.locator("#detail > section");
  await panels.nth(1).screenshot({ path: join(assets, "ai-analysis.png") });
  await panels.nth(2).screenshot({ path: join(assets, "human-review.png") });
  taken.push(`ai-analysis.png, human-review.png: ${failure.build_id}`);

  // ---- same page at phone width, to check the layout holds
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  return { taken, mobileHorizontalOverflow: overflow };
}
