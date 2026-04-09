# 脚本系统

闻字 内置了一个基于 Python 的脚本系统，可以用来自动化 macOS 常见操作——通过 Leader 键启动应用、绑定全局快捷键、显示提示、操作剪贴板等。

## 快速开始

1. **启用脚本系统**：在 设置 → 通用 → Scripting 中打开开关，或者直接编辑 `config.json`：

   ```json
   {
     "scripting": {
       "enabled": true
     }
   }
   ```

2. **创建脚本文件** `~/.config/WenZi/scripts/init.py`：

   ```python
   wz.leader("cmd_r", [
       {"key": "w", "app": "WeChat"},
       {"key": "s", "app": "Slack"},
       {"key": "t", "app": "iTerm"},
   ])
   ```

3. **重启闻字**。按住右 Command 键，屏幕上会显示快捷键面板，再按字母键即可启动对应应用。

## Leader 键

Leader 键的使用方式：按住一个触发键（如右 Command），屏幕上会浮现可用映射列表，然后按第二个键执行对应操作。松开触发键后面板自动消失。

```python
wz.leader("cmd_r", [
    {"key": "w", "app": "WeChat"},
    {"key": "f", "app": "Safari"},
    {"key": "g", "app": "/Users/me/Applications/Google Chrome.app"},
    {"key": "i", "exec": "/usr/local/bin/code ~/work/projects", "desc": "projects"},
    {"key": "d", "desc": "日期", "func": lambda: (
        wz.pasteboard.set(wz.date("%Y-%m-%d")),
        wz.notify("日期已复制", wz.date("%Y-%m-%d")),
    )},
    {"key": "r", "desc": "重载脚本", "func": lambda: wz.reload()},
])
```

### 触发键

任何修饰键都可以作为触发键，可用名称如下：

| 按键 | 名称 |
|------|------|
| 右 Command | `cmd_r` |
| 右 Alt/Option | `alt_r` |
| 右 Shift | `shift_r` |
| 右 Control | `ctrl_r` |
| 左 Command | `cmd` |
| 左 Alt/Option | `alt` |
| 左 Shift | `shift` |
| 左 Control | `ctrl` |

可以用不同的触发键注册多组 Leader：

```python
wz.leader("cmd_r", [...])   # 右 Command 启动应用
wz.leader("alt_r", [...])   # 右 Alt 执行工具操作
```

### 映射动作

每个映射字典需要 `"key"` 字段加一个动作：

| 字段 | 类型 | 说明 |
|------|------|------|
| `key` | `str` | 子键名称（如 `"w"`、`"1"`、`"f"`） |
| `app` | `str` | 应用名称或 `.app` 完整路径，启动/聚焦该应用 |
| `func` | `callable` | 要调用的 Python 函数 |
| `exec` | `str` | 要执行的 Shell 命令 |
| `desc` | `str` | 可选描述，显示在浮窗面板中 |

如果省略 `desc`，面板会显示应用名称或命令。

## 启动器

启动器是一个键盘驱动的搜索面板（类似 Alfred 或 Raycast），可以快速查找并打开应用、文件、书签、剪贴板历史和代码片段。它内置于脚本系统中，通过可配置的快捷键激活。

### 激活方式

- **默认快捷键：** `Cmd+Space`（可通过配置 `scripting.chooser.hotkey` 修改）
- **数据源专用快捷键：** 每个数据源可以绑定独立的快捷键（见下方配置）
- **脚本 API：** `wz.chooser.show()` / `wz.chooser.toggle()`

### 搜索模式

启动器支持两种搜索模式：

- **全局搜索：** 直接输入关键词，搜索所有无前缀数据源（如应用）。结果按优先级和模糊匹配得分排序。
- **前缀搜索：** 输入前缀加空格激活特定数据源。例如 `f readme` 搜索文件名包含 "readme" 的文件。

### 内置数据源

| 数据源 | 前缀 | 说明 |
|--------|------|------|
| **应用** | *（无）* | 搜索已安装的应用。全局搜索时始终参与。 |
| **计算器** | *（无）* | 数学运算和单位转换。全局搜索时始终参与。 |
| **命令** | `>` | 命令面板——脚本在此注册命名命令。 |
| **文件** | `f` | 通过 macOS Spotlight 按文件名搜索。 |
| **文件夹** | `fd` | 通过 macOS Spotlight 搜索文件夹。 |
| **剪贴板** | `cb` | 浏览剪贴板历史（文本和图片）。 |
| **代码片段** | `sn` | 搜索文本片段，支持关键词自动展开。 |
| **书签** | `bm` | 搜索浏览器书签（Chrome、Safari、Arc、Edge、Brave、Firefox）。 |
| **系统设置** | *（无）* | 搜索 macOS 系统设置面板和子项。始终在全局搜索中显示。 |

前缀可通过配置 `scripting.chooser.prefixes` 修改。`>` 前缀保留给命令面板，不可更改。

### 键盘快捷键

| 快捷键 | 操作 |
|--------|------|
| `↑` `↓` | 上下导航 |
| `Enter` | 打开/执行选中项 |
| `⌘+Enter` | 在 Finder 中显示（适用于文件类项目） |
| `⌘1` – `⌘9` | 按位置快速选择 |
| `Tab` | 自动补全（如在 `>` 模式下补全命令名） |
| `Esc` | 关闭启动器 |
| `Alt` / `Ctrl` / `Shift`（按住） | 显示选中项的替代操作 |

### 自定义数据源

可以通过 `@wz.chooser.source` 装饰器注册自定义数据源：

```python
@wz.chooser.source("todos", prefix="td", priority=5)
def search_todos(query):
    return [
        {"title": "修复 bug #123", "subtitle": "后端", "action": lambda: ...},
        {"title": "写文档", "subtitle": "前端", "action": lambda: ...},
    ]
```

### 命令

脚本可以注册命名命令，这些命令会出现在命令面板中（在启动器中输入 `> ` 激活）。命令支持参数传递、修饰键和 Tab 补全。

```python
# 装饰器方式
@wz.chooser.command("greet", title="Greet", subtitle="Say hello")
def greet(args):
    name = args.strip() or "World"
    wz.notify("Hello", f"Hello, {name}!")

# 直接注册
wz.chooser.register_command(
    name="open-url",
    title="Open URL",
    subtitle="Open a URL in browser",
    action=lambda args: wz.execute(f"open {args.strip()}"),
)
```

**参数传递：** 输入完整命令名后加空格即可进入参数模式。例如 `> greet Alice` 会将 `"Alice"` 作为 `args` 参数传递。Tab 补全命令名后也会自动进入参数模式。

**修饰键：** 命令可以为修饰键定义替代动作：

```python
wz.chooser.register_command(
    name="deploy",
    title="Deploy",
    action=lambda args: deploy(args),
    modifiers={
        "alt": {"subtitle": "Force deploy", "action": lambda args: force_deploy(args)},
    },
)
```

**推广命令（promoted）：** 默认情况下，命令仅在 `>` 前缀激活时显示。设置 `promoted=True` 可使其同时出现在主搜索中（与应用等并列）：

```python
@wz.chooser.command("reload", title="Reload Scripts", promoted=True)
def reload(args):
    wz.reload()
```

**内置 help 命令：** 系统始终提供一个推广的 `help` 命令。在启动器中输入 "help" 并按回车，即可查看所有可用的前缀及其说明。

### 使用学习

启用后（默认开启），启动器会跟踪你对每个查询选择了哪些项目，并在后续搜索中提升常用项目的排名。数据存储在 `~/.config/WenZi/chooser_usage.json`。

## API 参考

### `wz.leader(trigger_key, mappings)`

注册一组 Leader 键配置。

```python
wz.leader("cmd_r", [
    {"key": "w", "app": "WeChat"},
])
```

### `wz.app.launch(name)`

启动或聚焦应用。支持应用名称或完整路径。

```python
wz.app.launch("Safari")
wz.app.launch("/Applications/Visual Studio Code.app")
```

### `wz.app.frontmost()`

返回当前前台应用的名称。

```python
name = wz.app.frontmost()  # 例如 "Finder"
```

### `wz.alert(text, duration=2.0)`

在屏幕上显示一个浮动提示，`duration` 秒后自动消失。

```python
wz.alert("你好！", duration=3.0)
```

### `wz.notify(title, message="")`

发送 macOS 系统通知。

```python
wz.notify("构建完成", "所有测试已通过")
```

### `wz.ui.webview_panel(title, html, ...)`

创建自定义 WebView 面板。返回 `WebViewPanel` 对象，支持 `show()`、`close()`、`eval_js()`、`send()`、`on()`、`handle()` 方法。

```python
panel = wz.ui.webview_panel(
    title="我的面板",
    html="<h1>你好</h1>",
    width=400, height=300,
    floating=True,
)
panel.show()
```

#### JavaScript 桥接

WebView 内部会在文档加载前注入 `wz` 对象，提供 JS 与 Python 双向通信：

| JS API | 说明 |
|--------|------|
| `wz.send(event, data)` | 向 Python 发送事件（无返回值） |
| `wz.call(method, data)` | 调用 Python 处理器，返回 Promise |
| `wz.on(event, callback)` | 监听 Python 发来的事件 |
| `wz.playAudio(url)` | 通过原生 NSSound 播放音频（见下文） |

#### `wz.playAudio(url)` — 无 Now Playing 图标的音频播放

使用 macOS 原生 `NSSound` 播放音频 URL。避免在 WKWebView 中使用 HTML5 `new Audio(url).play()` 时菜单栏出现"正在播放"图标。

```javascript
// WebView JS 中 — 不会出现 Now Playing 图标
wz.playAudio('https://example.com/sound.mp3');

// 标准 HTML5 Audio — 会显示 Now Playing 图标（需要时仍可使用）
new Audio('https://example.com/sound.mp3').play();
```

`wz.playAudio()` 在所有 `wz.ui.webview_panel()` 面板中自动可用。在启动器预览中渲染的 HTML 需使用 `webkit.messageHandlers.chooser.postMessage({type: 'playAudio', url: '...'})` 代替（启动器不注入 `wz` JS 桥接）。

### `wz.pasteboard.get()`

获取当前剪贴板文本，没有内容则返回 `None`。

```python
text = wz.pasteboard.get()
```

### `wz.pasteboard.set(text)`

设置剪贴板文本。

```python
wz.pasteboard.set("Hello, world!")
```

### `wz.keystroke(key, modifiers=None)`

通过 Quartz CGEvent 模拟按键。

```python
wz.keystroke("c", modifiers=["cmd"])       # Cmd+C
wz.keystroke("v", modifiers=["cmd"])       # Cmd+V
wz.keystroke("space")                       # 空格
wz.keystroke("a", modifiers=["cmd", "shift"])  # Cmd+Shift+A
```

### `wz.execute(command, background=True)`

执行 Shell 命令。

```python
wz.execute("open ~/Downloads")             # 后台执行（返回 None）
output = wz.execute("date", background=False)  # 前台执行（返回 stdout）
```

### `wz.timer.after(seconds, callback)`

延迟执行一次。返回 `timer_id`。

```python
tid = wz.timer.after(5.0, lambda: wz.alert("5 秒到了"))
```

### `wz.timer.every(seconds, callback)`

按间隔重复执行。返回 `timer_id`。

```python
tid = wz.timer.every(60.0, lambda: wz.notify("提醒", "该休息了"))
```

### `wz.timer.cancel(timer_id)`

取消定时器。

```python
tid = wz.timer.every(10.0, my_func)
wz.timer.cancel(tid)
```

### `wz.date(format="%Y-%m-%d")`

返回格式化的当前日期/时间字符串。

```python
wz.date()              # "2025-03-15"
wz.date("%H:%M:%S")   # "14:30:00"
wz.date("%Y-%m-%d %H:%M")  # "2025-03-15 14:30"
```

### `wz.reload()`

重新加载所有脚本。停止当前监听器，清除脚本目录下已缓存的模块，重新读取 `init.py`（及其导入的所有子模块），然后重启。所有文件变更都会在重载后生效。

```python
wz.reload()
```

### `wz.chooser.show(initial_query=None)`

显示启动器面板。可选预填搜索输入。

```python
wz.chooser.show()
wz.chooser.show(initial_query="f readme")
```

### `wz.chooser.close()`

关闭启动器面板。

### `wz.chooser.toggle()`

切换启动器面板的显示/隐藏。

### `wz.chooser.show_source(prefix)`

以指定数据源激活状态显示启动器。

```python
wz.chooser.show_source("cb")  # 打开并显示剪贴板历史
```

### `wz.chooser.register_source(source)`

注册一个 `ChooserSource` 对象作为数据源。

### `wz.chooser.unregister_source(name)`

按名称移除已注册的数据源。

### `wz.chooser.pick(items, callback, placeholder="Choose...")`

将启动器用作通用选择 UI。显示一组固定选项；用户选择后调用 `callback(item_dict)`，如果关闭则调用 `callback(None)`。

```python
wz.chooser.pick(
    [{"title": "选项 A"}, {"title": "选项 B"}],
    callback=lambda item: print(item),
    placeholder="请选择...",
)
```

### `@wz.chooser.on(event)`

装饰器，注册启动器事件处理函数。

支持的事件：`open`、`close`、`select`、`delete`。

```python
@wz.chooser.on("select")
def on_select(item_info):
    print(f"选中了: {item_info['title']}")
```

### `wz.chooser.register_command(name, title, action, ...)`

在命令面板（`>` 前缀）中注册一个命名命令。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | `str` | *（必填）* | 唯一命令名（单个 token，如 `"reload-scripts"`） |
| `title` | `str` | *（必填）* | 显示在启动器中的标题 |
| `action` | `callable` | *（必填）* | 回调函数，接收参数字符串：`action(args_str)` |
| `subtitle` | `str` | `""` | 标题下方的描述 |
| `icon` | `str` | `""` | 图标 URL（`file://` 或 `data:` URI） |
| `modifiers` | `dict` | `None` | 修饰键动作（见上方命令章节） |
| `promoted` | `bool` | `False` | 同时出现在无前缀的主搜索中 |

```python
wz.chooser.register_command(
    name="greet",
    title="Greet",
    action=lambda args: wz.notify("Hello", args.strip() or "World"),
    promoted=True,
)
```

### `wz.chooser.unregister_command(name)`

按名称移除已注册的命令。

### `@wz.chooser.command(name, title, ...)`

装饰器，将函数注册为启动器命令。参数与 `register_command` 相同，但不需要 `action`（被装饰的函数即为 action）。

```python
@wz.chooser.command("greet", title="Greet", promoted=True)
def greet(args):
    wz.notify("Hello", args.strip() or "World")
```

### `@wz.chooser.source(name, prefix=None, priority=0, description="")`

装饰器，将搜索函数注册为启动器数据源。设置 `description` 可使该源出现在内置 `help` 命令的输出中。

```python
@wz.chooser.source("notes", prefix="n", priority=5, description="Search notes")
def search_notes(query):
    return [{"title": "...", "action": lambda: ...}]
```

## 使用示例

### 应用启动器

```python
wz.leader("cmd_r", [
    {"key": "1", "app": "1Password"},
    {"key": "b", "app": "Obsidian"},
    {"key": "c", "app": "Calendar"},
    {"key": "f", "app": "Safari"},
    {"key": "g", "app": "/Users/me/Applications/Google Chrome.app"},
    {"key": "n", "app": "Notes"},
    {"key": "s", "app": "Slack"},
    {"key": "t", "app": "iTerm"},
    {"key": "v", "app": "Visual Studio Code"},
    {"key": "w", "app": "WeChat"},
    {"key": "z", "app": "zoom.us"},
])
```

### 工具快捷键

```python
wz.leader("alt_r", [
    {"key": "d", "desc": "日期 → 剪贴板", "func": lambda: (
        wz.pasteboard.set(wz.date("%Y-%m-%d")),
        wz.notify("日期已复制", wz.date("%Y-%m-%d")),
    )},
    {"key": "t", "desc": "时间戳", "func": lambda: (
        wz.pasteboard.set(wz.date("%Y-%m-%d %H:%M:%S")),
        wz.alert("时间戳已复制"),
    )},
    {"key": "r", "desc": "重载脚本", "func": lambda: wz.reload()},
])
```

### 定时提醒

```python
# 每 30 分钟提醒休息
wz.timer.every(1800, lambda: wz.notify("休息", "站起来活动一下！"))
```

### 全局快捷键

```python
# Ctrl+Cmd+N 打开备忘录
wz.hotkey.bind("ctrl+cmd+n", lambda: wz.execute("open -a Notes"))
```

## 启动器配置

启动器的配置位于 `config.json` 的 `scripting.chooser` 下：

```json
{
  "scripting": {
    "chooser": {
      "enabled": true,
      "hotkey": "cmd+space",
      "app_search": true,
      "file_search": true,
      "clipboard_history": false,
      "snippets": false,
      "bookmarks": true,
      "usage_learning": true,
      "switch_to_english": true,
      "recycle_mode": "preload_html",
      "prefixes": {
        "clipboard": "cb",
        "files": "f",
        "snippets": "sn",
        "bookmarks": "bm"
      },
      "source_hotkeys": {
        "clipboard": "",
        "files": "",
        "snippets": "",
        "bookmarks": ""
      }
    }
  }
}
```

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 启动器总开关 |
| `hotkey` | `"cmd+space"` | 切换启动器的全局快捷键 |
| `app_search` | `true` | 启用应用搜索 |
| `file_search` | `true` | 启用 Spotlight 文件搜索 |
| `clipboard_history` | `false` | 启用剪贴板历史跟踪 |
| `snippets` | `false` | 启用代码片段搜索和自动展开 |
| `bookmarks` | `true` | 启用浏览器书签搜索 |
| `usage_learning` | `true` | 跟踪选择频率以优化排序 |
| `switch_to_english` | `true` | 启动器打开期间临时切换到英文输入法 |
| `recycle_mode` | `"preload_html"` | 启动器隐藏 60 秒后的空闲 WebView 策略 |
| `prefixes` | *（见上方）* | 各数据源的前缀字符串 |
| `source_hotkeys` | *（空）* | 直接打开启动器并预选数据源的快捷键 |

### `recycle_mode`

启动器关闭后，WenZi 会先短时间保留隐藏的 chooser，这样立刻重新打开会更快。
当 chooser 已经隐藏 60 秒后，`recycle_mode` 决定下一步怎么处理：

- `destroy`：完全销毁隐藏的 `WKWebView`；空闲内存最低，但下次打开最慢
- `prebuild`：销毁旧 WebView，然后预建一个新的空 panel/WebView 外壳；空闲内存低于 `preload_html`
- `preload_html`：销毁旧 WebView，然后后台预建并预加载 `chooser.html`；会占用更多空闲内存来换取更快的下次打开
- `keep_alive`：跳过空闲 recycle，持续复用隐藏的 WebView；重新打开最快，但长期内存最高

## 脚本运行环境

- 脚本作为标准 Python 代码运行，可以使用 `import` 导入任何模块
- `wz` 对象在 `init.py` 中作为全局变量直接可用，无需导入
- 在子模块中通过 `from wenzi.scripting.api import wz` 获取 `wz` 对象
- 脚本中的错误会被捕获并以浮窗提示显示
- 脚本在启动时加载一次，修改后需调用 `wz.reload()` 重新加载
- 脚本路径：`~/.config/WenZi/scripts/init.py`
- 可通过 `"scripting": {"script_dir": "/path/to/scripts"}` 自定义脚本目录

### 多文件脚本

可以将脚本拆分为多个 `.py` 文件。`init.py` 是入口文件，同目录下的其他文件可通过标准 `import` 语句导入：

```
~/.config/WenZi/scripts/
├── init.py          # 入口文件
├── my_sources.py    # 自定义启动器数据源
└── utils/
    ├── __init__.py
    └── formatting.py
```

```python
# init.py
import my_sources
from utils.formatting import fmt_date

wz.chooser.register_source(my_sources.build_source())
wz.hotkey.bind("cmd+shift+d", lambda: wz.type_text(fmt_date()))
```

```python
# my_sources.py
from wenzi.scripting.api import wz   # 子模块中需要导入 wz

def build_source():
    @wz.chooser.source("todos", prefix="td")
    def search_todos(query):
        return [{"title": "示例待办", "action": lambda: wz.alert("Done!")}]
```

调用 `wz.reload()` 时，脚本目录下的**所有文件**都会被重新加载，而不仅仅是 `init.py`。

> **注意：** 仅支持绝对导入（`import helper`、`from utils import foo`）。不支持相对导入（`from . import foo`），因为 `init.py` 不是作为 Python 包加载的。
>
> **注意：** 不要在用户脚本中定义 PyObjC 的 `NSObject` 子类。Objective-C 运行时不支持重复注册同名类，重载时会导致崩溃。

## 异步 / Async

所有接受回调的 API（`wz.hotkey.bind`、`wz.timer.after`、`wz.timer.every`、`wz.leader`、`wz.on`、`wz.chooser.command`、`wz.chooser.register_command`、`wz.chooser.pick`、`wz.chooser.on`）都透明支持 `async def` 回调，无需额外设置：

```python
import asyncio

async def slow_task():
    await asyncio.sleep(2)
    wz.notify("Done", "Task finished!")

wz.hotkey.bind("ctrl+cmd+a", slow_task)
```

Lambda 返回协程也同样支持：

```python
wz.hotkey.bind("ctrl+cmd+b", lambda: slow_task())
```

### `wz.run(coroutine)`

在回调之外执行异步代码：

```python
async def fetch_and_copy(url):
    import urllib.request
    resp = await asyncio.to_thread(urllib.request.urlopen, url)
    wz.pasteboard.set(resp.read().decode())
    wz.notify("Copied", f"Content from {url}")

wz.run(fetch_and_copy("https://example.com"))
```

### 工作原理

异步回调会自动提交到后台 asyncio 事件循环。未捕获的异常会记录到 `~/Library/Logs/WenZi/wenzi.log`，不会静默丢失。

脚本重载（`wz.reload()`）时，上一轮脚本产生的所有异步任务会被取消，有 2 秒的清理宽限期。

### 限制

- **不支持顶层 `await`** — `init.py` 是同步执行的。将异步逻辑写在 `async def` 函数中，注册为回调或通过 `wz.run()` 提交。
- **Chooser source 必须同步** — `@wz.chooser.source()` 的搜索函数需要立即返回结果，不支持异步。
- **不要在 async 回调中调用 `wz.run(coro).result()`** — 这会死锁。在 async 函数内直接用 `await`。
- **无内置并发控制** — 快速连按快捷键会产生多个并发任务。如需互斥，自行使用 flag 或 `asyncio.Lock`。

## 安全说明

脚本以**未沙箱化的 Python** 运行，拥有与 闻字 相同的系统权限。这意味着脚本可以：

- 读写你的用户账户能访问的任何文件
- 执行任意 Shell 命令
- 访问网络
- 读取剪贴板内容
- 模拟按键并与其他应用交互

**请只运行你自己编写或仔细审查过的脚本。** 不要从不可信的来源直接复制粘贴脚本。恶意脚本可能会在你不知情的情况下窃取数据、安装软件或修改文件。

出于安全考虑，脚本系统默认处于禁用状态。

## 常见问题

**脚本没有加载？**
- 确认 `config.json` 中 `"scripting": {"enabled": true}` 已设置
- 启用后需要重启闻字
- 查看日志 `~/Library/Logs/WenZi/wenzi.log` 排查错误

**Leader 键没有响应？**
- 确保 闻字 已获得辅助功能权限（系统设置 → 隐私与安全性 → 辅助功能）
- 检查触发键名称是否正确（如 `cmd_r` 而非 `right_cmd`）

**提示面板不可见？**
- 面板需要辅助功能权限才能显示在其他应用之上

**脚本报错？**
- 语法错误和异常会记录到日志并以浮窗提示
- 查看 `~/Library/Logs/WenZi/wenzi.log` 获取完整错误信息
