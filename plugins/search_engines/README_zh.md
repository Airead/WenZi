# Search Engines

在 WenZi 启动器中使用自定义网页搜索引擎，每个引擎拥有独立前缀。

## 功能

### 内置引擎

| 引擎 | 前缀 | 说明 |
|------|------|------|
| Google | `g` | 通用网页搜索 |
| GitHub | `gh` | 仓库和代码搜索 |
| X | `x` | 搜索帖子和用户 |
| Etherscan | `eth` | 地址、代币和交易查询 |

### 操作

| 按键 | 操作 |
|------|------|
| `Enter` | 在浏览器中打开搜索 |
| `Alt+Enter` | 复制搜索 URL 到剪贴板 |

### 帮助模式

输入前缀但不输入查询内容，可查看引擎名称、描述和可用操作。

### 图标缓存

网站图标会自动下载并本地缓存，有效期 7 天，确保快速显示。

## 配置

编辑插件目录中的 `engines.toml` 文件来添加、删除或修改搜索引擎：

```toml
[[engines]]
id = "google"
name = "Google"
prefix = "g"
url = "https://www.google.com/search?q={query}"
homepage = "https://www.google.com"
subtitle = "General web search"
badge = "G"
icon_url = "https://www.google.com/favicon.ico"
```

### URL 占位符

| 占位符 | 编码方式 |
|--------|----------|
| `{query}` | URL 编码（`urllib.parse.quote`） |
| `{query_plus}` | URL 编码，空格用 `+` 替代（`urllib.parse.quote_plus`） |
| `{raw}` | 无编码 — 原始查询文本 |

### 引擎字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 显示名称 |
| `prefix` | 是 | 启动器激活前缀 |
| `url` | 是 | 搜索 URL 模板，含占位符 |
| `id` | 否 | 标识符（默认为小写名称） |
| `homepage` | 否 | 引擎主页 URL |
| `subtitle` | 否 | 在启动器中显示的描述 |
| `badge` | 否 | 引擎名称旁的短标签文本 |
| `icon_url` | 否 | 图标 URL（未设置则从主页自动生成） |

## 使用方法

1. 打开 WenZi 启动器
2. 输入前缀加搜索内容（如 `g macOS 窗口管理`）
3. 按 Enter 在浏览器中搜索，或按 Alt+Enter 复制 URL

## 要求

- WenZi ≥ 0.1.12
- 需要网络连接
