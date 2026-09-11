# Personal Hub 嵌入页 Invalid base URL

状态：代码已修复，等待生产部署与浏览器验证。

## 现象

从 Personal Hub 打开“行迹”后，总览显示“加载失败”，toast 报：

`Failed to construct 'URL': Invalid base URL`

同时后端 devlog 没有对应异常，因为错误发生在浏览器端、请求尚未发出。

## 根因

Hub 原先先 `fetch()` 内部页面，再通过 `iframe.srcdoc` 注入 HTML。`srcdoc` 文档的 `location.origin` 可能为 `null`，而 `app.js` 中会使用 `new URL(path, location.origin)` 构造 API URL，因此在浏览器端直接抛出 TypeError。

此外原有安全头 `X-Frame-Options: DENY` 使得内部页面不能直接改为同源 iframe，因此过去才使用了 `srcdoc` 绕开 framing。

## 当前修复

- Hub 内部页面改为真实同源 iframe URL，不再使用 `srcdoc`。
- recruitment 入口层将 `X-Frame-Options` 覆盖为 `SAMEORIGIN`，仍禁止第三方 framing，但允许 Hub 嵌入本站页面。
- 增加 `/api/v1/debug/client-error`，浏览器异常可写入内存 devlog，模块为 `frontend`。
- `ui.js` 捕获 `window.error`、`unhandledrejection`，并把明显的失败 toast 上报。
- `/devlog` 增加“前端”模块筛选并高亮浏览器异常。
- 更新静态资源版本，避免继续命中旧 Hub / UI 脚本缓存。

## 待验证

部署后需要确认：

1. `/hub#activity` 能正常加载总览，不再出现 Invalid base URL。
2. Hub 内切换总览 / 日报 / 趋势 / 秋招仍保持在总控外壳内。
3. 人为触发一个前端异常后，`/devlog` → “前端” → “仅异常 / 警告”可看到结构化错误。
4. 外部站点仍无法 iframe 本站页面（SAMEORIGIN 生效）。

生产验证完成前不要写入 `bughistory/`。
