import { test, expect, type Page } from "@playwright/test";

// All values and assets here are synthetic; never load the user's local ledger.
const now = () => new Date().toISOString();
const baseRecord = { id: "synthetic-buy", action: "buy", occurred_at: "2026-10-20T02:30:00Z", date_only: false, market: "A", symbol: "600999", name: "示例资产甲", currency: "CNY", settlement_currency: "CNY", quantity: "100", price: "10", amount: "1000", fee: "2", note: "合成测试流水" };
const opening = { ...baseRecord, id: "synthetic-opening", action: "opening_position", note: "合成期初基线" };
const positions = [
  { market: "A", symbol: "600999", name: "示例资产甲", currency: "CNY", quantity: "100", cost: "1000", average_cost: "10", price: "10.5", market_value_native: "1050", market_value: "1050", pnl: "50", daily_change: "5", weight: "10.5", price_at: now(), source: "synthetic", estimated: false, stale: false },
  { market: "OTHER", symbol: "DEMO", name: "示例手动资产", currency: "CNY", quantity: "2", cost: "2000", price: "1200", market_value_native: "2400", market_value: "2400", pnl: "400", daily_change: null, weight: "24", price_at: now(), source: "manual", estimated: true },
  { market: "FUND", symbol: "510999", name: "示例场内基金", currency: "CNY", quantity: "100", cost: "100", price: "1.1", market_value_native: "110", market_value: "110", pnl: "10", daily_change: "1", weight: "1.1", price_at: now(), source: "synthetic", estimated: false },
];
const foreignPosition = { ...positions[0], market: "US", symbol: "DEMOUS", name: "示例美元资产", currency: "USD", fx_at: now(), fx_source: "synthetic-fx", fx_stale: false };
const overview = () => ({ total_assets: "10000", market_value: "3560", cash: "6440", cumulative_pnl: "460", cumulative_return: "4.8218", daily_pnl: "51", daily_return: "0.5126", net_invested: "9540", updated_at: now(), complete: true, warnings: [], cash_balances: [{ currency: "CNY", balance: "6440", rate: "1", cny_value: "6440" }], migration: { version: 1, source_hash: "synthetic-hash", inception: "2026-10-01", record_count: 2, estimated: false, warnings: [] }, sessions: [{ market: "A", state: "open" }], history_note: "期初之前的收益可能不完整，请核对历史账单。" });
const defaultPoints = () => Array.from({ length: 4 }, (_, i) => ({ occurred_at: new Date(Date.now() - (4 - i) * 86400000).toISOString(), total_assets: String(9000 + i * 250), cumulative_pnl: String(100 + i * 100), cumulative_return: String(10 + i), daily_return: String(1 + i), time_weighted_return: String(1 + i), compounded_return: String(1 + i), kind: "daily", complete: true }));
const sheets = [{ name: "说明", columns: ["说明"], rows: [{ index: 2, raw: { 说明: "合成测试工作表" } }] }, { name: "流水", columns: ["日期", "动作", "市场", "代码", "数量", "成本价", "金额", "结算费用"], rows: [{ index: 2, raw: { 日期: "2026-10-20", 动作: "买入", 市场: "A", 代码: "600999", 数量: "100", 成本价: "10", 金额: "1000", 结算费用: "2" } }] }];
const previewRows = [
  { index: 2, record: baseRecord, errors: [], suspected_duplicate: false, external_duplicate: false },
  { index: 3, record: { ...baseRecord, id: "other" }, errors: [], suspected_duplicate: true, external_duplicate: false },
  { index: 4, record: null, errors: ["价格必须为有效数字"], suspected_duplicate: false, external_duplicate: false },
  { index: 5, record: baseRecord, errors: [], suspected_duplicate: false, external_duplicate: true },
];
async function fixture(page: Page, overrides: { overview?: object; positions?: object[]; points?: object[] } = {}) {
  const calls: { path: string; method: string; body: any; headers: Record<string, string> }[] = [];
  let records = [{ ...baseRecord }, { ...opening }];
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    let body: any = null;
    if (["POST", "PATCH"].includes(method) && !path.endsWith("/inspect")) body = request.postDataJSON();
    calls.push({ path, method, body, headers: request.headers() });
    let response: any = {};
    if (path === "/api/auth/me") response = { username: "synthetic-tester" };
    else if (path === "/api/jobs") response = [];
    else if (path === "/api/investment/overview") response = overrides.overview || overview();
    else if (path === "/api/investment/positions") response = { items: overrides.positions || positions };
    else if (path === "/api/investment/curve") response = { points: overrides.points || defaultPoints(), trading_date: "2026-11-05" };
    else if (path.endsWith("/revisions")) response = { items: [{ id: "rev1", record_id: "synthetic-buy", occurred_at: "2026-10-21T02:30:00Z", operation: "update", payload: { ...baseRecord, note: "历史修订示例" } }] };
    else if (path === "/api/investment/records" && method === "POST") { response = { ...body, id: "created" }; records = [response, ...records]; }
    else if (path === "/api/investment/records") response = { items: records };
    else if (path.startsWith("/api/investment/records/") && method === "PATCH") { const id = path.split("/").at(-1)!; records = records.map(record => record.id === id ? { ...record, ...body } : record); response = { ...body, id }; }
    else if (path.startsWith("/api/investment/records/") && method === "DELETE") { records = records.map(record => record.id === path.split("/").at(-1) ? { ...record, voided: true } : record); response = { success: true }; }
    else if (path.endsWith("/imports/inspect")) response = { id: "synthetic-file", hash: "synthetic-hash", duplicate_file: true, sheets };
    else if (path.endsWith("/imports/preview")) response = { rows: previewRows, ...(body.mode === "reconcile" ? { validation_only: true, discrepancies: [{ market: "A", symbol: "600999", imported_quantity: "110", ledger_quantity: "100" }] } : {}) };
    else if (path.endsWith("/imports/confirm")) response = { imported: body.indices.length, skipped: 0 };
    else if (path.endsWith("/refresh")) response = { status: "refreshing" };
    else if (path.endsWith("/valuation")) response = { success: true };
    await route.fulfill({ json: response });
  });
  return calls;
}
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true); }
async function upload(page: Page) {
  await page.getByRole("button", { name: "导入账单", exact: true }).click();
  await page.getByLabel("上传投资文件").setInputFiles({ name: "synthetic.csv", mimeType: "text/csv", buffer: Buffer.from("日期,动作,金额\n2026-10-20,入金,1000") });
  await page.getByLabel("工作表", { exact: true }).selectOption("流水");
}

test("synthetic desktop and mobile show three independent charts, percentages and responsive cards", async ({ page }, testInfo) => {
  await fixture(page);
  for (const width of [1440, 768, 390, 360]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/investment");
    await expect(page.getByRole("heading", { name: "投资总览", exact: true })).toBeVisible();
    await expect(page.locator(".breadcrumb strong")).toHaveText("投资总览");
    await expect(page.getByRole("img", { name: /交互曲线/ })).toHaveCount(3);
    const asset = page.getByRole("region", { name: "总资产走势", exact: true });
    const pnl = page.getByRole("region", { name: "累计盈亏走势", exact: true });
    const rate = page.getByRole("region", { name: "组合收益率走势", exact: true });
    await expect(rate.locator(".investment-chart-readout strong")).toHaveText("+4.00%");
    await expect(rate.locator(".investment-chart-readout strong")).toHaveClass("investment-positive");
    await expect(rate.locator('path[stroke-width="2.5"]')).toHaveAttribute("stroke", "#c35761");
    await expect(rate.getByRole("heading", { name: "交易日收益率走势", exact: true })).toBeVisible();
    await expect(page.locator(".investment-kicker")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "资产汇总" }).getByText("本金收益比例", { exact: false })).toContainText("+4.82%");
    await asset.getByRole("button", { name: "近一周", exact: true }).click();
    await expect(asset.getByRole("button", { name: "近一周" })).toHaveAttribute("aria-pressed", "true");
    await expect(pnl.getByRole("button", { name: "近一月" })).toHaveAttribute("aria-pressed", "true");
    await rate.getByRole("img", { name: "组合收益率走势交互曲线", exact: true }).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(rate.locator(".investment-chart-readout strong")).toHaveText("+3.00%");
    await rate.getByRole("button", { name: "本交易日", exact: true }).click();
    await expect(rate.getByRole("button", { name: "本交易日" })).toHaveAttribute("aria-pressed", "true");
    await expect(asset.getByRole("button", { name: "近一周" })).toHaveAttribute("aria-pressed", "true");
    await expect(pnl.getByRole("button", { name: "近一月" })).toHaveAttribute("aria-pressed", "true");
    await pnl.getByRole("img", { name: "累计盈亏走势交互曲线", exact: true }).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(pnl.locator(".investment-chart-readout strong")).toHaveText("¥ +300.00");
    await asset.getByRole("button", { name: "全部", exact: true }).click();
    await expect(pnl.getByRole("button", { name: "近一月" })).toHaveAttribute("aria-pressed", "true");
    if (width < 768) {
      await expect(page.locator(".investment-mobile-holdings")).toBeVisible();
      await expect(page.locator(".investment-mobile-holdings").getByText("+5.00%", { exact: true })).toBeVisible();
      await expect(page.locator(".mobile-nav").getByRole("link", { name: "投资", exact: true })).toBeVisible();
    } else {
      await expect(page.locator(".investment-desktop-table").first()).toBeVisible();
      await expect(page.locator(".investment-desktop-table").getByText("+5.00%", { exact: true })).toBeVisible();
    }
    await noOverflow(page);
    if (width === 1440) {
      await page.getByRole("button", { name: "数据说明", exact: true }).click();
      const notes = page.getByRole("dialog", { name: "数据说明", exact: true });
      await notes.getByText("计算口径与曲线说明", { exact: true }).click();
      await expect(notes.getByText(/本金收益比例＝累计盈亏 ÷ 累计净投入/)).toBeVisible();
      await notes.getByRole("button", { name: "关闭", exact: true }).click();
    }
    if (width === 1440 || width === 390) await page.screenshot({ path: testInfo.outputPath(`investment-synthetic-${width}.png`), fullPage: true });
  }
});

test("return percentages connect valid values and keep losses green", async ({ page }) => {
  const points = defaultPoints().map((point, index) => ({ ...point, compounded_return: ["1", null, "-2.5", "-1.25"][index] }));
  await fixture(page, { points });
  await page.goto("/investment");
  const rate = page.getByRole("region", { name: "组合收益率走势", exact: true });
  await rate.getByRole("button", { name: "近一周", exact: true }).click();
  await expect(rate.locator(".investment-chart-readout strong")).toHaveText("-1.25%");
  await expect(rate.locator(".investment-chart-readout strong")).toHaveClass("investment-negative");
  await expect(rate.locator('path[stroke-width="2.5"]')).toHaveCount(1);
  for (const path of await rate.locator('path[stroke-width="2.5"]').all()) await expect(path).toHaveAttribute("stroke", "#267c68");
  await expect(rate.locator("svg text").filter({ hasText: "%" })).toHaveCount(4);
  await expect(rate.locator(".investment-chart-footer")).not.toContainText("数据有缺口");
  await rate.getByRole("img", { name: "组合收益率走势交互曲线", exact: true }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(rate.locator(".investment-chart-readout strong")).toHaveText("-2.50%");
  await page.keyboard.press("ArrowLeft");
  await expect(rate.locator(".investment-chart-readout strong")).toHaveText("+1.00%");
  await page.route("**/api/investment/curve?period=all", route => route.fulfill({ json: { points: points.map(point => ({ ...point, compounded_return: null })) } }));
  await rate.getByRole("button", { name: "全部", exact: true }).click();
  await expect(rate.getByText("需要完整的期初收益率、资产市值及出入金时估值，才能衔接复利累计收益率。", { exact: true })).toBeVisible();
  await expect(rate.getByRole("img", { name: /交互曲线/ })).toHaveCount(0);
});

test("compounded returns show boundary estimates and missing latest value", async ({ page }) => {
  const points = defaultPoints().map((point, index) => ({ ...point, cumulative_return: "-4", compounded_return: index === 3 ? null : String(index), twr_estimated: true, compounded_warning: index === 3 ? "出入金时缺少资产估值，累计收益无法衔接" : null }));
  await fixture(page, { points });
  await page.goto("/investment");
  const rate = page.getByRole("region", { name: "组合收益率走势", exact: true });
  await rate.getByRole("button", { name: "近一月", exact: true }).click();
  await expect(rate.locator(".investment-chart-readout strong")).toHaveText("+2.00%");
  await expect(rate.locator(".investment-chart-readout")).toContainText("估算");
  await expect(rate.getByText("最新值缺失 · 显示最近有效值", { exact: true })).toBeVisible();
  await expect(rate.getByText("最新值缺失 · 显示最近有效值", { exact: true })).toHaveAttribute("title", "出入金时缺少资产估值，累计收益无法衔接");
});

test("trading day shows gains independently of cumulative losses", async ({ page }) => {
  const points = defaultPoints().map((point, index) => ({ ...point, cumulative_return: "-4", time_weighted_return: "1", compounded_return: "-2", daily_return: ["0", "0.5", "0.8", "1"][index] }));
  await fixture(page, { points, overview: { ...overview(), cumulative_return: "-4", daily_pnl: "100", daily_return: "1" } });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/investment");
    const rate = page.getByRole("region", { name: "组合收益率走势", exact: true });
    await expect(rate.getByRole("heading", { name: "交易日收益率走势", exact: true })).toBeVisible();
    await expect(rate.locator(".investment-chart-readout strong")).toHaveText("+1.00%");
    await expect(rate.locator(".investment-chart-readout strong")).toHaveClass("investment-positive");
    await expect(page.getByRole("region", { name: "资产汇总" }).getByText("交易日收益率", { exact: false })).toContainText("+1.00%");
    await rate.getByRole("button", { name: "近一周", exact: true }).click();
    await expect(rate.getByRole("heading", { name: "复利累计收益率走势", exact: true })).toBeVisible();
    await expect(rate.locator(".investment-chart-readout strong")).toHaveText("-2.00%");
    await expect(rate.locator(".investment-chart-readout strong")).toHaveClass("investment-negative");
    await rate.getByRole("button", { name: "本交易日", exact: true }).click();
    await expect(rate.locator(".investment-chart-readout strong")).toHaveText("+1.00%");
    await noOverflow(page);
  }
});

test("historical ranges start at the real opening loss and compound subsequent returns", async ({ page }) => {
  // Opening capital 1000, assets 900: start -10%; then +10% and -10% compound to -10.9%.
  const points = [
    { occurred_at: "2026-10-21T00:00:00Z", date: "2026-10-20", total_assets: "900", cumulative_pnl: "-100", cumulative_return: "-10", time_weighted_return: "0", compounded_return: "-10", kind: "daily", complete: true },
    { occurred_at: "2026-11-04T00:00:00Z", date: "2026-11-03", total_assets: "990", cumulative_pnl: "-10", cumulative_return: "-1", time_weighted_return: "10", compounded_return: "-1", kind: "daily", complete: true },
    { occurred_at: "2026-11-05T00:00:00Z", date: "2026-11-04", total_assets: "891", cumulative_pnl: "-109", cumulative_return: "-10.9", time_weighted_return: "-1", compounded_return: "-10.9", kind: "daily", complete: true },
  ];
  await fixture(page, { overview: { ...overview(), total_assets: "891", net_invested: "1000", cumulative_pnl: "-109", cumulative_return: "-10.9" } });
  await page.route("**/api/investment/curve?period=*", route => {
    const period = new URL(route.request().url()).searchParams.get("period");
    return route.fulfill({ json: { points: period === "week" ? points.slice(1) : points, trading_date: "2026-11-05", return_note: "自 2026-10-20 的期初收益率 -10.00% 起算" } });
  });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/investment");
    const rate = page.getByRole("region", { name: "组合收益率走势", exact: true });
    await expect(page.getByRole("region", { name: "资产汇总" }).getByText("本金收益比例", { exact: false })).toContainText("-10.90%");
    for (const [label, first] of [["近一周", "-1.00%"], ["近一月", "-10.00%"], ["近三月", "-10.00%"], ["全部", "-10.00%"]]) {
      await rate.getByRole("button", { name: label, exact: true }).click();
      await expect(rate.getByRole("heading", { name: "复利累计收益率走势", exact: true })).toBeVisible();
      await expect(rate.locator(".investment-chart-readout strong")).toHaveText("-10.90%");
      await expect(rate.locator(".investment-chart-readout strong")).toHaveClass("investment-negative");
      await expect(rate.locator("h2")).toHaveAttribute("title", /2026-10-20/);
      const previous = rate.getByRole("button", { name: "组合收益率走势上一个点", exact: true });
      while (await previous.isEnabled()) await previous.click();
      await expect(rate.locator(".investment-chart-readout strong")).toHaveText(first);
      await expect(rate.locator('path[stroke-width="2.5"]')).toHaveCount(1);
      await noOverflow(page);
    }
  }
});

test("all eight record types submit decimal strings, date-only and aware exact times; US and HK settlement", async ({ page }) => {
  const calls = await fixture(page);
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto("/investment");
  for (const action of ["buy", "sell", "deposit", "withdraw", "dividend", "fee", "exchange", "split"]) {
    await page.getByRole("button", { name: "记一笔", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "新增投资记录" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("记录类型", { exact: true }).selectOption(action);
    await expect(dialog.getByLabel("发生日期", { exact: true })).toHaveValue("");
    await dialog.getByLabel("发生日期", { exact: true }).fill("2026-10-20");
    if (["buy", "sell", "dividend", "split"].includes(action)) await dialog.getByLabel("证券 / 资产代码", { exact: true }).fill("600999");
    if (action === "buy" || action === "sell") {
      await dialog.getByLabel("数量", { exact: true }).fill("10");
      await dialog.getByLabel(/成交单价/).fill("10.125");
      if (action === "buy") {
        await dialog.getByLabel("市场", { exact: true }).selectOption("US");
        await expect(dialog.getByLabel("原币币种", { exact: true })).toHaveValue("USD");
        await dialog.getByLabel("填写精确时间", { exact: false }).check();
        await dialog.getByLabel("发生时间", { exact: true }).fill("10:31:22");
      } else {
        await dialog.getByLabel("市场", { exact: true }).selectOption("HK_CONNECT");
        await dialog.getByLabel(/实际结算金额/).fill("91.25");
        await dialog.getByLabel(/实际结算费用/).fill("1.25");
      }
    } else if (action === "split") await dialog.getByLabel(/拆股比例/).fill("2");
    else if (action === "exchange") { await dialog.getByLabel(/转出金额/).fill("700"); await dialog.getByLabel("转入金额", { exact: true }).fill("100"); }
    else await dialog.getByLabel(/^金额/).fill("100.25");
    await noOverflow(page);
    await dialog.getByRole("button", { name: "保存记录", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    const submitted = calls.filter(call => call.method === "POST" && call.path.endsWith("/records")).at(-1)!.body;
    expect(submitted.action).toBe(action);
    expect(submitted.occurred_at).toContain("+08:00");
    expect(submitted.date_only).toBe(action !== "buy");
    expect(typeof submitted.amount).toBe("string");
    if (action === "buy") { expect(submitted.settlement_currency).toBe("USD"); expect(submitted.occurred_at).toContain("10:31:22"); }
    if (action === "sell") { expect(submitted.market).toBe("HK"); expect(submitted.currency).toBe("HKD"); expect(submitted.settlement_currency).toBe("CNY"); expect(submitted.settlement_amount).toBe("91.25"); expect(submitted.settlement_fee).toBe("1.25"); }
  }
});

test("edit, revisions, void and immutable opening baseline", async ({ page }) => {
  const calls = await fixture(page);
  await page.goto("/investment");
  await page.getByRole("button", { name: "投资流水", exact: true }).click();
  await expect(page.getByRole("button", { name: "编辑记录 synthetic-opening", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "作废记录 synthetic-opening", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "编辑记录 synthetic-buy", exact: true }).filter({ visible: true }).click();
  await page.getByLabel("备注", { exact: true }).fill("修改后的合成备注");
  await page.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect.poll(() => calls.find(call => call.method === "PATCH")?.body.note).toBe("修改后的合成备注");
  await page.getByRole("button", { name: "修订历史 synthetic-buy", exact: true }).filter({ visible: true }).click();
  const history = page.getByRole("dialog", { name: "记录修订历史" });
  await expect(history.getByText("历史修订示例", { exact: true })).toBeVisible();
  await expect(history.getByText(/2026\/10\/21/)).toBeVisible();
  await expect(history).not.toContainText("时间未知");
  await history.getByRole("button", { name: "关闭历史" }).click();
  await page.getByRole("button", { name: "作废记录 synthetic-buy", exact: true }).filter({ visible: true }).click();
  await page.getByRole("button", { name: "确认作废", exact: true }).click();
  await expect.poll(() => calls.some(call => call.method === "DELETE")).toBe(true);
  await page.getByLabel("显示已作废").check();
  await expect(page.locator(".investment-desktop-table .investment-voided")).toHaveCount(1);
});

test("import mapping, invalid null rows, duplicates and explicit selected indices", async ({ page }) => {
  const calls = await fixture(page);
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto("/investment");
  await upload(page);
  await expect(page.getByText("这个文件曾经导入过。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("映射记录类型", { exact: true })).toHaveValue("动作");
  await expect(page.getByLabel("映射单价", { exact: true })).toHaveValue("成本价");
  await expect(page.getByLabel("映射实际结算费用", { exact: true })).toHaveValue("结算费用");
  await page.getByRole("button", { name: "生成导入预览", exact: true }).click();
  await expect(page.getByLabel("导入第 2 行", { exact: true })).toBeChecked();
  await expect(page.getByLabel("导入第 3 行", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("导入第 4 行", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("导入第 5 行", { exact: true })).toBeDisabled();
  await expect(page.getByText("价格必须为有效数字", { exact: true })).toBeVisible();
  await page.getByLabel("导入第 3 行", { exact: true }).check();
  await noOverflow(page);
  await page.getByRole("button", { name: "确认导入 2 行", exact: true }).click();
  await expect(page.getByRole("heading", { name: "导入已完成" })).toBeVisible();
  expect(calls.find(call => call.path.endsWith("/inspect"))!.headers["content-type"]).toContain("multipart/form-data; boundary=");
  const preview = calls.find(call => call.path.endsWith("/preview"))!.body;
  expect(preview.sheet).toBe("流水");
  expect(preview.mapping.action).toBe("动作");
  expect(preview.mapping.price).toBe("成本价");
  expect(calls.find(call => call.path.endsWith("/confirm"))!.body.indices).toEqual([2, 3]);
});

test("snapshot requires explicit date and reconcile displays differences without write action", async ({ page }) => {
  const calls = await fixture(page);
  await page.goto("/investment");
  await upload(page);
  await page.getByLabel("导入方式", { exact: true }).selectOption("snapshot");
  await page.getByRole("button", { name: "生成导入预览", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("快照日期");
  await page.getByLabel("快照日期", { exact: true }).fill("2026-10-20");
  await page.getByRole("button", { name: "生成导入预览", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认导入 1 行", exact: true })).toBeVisible();
  await page.getByLabel("导入方式", { exact: true }).selectOption("reconcile");
  await page.getByRole("button", { name: "生成导入预览", exact: true }).click();
  await expect(page.getByRole("heading", { name: "持仓核对结果" })).toBeVisible();
  await expect(page.getByRole("button", { name: /确认导入/ })).toHaveCount(0);
  await expect(page.getByLabel("导入第 2 行", { exact: true })).toBeDisabled();
  await expect(page.getByText("110.0000", { exact: true })).toBeVisible();
  expect(calls.filter(call => call.path.endsWith("/confirm"))).toHaveLength(0);
  const preview = calls.filter(call => call.path.endsWith("/preview")).at(-1)!.body;
  expect(preview.mode).toBe("reconcile"); expect(preview.snapshot_date).toBe("2026-10-20");
});

test("OTHER manual valuation sends only chosen price or total_amount and refresh triggers", async ({ page }) => {
  const calls = await fixture(page);
  await page.setViewportSize({ width: 360, height: 900 });
  await page.goto("/investment");
  await page.getByRole("button", { name: "刷新行情", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "行情刷新已启动" })).toBeVisible();
  await page.getByRole("button", { name: "更新估值", exact: true }).filter({ visible: true }).click();
  await page.getByLabel("估值方式", { exact: true }).selectOption("total");
  await page.getByLabel("资产总金额", { exact: true }).fill("2450.125");
  await page.getByLabel("估值日期", { exact: true }).fill("2026-10-20");
  await page.getByLabel("估值时间（北京时间）", { exact: true }).fill("15:00");
  await noOverflow(page);
  await page.getByRole("button", { name: "保存估值", exact: true }).click();
  await expect.poll(() => calls.find(call => call.path.endsWith("/valuation"))?.body.total_amount).toBe("2450.125");
  const body = calls.find(call => call.path.endsWith("/valuation"))!.body;
  expect(body.price).toBeUndefined(); expect(body.market).toBe("OTHER"); expect(body.occurred_at).toContain("+08:00");
});

test("missing totals and stale sources stay missing while valid curve points connect", async ({ page }) => {
  const point = (offset: number, value: string | null, more = {}) => ({ occurred_at: new Date(Date.now() - offset).toISOString(), total_assets: value, cumulative_pnl: value, kind: "minute", complete: value !== null, ...more });
  const points = [point(700000, null), point(600000, "100"), point(540000, "110"), point(420000, "120", { break_before: true }), point(200000, "130"), point(140000, null), point(80000, "140"), point(-3600000, "999999")];
  await fixture(page, { overview: { ...overview(), total_assets: null, daily_pnl: null, complete: false, warnings: [{ message: "缺少行情，请补齐估值" }] }, positions: [{ ...positions[0], stale: true, price_at: new Date(Date.now() - 600000).toISOString() }], points });
  await page.goto("/investment");
  await expect(page.locator(".investment-total-main > strong")).toHaveText("¥ —");
  await expect(page.getByText("数据待更新", { exact: true })).toBeVisible();
  await expect(page.getByText("资产数据尚不完整", { exact: true })).toBeVisible();
  await expect(page.getByText("缺少行情，请补齐估值", { exact: true }).first()).toBeVisible();
  const chart = page.getByRole("region", { name: "总资产走势", exact: true });
  await expect(chart.locator('path[stroke-width="2.5"]')).toHaveCount(1);
  await expect(chart.locator(".investment-chart-readout")).toContainText("盘中采样");
  await expect(chart.locator(".investment-chart-readout strong")).toHaveText("¥ 140.00");
  await expect(chart.locator("svg[role=img]")).not.toContainText("999999");
  await page.route("**/api/investment/curve?period=all", route => route.fulfill({ json: { points: [point(6 * 86400000, "100", { kind: "daily" }), point(2 * 86400000, "200", { kind: "daily" })] } }));
  await chart.getByRole("button", { name: "全部", exact: true }).click();
  await expect(chart.locator('path[stroke-width="2.5"]')).toHaveCount(1);
});

test("empty curves and holdings show recoverable states; multipart 401 expires session", async ({ page }) => {
  await fixture(page, { points: [], positions: [] });
  await page.goto("/investment");
  await expect(page.getByRole("heading", { name: "还没有持仓", exact: true })).toBeVisible();
  await expect(page.getByText("暂无可用的总资产走势数据", { exact: true })).toBeVisible();
  await page.route("**/api/investment/imports/inspect", route => route.fulfill({ status: 401, json: { detail: "登录已过期" } }));
  await page.getByRole("button", { name: "导入账单", exact: true }).click();
  await page.getByLabel("上传投资文件").setInputFiles({ name: "synthetic.csv", mimeType: "text/csv", buffer: Buffer.from("日期,金额\n2026-10-20,100") });
  await expect(page.getByRole("heading", { name: "欢迎回到创作空间", exact: true })).toBeVisible();
});


test("foreign FX source timestamps and stale rates propagate to summary without exposing provenance", async ({ page }) => {
  const fxAt = new Date(Date.now() - 600000).toISOString();
  await fixture(page, { positions: [{ ...foreignPosition, fx_at: fxAt, fx_stale: true }], overview: { ...overview(), cash_balances: [{ currency: "USD", balance: "100", rate: "7", cny_value: "700", fx_at: fxAt, fx_source: "synthetic-fx", fx_stale: true }], migration: { source_hash: "synthetic-hash", record_count: 2, opening_snapshot_reconciliation: { matched: false, status: "compared", discrepancies: [{ symbol: "DEMO", source: "private-provenance-not-for-display" }] } } } });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto("/investment");
  await expect(page.getByText("数据待更新", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "现金余额" }).locator(".investment-fx-note")).toContainText("synthetic-fx");
  await expect(page.locator(".investment-mobile-holdings .investment-fx-note")).toContainText("汇率待更新");
  await expect(page.getByRole("region", { name: "数据质量与迁移清单" })).toHaveCount(0);
  await page.getByRole("button", { name: "数据说明", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "数据说明", exact: true })).toBeVisible();
  await expect(page.getByText(/期初快照核对：存在 1 项差异/)).toBeVisible();
  await expect(page.getByText("历史账本已迁移", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "数据说明", exact: true })).not.toContainText("private-provenance-not-for-display");
  await noOverflow(page);
  await page.getByRole("dialog", { name: "数据说明", exact: true }).getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.getByRole("region", { name: "数据质量与迁移清单" })).toHaveCount(0);
});

test("record validation errors stay in the modal and retry saves user input", async ({ page }) => {
  await fixture(page);
  let attempts = 0;
  await page.route("**/api/investment/records", async route => {
    if (route.request().method() !== "POST") return route.fallback();
    attempts++;
    return route.fulfill(attempts === 1 ? { status: 422, json: { detail: [{ msg: "合成校验错误，请核对备注" }] } } : { json: { ...route.request().postDataJSON(), id: "retry-created" } });
  });
  await page.goto("/investment");
  await page.getByRole("button", { name: "记一笔", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "新增投资记录" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("记录类型", { exact: true }).selectOption("deposit");
  await dialog.getByLabel("发生日期", { exact: true }).fill("2026-10-20");
  await dialog.getByLabel("金额", { exact: true }).fill("123.4567");
  await dialog.getByLabel("备注", { exact: true }).fill("重试仍保留");
  await dialog.getByRole("button", { name: "保存记录", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("合成校验错误");
  await expect(dialog.getByLabel("金额", { exact: true })).toHaveValue("123.4567");
  await expect(dialog.getByLabel("备注", { exact: true })).toHaveValue("重试仍保留");
  await dialog.getByRole("button", { name: "保存记录", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(attempts).toBe(2);
});

test("daily labels use the portfolio date, latest samples connect, trading-day PNL names its baseline", async ({ page }) => {
  const points = [
    { occurred_at: "2026-10-21T00:00:00Z", date: "2026-10-20", total_assets: "9000", cumulative_pnl: "100", time_weighted_return: "0", compounded_return: "-10", kind: "daily", complete: true },
    { occurred_at: "2026-10-22T00:00:00Z", date: "2026-10-21", total_assets: "9250", cumulative_pnl: "350", time_weighted_return: "2.5", kind: "daily", complete: true },
    { occurred_at: "2026-10-22T03:00:00Z", total_assets: "10000", cumulative_pnl: "460", time_weighted_return: "3", kind: "latest_sample", complete: true, break_before: true },
  ];
  await fixture(page, { points, overview: { ...overview(), daily_date: "2026-11-05", daily_previous_date: "2026-11-04", daily_baseline_at: "2026-11-05T00:00:00Z", daily_baseline_assets: "9949", daily_net_external: "0" } });
  await page.goto("/investment");
  await expect(page.getByRole("region", { name: "资产汇总" }).getByText("2026/11/05 交易日", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "资产汇总" }).getByText("2026/11/05 交易日", { exact: true })).toHaveAttribute("title", /2026-11-04 日终总资产（¥ 9,949.00）/);
  const chart = page.getByRole("region", { name: "总资产走势", exact: true });
  await expect(chart.locator(".investment-chart-readout strong")).toHaveText("¥ 10,000.00");
  await expect(chart.locator(".investment-chart-readout span")).toContainText("最近采样");
  await expect(chart.locator('path[stroke-width="2.5"]')).toHaveCount(1);
  await expect(chart.locator("svg text").filter({ hasText: "2026/10/20" })).toHaveCount(1);
  await chart.getByRole("button", { name: "总资产走势上一个点", exact: true }).click();
  await expect(chart.locator(".investment-chart-readout span")).toHaveText("2026/10/21 · 日终");
  await expect(chart.locator(".investment-chart-readout span")).toHaveAttribute("title", "估值截止：2026/10/22 08:00");
});

test("closed markets show source time without claiming a fresh quote", async ({ page }) => {
  await fixture(page, { overview: { ...overview(), updated_at: new Date(Date.now() - 3600000).toISOString(), sessions: [{ market: "A", state: "closed" }, { market: "US", state: "closed" }] } });
  await page.goto("/investment");
  await expect(page.getByText("休市 · 最近行情", { exact: true })).toBeVisible();
  await expect(page.getByText("资产数据已更新", { exact: true })).toHaveCount(0);
});


test("trading-day market contributions stay visible on desktop and mobile", async ({ page }) => {
  await fixture(page, { overview: { ...overview(), daily_date: "2026-11-05", daily_breakdown: [
    { market: "A", pnl: "24446.6" }, { market: "HK", pnl: "97506.89" },
    { market: "US", pnl: "-22951.9" }, { market: "OTHER", pnl: "-2750.14" },
  ] } });
  for (const width of [1440, 390, 360]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/investment");
    await expect(page.getByText("交易日盈亏", { exact: true })).toBeVisible();
    const breakdown = page.getByRole("region", { name: "交易日盈亏明细" });
    await expect(breakdown.getByText("¥ -22,951.90", { exact: true })).toBeVisible();
    await expect(breakdown.getByText("¥ -22,951.90", { exact: true })).toHaveClass("investment-negative");
    const chart = page.getByRole("region", { name: "总资产走势", exact: true });
    await chart.getByRole("button", { name: "本交易日", exact: true }).click();
    await expect(chart.getByText("2026/11/05 交易日", { exact: true })).toBeVisible();
    await noOverflow(page);
  }
});

test("intraday curves collapse missing time and connect valid samples with real timestamps", async ({ page }) => {
  await fixture(page);
  const point = (time: string, value: string | null, window: number, more = {}) => ({ occurred_at: `2026-11-05T${time}Z`, total_assets: value, cumulative_pnl: value, cumulative_return: value, time_weighted_return: value, kind: "minute", complete: value !== null, trading_window: window, ...more });
  await page.route("**/api/investment/curve?period=today", route => route.fulfill({ json: {
    trading_date: "2026-11-05", trading_windows: [
      { start: "2026-11-05T01:30:00Z", end: "2026-11-05T04:00:00Z" },
      { start: "2026-11-05T05:00:00Z", end: "2026-11-05T08:00:00Z" },
      { start: "2026-11-05T14:30:00Z", end: "2026-11-05T21:00:00Z" },
    ], points: [point("14:30:00", "100", 2), point("14:31:00", "101", 2), point("14:48:00", null, 2), point("21:00:02", "90", 2, { kind: "close", chart_at: "2026-11-05T21:00:00Z", break_before: true })],
  } }));
  for (const width of [1440, 677, 390]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/investment");
    const chart = page.getByRole("region", { name: "总资产走势", exact: true });
    await chart.getByRole("button", { name: "本交易日", exact: true }).click();
    await expect(chart.locator(".investment-chart-readout")).toContainText("收盘估值");
    await expect(chart.locator("svg text").first()).toBeVisible();
    await expect(chart.locator("svg text").last()).toContainText("2026/11/06 05:00");
    const circle = chart.locator('circle[r="2"]').first();
    expect(Number(await circle.getAttribute("cx"))).toBeCloseTo(68, 2);
    await expect(chart.locator('path[stroke-width="2.5"]')).toHaveCount(1);
    await expect(chart.locator("svg circle")).toHaveCount(4);
    const circles = chart.locator('circle[r="2"]');
    expect(Number(await circles.nth(1).getAttribute("cx"))).toBeCloseTo(370, 2);
    expect(Number(await circles.nth(2).getAttribute("cx"))).toBeCloseTo(672, 2);
    await chart.getByRole("img", { name: "总资产走势交互曲线" }).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(chart.locator(".investment-chart-readout span")).toContainText("2026/11/05 22:31");
    await expect(chart.locator(".investment-chart-readout strong")).toHaveText("¥ 101.00");
    await noOverflow(page);
  }
});


test("homepage and login land on investment, return chart defaults to the trading day", async ({ page }) => {
  await fixture(page);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/");
    await expect(page).toHaveURL(/\/investment$/);
    const rate = page.getByRole("region", { name: "组合收益率走势", exact: true });
    await expect(rate.getByRole("button", { name: "本交易日", exact: true })).toHaveAttribute("aria-pressed", "true");
    const asset = page.getByRole("region", { name: "总资产走势", exact: true });
    await expect(asset.getByRole("button", { name: "近一月", exact: true })).toHaveAttribute("aria-pressed", "true");
    const creation = width === 1440 ? page.getByRole("link", { name: "快速创作", exact: true }) : page.locator(".mobile-nav").getByRole("link", { name: "创作", exact: true });
    await expect(creation).toHaveAttribute("href", "/studio");
    await noOverflow(page);
  }
  let loggedIn = false;
  await page.route("**/api/auth/me", route => route.fulfill({ status: loggedIn ? 200 : 401, json: loggedIn ? { username: "synthetic-tester" } : { detail: "请登录" } }));
  await page.route("**/api/auth/login", route => { loggedIn = true; return route.fulfill({ json: { success: true } }); });
  await page.goto("/");
  await page.getByPlaceholder("输入你的账号").fill("synthetic-tester");
  await page.getByPlaceholder("输入你的密码").fill("synthetic-password");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page).toHaveURL(/\/investment$/);
  await expect(page.getByRole("heading", { name: "投资总览", exact: true })).toBeVisible();
});


test("records start collapsed, preserve filters and can add records while collapsed", async ({ page }) => {
  await fixture(page);
  for (const width of [1440, 677, 390]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/investment");
    await expect(page.getByRole("heading", { name: "投资总览", exact: true })).toBeVisible();
    await expect(page.locator('.investment-heading h1 > span')).toHaveCount(0);
    const records = page.getByRole("region", { name: "投资流水", exact: true });
    const toggle = records.getByRole("button", { name: "投资流水", exact: true });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(records.getByLabel("筛选记录类型", { exact: true })).not.toBeVisible();
    await records.getByRole("button", { name: "新增记录", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "新增投资记录", exact: true })).toBeVisible();
    await page.getByRole("dialog", { name: "新增投资记录", exact: true }).getByRole("button", { name: "取消", exact: true }).click();
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await records.getByLabel("筛选记录类型", { exact: true }).selectOption("buy");
    await expect(records.getByLabel("筛选记录类型", { exact: true })).toBeVisible();
    await toggle.click();
    await expect(records.getByLabel("筛选记录类型", { exact: true })).not.toBeVisible();
    await toggle.click();
    await expect(records.getByLabel("筛选记录类型", { exact: true })).toHaveValue("buy");
    await expect(records.getByRole("button", { name: "编辑记录 synthetic-buy", exact: true }).filter({ visible: true })).toBeVisible();
    await noOverflow(page);
  }
});


test("delayed HK trading points remain visible with an explicit delay note", async ({ page }) => {
  const points = defaultPoints().map(point => ({ ...point, kind: "minute", quote_delayed: true, quote_delay_seconds: 900, daily_return_estimated: true }));
  await fixture(page, { points });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/investment");
    const rate = page.getByRole("region", { name: "组合收益率走势", exact: true });
    await expect(rate.getByRole("img", { name: /交互曲线/ })).toBeVisible();
    await expect(rate.locator(".investment-chart-readout")).toContainText("港股行情约延迟 15 分钟");
    await expect(rate.locator(".investment-chart-readout")).toContainText("估算");
    await noOverflow(page);
  }
});
