import { test, expect, Page } from "@playwright/test";
const cover = (id: string) => `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='108' height='192'%3E%3Ctext%3E${id}%3C/text%3E%3C/svg%3E`;
const resources = [
  {
    id: "tpl1",
    kind: "template",
    name: "image_default",
    size: 100,
    preview_url: cover("tpl1"),
    metadata: {
      template_path: "1080x1920/image_default.html",
      template_type: "image",
      orientation: "portrait",
      width: 1080,
      height: 1920,
      parameters: { accent: { type: "color", default: "#8877bb", label: "主题色" } },
    },
  },
  {
    id: "tpl2",
    kind: "template",
    name: "static_default",
    size: 100,
    preview_url: cover("tpl2"),
    metadata: {
      template_path: "1080x1920/static_default.html",
      template_type: "static",
      orientation: "portrait",
      width: 1080,
      height: 1920,
    },
  },
];
const models = {
  images: [
    { key: "api/dashscope/test", name: "清晰插画", provider: "dashscope" },
  ],
  videos: [],
  llm_configured: true,
  tts_api_configured: false,
};
const work = {
  id: "example1",
  task_id: "example1",
  kind: "video",
  title: "把生活里的小确幸，讲成一个故事",
  status: "completed",
  created_at: "2026-11-05T00:00:00Z",
  duration: 28,
  n_frames: 3,
};
const config = {
  frame_template: "1080x1920/image_default.html",
  media_workflow: "api/dashscope/test",
  tts_inference_mode: "local",
  voice_id: "zh-CN-XiaoxiaoNeural",
  tts_speed: 1,
};
const detail = {
  metadata: {
    status: "completed",
    input: { text: "生活中的小确幸", title: work.title, template_id: "tpl1" },
  },
  storyboard: {
    title: work.title,
    config,
    frames: [0, 1, 2].map((i) => ({
      index: i,
      narration: [
        "窗外的阳光，悄悄落在杯沿。",
        "慢一点，发现生活的柔软。",
        "平凡的一天，也有值得珍藏的光。",
      ][i],
      image_prompt: "温暖阳光中的生活插画",
      duration: 9,
    })),
  },
};
async function fixture(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/auth/me") body = { username: "creator" };
    else if (path === "/api/resources") body = resources;
    else if (path === "/api/models") body = models;
    else if (path === "/api/voices")
      body = [{ id: "zh-CN-XiaoxiaoNeural", name: "晓晓 · 温柔女声" }];
    else if (path === "/api/jobs") body = [];
    else if (path === "/api/works")
      body = { tasks: [work], total: 1, page: 1, total_pages: 1 };
    else if (path === "/api/works/example1") body = detail;
    else if (path === "/api/settings")
      body = {
        project_name: "Pixelle",
        llm: {
          api_key: "••••••••",
          base_url: "https://provider.test",
          model: "test",
        },
        api_providers: { common: {}, dashscope: { api_key: "••••••••" } },
        tts_models: { providers: { minimax: {} } },
      };
    else if (path === "/api/preview/script")
      body = {
        title: "生活里的小确幸",
        narrations: ["第一段旁白", "第二段旁白", "第三段旁白"],
      };
    else if (path === "/api/preview/template")
      body = { url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='108' height='192'%3E%3Crect width='108' height='192' fill='%23ddd5ef'/%3E%3C/svg%3E" };
    else if (path.endsWith("/edit"))
      body = { id: "edit1", status: "pending", work_id: "example1" };
    else if (path === "/api/video/generate")
      body = {
        id: "job1",
        status: "pending",
        progress: 0,
        params: {},
        work_id: "example1",
      };
    else if (path === "/api/jobs/job1")
      body = {
        id: "job1",
        status: "pending",
        progress: 0,
        params: {},
        work_id: "example1",
      };
    await route.fulfill({ json: body });
  });
}
async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
}

test("new drafts use configured speech defaults and keep remembered choices", async ({ page }) => {
  await fixture(page);
  await page.route("**/api/models", route => route.fulfill({ json: { ...models, tts_api_configured: true } }));
  await page.route("**/api/settings", route => route.fulfill({ json: {
    tts: { inference_mode: "api", local: { voice: "zh-CN-YunxiNeural", speed: 1.1 } },
    tts_models: { providers: { minimax: { default_voice_id: "warm-voice", default_model: "speech-test" } } },
  } }));
  await page.route("**/api/voices?mode=api", route => route.fulfill({ json: [{ id: "warm-voice", name: "温暖配音" }] }));
  await page.goto("/studio");
  await expect(page.locator(".compact-select")).toHaveValue("api");
  await expect(page.getByRole("combobox", { name: "配音音色" })).toHaveValue("warm-voice");
  await page.locator(".compact-select").selectOption("local");
  await page.reload();
  await expect(page.locator(".compact-select")).toHaveValue("local");
});

test("新草稿带出火柴人前缀，提交创作并保留用户清空选择", async ({ page }) => {
  await fixture(page);
  const prefix = "Minimalist black and white sketch, expressive matchstick figure";
  await page.route("**/api/settings", route => route.fulfill({ json: {
    media: { image: { prompt_prefix: prefix } },
  } }));
  let submitted: any;
  await page.route("**/api/video/generate", route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: { id: "job1", status: "pending", work_id: "example1" } });
  });
  await page.goto("/studio");
  await page.getByText("更多创作选项", { exact: true }).click();
  const field = page.getByRole("textbox", { name: "画面风格描述 Prompt 前缀", exact: true });
  await expect(field).toHaveValue(prefix);
  await page.locator("textarea.idea-editor").fill("生活中的小确幸");
  await page.getByRole("button", { name: "生成视频", exact: true }).click();
  await expect.poll(() => submitted?.prompt_prefix).toBe(prefix);
  await field.fill("水彩画风格");
  await page.reload();
  await page.getByText("更多创作选项", { exact: true }).click();
  await expect(field).toHaveValue("水彩画风格");
  await field.fill("");
  await page.reload();
  await page.getByText("更多创作选项", { exact: true }).click();
  await expect(field).toHaveValue("");
  await page.getByRole("button", { name: "使用系统默认风格", exact: true }).click();
  await expect(field).toHaveValue(prefix);
  await noOverflow(page);
});

test("设置晚到时保留已编辑的风格，并为旧草稿补齐缺失前缀", async ({ page }) => {
  await fixture(page);
  const prefix = "expressive matchstick figure";
  let release: (() => void) | undefined;
  await page.route("**/api/settings", async route => {
    await new Promise<void>(resolve => { release = resolve; });
    await route.fulfill({ json: { media: { image: { prompt_prefix: prefix } } } });
  });
  await page.goto("/studio");
  await page.getByText("更多创作选项", { exact: true }).click();
  const field = page.getByRole("textbox", { name: "画面风格描述 Prompt 前缀", exact: true });
  await field.fill("自己的画面风格");
  await expect.poll(() => !!release).toBe(true);
  release!();
  await expect(page.getByRole("button", { name: "使用系统默认风格", exact: true })).toBeEnabled();
  await expect(field).toHaveValue("自己的画面风格");
  await page.route("**/api/settings", route => route.fulfill({ json: {
    media: { image: { prompt_prefix: prefix } },
  } }));
  await page.evaluate(() => localStorage.setItem("pixelle-draft", JSON.stringify({ text: "保留旧草稿" })));
  await page.reload();
  await page.getByText("更多创作选项", { exact: true }).click();
  await expect(field).toHaveValue(prefix);
  await expect(page.locator("textarea.idea-editor")).toHaveValue("保留旧草稿");
});

test("创作页文字清晰，桌面与手机首屏都能找到生成入口", async ({ page }) => {
  await fixture(page);
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/studio");
    await expect(page.getByRole("heading", { name: "创作内容", exact: true })).toBeVisible();
    const action = page.getByRole("button", { name: "生成视频", exact: true });
    const bounds = await action.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.y + bounds!.height).toBeLessThan(900);
    const textStyle = await page.locator("textarea.idea-editor").evaluate(element => {
      const style = getComputedStyle(element, "::placeholder");
      const rgb = style.color.match(/\d+/g)!.slice(0, 3).map(Number);
      const linear = rgb.map(n => n / 255).map(n => n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4);
      const luminance = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
      return { fontSize: parseFloat(style.fontSize), contrast: 1.05 / (luminance + 0.05) };
    });
    expect(textStyle.fontSize).toBeGreaterThanOrEqual(16);
    expect(textStyle.contrast).toBeGreaterThanOrEqual(4.5);
    await page.locator('textarea.idea-editor').fill("我的创作主题");
    await expect(action).toBeEnabled();
    await page.locator(".sound-grid").scrollIntoViewIfNeeded();
    const scrolledBounds = await action.boundingBox();
    expect(scrolledBounds!.y + scrolledBounds!.height).toBeLessThan(900);
    await noOverflow(page);
  }
});

test("固定模板直接显示效果图，个人修改才渲染", async ({ page }) => {
  await fixture(page);
  const requests: any[] = [];
  let finishUpdate: (() => void) | undefined;
  await page.route("**/api/preview/template", async route => {
    const body = route.request().postDataJSON();
    requests.push(body);
    if (requests.length === 1) await new Promise<void>(resolve => { finishUpdate = resolve; });
    await route.fulfill({ json: { url: `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='108' height='192'%3E%3Ctext%3E${encodeURIComponent(body.title)}%3C/text%3E%3C/svg%3E` } });
  });
  await page.goto("/studio");
  const image = page.getByAltText("当前模板真实渲染预览");
  await expect(image).toBeVisible();
  await page.waitForTimeout(650);
  expect(requests).toHaveLength(0);
  await expect(page.getByRole("button", { name: "预览效果", exact: true })).toHaveCount(0);
  const original = await image.getAttribute("src");
  const title = page.getByRole("textbox", { name: "作品标题 选填", exact: true });
  await title.fill("第一版");
  await title.fill("第二版");
  await title.fill("最终标题");
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].title).toBe("最终标题");
  await expect(image).toHaveAttribute("src", original!);
  await expect(page.locator(".preview-stage")).toHaveAttribute("aria-busy", "true");
  finishUpdate!();
  await expect(image).not.toHaveAttribute("src", original!);
  await expect(page.locator(".preview-stage")).toHaveAttribute("aria-busy", "false");
  await page.locator(".compact-select").selectOption("local");
  await page.waitForTimeout(650);
  expect(requests).toHaveLength(1);
  await page.getByText("更多创作选项", { exact: true }).click();
  await page.getByLabel("主题色", { exact: true }).fill("#334455");
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1].template_params.accent).toBe("#334455");
  await page.getByRole("button", { name: "使用文案", exact: true }).click();
  await page.locator("textarea.idea-editor").fill("第一段文案。\n\n第二段文案。");
  await expect.poll(() => requests.length).toBe(3);
  expect(requests[2].text).toBe("第一段文案。");
});

test("手机风格列表和大预览显示真实效果，个人预览失败可重试", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  const requests: any[] = [];
  await page.route("**/api/preview/template", route => {
    const body = route.request().postDataJSON();
    requests.push(body);
    return route.fulfill(requests.length === 1
      ? { status: 503, json: { detail: "预览暂时不可用" } }
      : { json: { url: `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='108' height='192'%3E%3Ctext%3E${body.template_id}%3C/text%3E%3C/svg%3E` } });
  });
  await page.goto("/studio");
  const preview = page.getByAltText("当前模板真实渲染预览");
  await expect(page.locator(".creation-column img")).toHaveCount(0);
  await page.getByRole("button", { name: "更换风格", exact: true }).click();
  await expect(page.locator(".template-option img")).toHaveCount(2);
  await expect(page.locator(".template-option").first().locator("img")).toHaveAttribute("src", /tpl1/);
  await expect(page.locator(".template-option").nth(1).locator("img")).toHaveAttribute("src", /tpl2/);
  await page.locator(".template-option").nth(1).click();
  await expect(page.locator(".creation-column")).toBeVisible();
  await page.getByRole("button", { name: "查看预览", exact: true }).click();
  await expect(preview).toBeVisible();
  await expect(preview).toHaveAttribute("src", /tpl2/);
  await page.waitForTimeout(650);
  expect(requests).toHaveLength(0);
  await page.reload();
  await expect(preview).toHaveAttribute("src", /tpl2/);
  await page.getByRole("button", { name: "创作内容", exact: true }).click();
  await page.waitForTimeout(650);
  expect(requests).toHaveLength(0);
  await page.getByRole("textbox", { name: "作品标题 选填", exact: true }).fill("我的标题");
  await expect(page.getByRole("alert").filter({ visible: true })).toContainText("预览暂时不可用");
  expect(requests[0].template_id).toBe("tpl2");
  await page.getByRole("button", { name: "重新加载预览", exact: true }).filter({ visible: true }).click();
  await expect(preview).toHaveAttribute("src", /tpl2/);
  await expect(page.getByRole("alert").filter({ visible: true })).toHaveCount(0);
  await page.getByRole("textbox", { name: "作品标题 选填", exact: true }).fill("");
  await page.getByRole("button", { name: "更换风格", exact: true }).click();
  await page.locator(".template-option").first().click();
  await expect(preview).toHaveAttribute("src", /tpl1/);
  expect(requests).toHaveLength(2);
  await noOverflow(page);
});
for (const width of [360, 390, 768, 1440]) {
  test(`创作、作品库和分镜编辑 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 960 });
    await fixture(page);
    await page.goto("/studio");
    await expect(page.getByRole("heading", { name: /让想法/ })).toBeVisible();
    await noOverflow(page);
    await page.screenshot({
      path: `test-results/studio-${width}.png`,
      fullPage: true,
    });
    await page.locator("textarea.idea-editor").fill("生活中的小确幸");
    await page.getByRole("button", { name: "预览文案", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("dialog")).toBeVisible();
    await noOverflow(page);
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    await page.getByRole("button", { name: "更换风格", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await noOverflow(page);
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    await page.goto("/works");
    await expect(page.getByText(work.title)).toBeVisible();
    await noOverflow(page);
    await page.screenshot({
      path: `test-results/library-${width}.png`,
      fullPage: true,
    });
    await page.getByText(work.title).click();
    await expect(page.getByRole("heading", { name: work.title })).toBeVisible();
    await noOverflow(page);
    await page.getByRole("button", { name: "标题", exact: true }).click();
    await page.getByLabel("作品标题").fill("我的新标题");
    await page.getByRole("button", { name: "保存并更新作品" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    if (width <= 1100) {
      await page.getByRole("button", { name: "编辑这个分镜" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
    }
    await page
      .getByRole("button", { name: "修改文案与配音" })
      .filter({ visible: true })
      .click();
    await expect(page.getByLabel("旁白文案")).toHaveValue(
      detail.storyboard.frames[0].narration,
    );
    await noOverflow(page);
    await page.screenshot({
      path: `test-results/edit-${width}.png`,
      fullPage: true,
    });
  });
}
test("设置中的密钥聚焦不清空", async ({ page }) => {
  await fixture(page);
  await page.goto("/studio");
  await page
    .getByRole("button", { name: "创作设置", exact: true })
    .filter({ visible: true })
    .click();
  const key = page.getByLabel("API Key", { exact: true }).first();
  await key.focus();
  await expect(key).toHaveValue("••••••••");
  await noOverflow(page);
});
test("登录页面与会话过期", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({ status: 401, json: { detail: "请登录" } }),
  );
  await page.goto("/studio");
  await expect(
    page.getByRole("heading", { name: "欢迎回到创作空间" }),
  ).toBeVisible();
  await noOverflow(page);
  await page.screenshot({
    path: "test-results/login-mobile.png",
    fullPage: true,
  });
});
