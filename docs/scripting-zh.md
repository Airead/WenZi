# 脚本系统

VoiceText 内置了一个基于 Python 的脚本系统，可以用来自动化 macOS 常见操作——通过 Leader 键启动应用、绑定全局快捷键、显示提示、操作剪贴板等。

## 快速开始

1. **启用脚本系统**：在 设置 → 通用 → Scripting 中打开开关，或者直接编辑 `config.json`：

   ```json
   {
     "scripting": {
       "enabled": true
     }
   }
   ```

2. **创建脚本文件** `~/.config/VoiceText/scripts/init.py`：

   ```python
   vt.leader("cmd_r", [
       {"key": "w", "app": "WeChat"},
       {"key": "s", "app": "Slack"},
       {"key": "t", "app": "iTerm"},
   ])
   ```

3. **重启 VoiceText**。按住右 Command 键，屏幕上会显示快捷键面板，再按字母键即可启动对应应用。

## Leader 键

Leader 键的使用方式：按住一个触发键（如右 Command），屏幕上会浮现可用映射列表，然后按第二个键执行对应操作。松开触发键后面板自动消失。

```python
vt.leader("cmd_r", [
    {"key": "w", "app": "WeChat"},
    {"key": "f", "app": "Safari"},
    {"key": "g", "app": "/Users/me/Applications/Google Chrome.app"},
    {"key": "i", "exec": "/usr/local/bin/code ~/work/projects", "desc": "projects"},
    {"key": "d", "desc": "日期", "func": lambda: (
        vt.pasteboard.set(vt.date("%Y-%m-%d")),
        vt.notify("日期已复制", vt.date("%Y-%m-%d")),
    )},
    {"key": "r", "desc": "重载脚本", "func": lambda: vt.reload()},
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
vt.leader("cmd_r", [...])   # 右 Command 启动应用
vt.leader("alt_r", [...])   # 右 Alt 执行工具操作
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

## API 参考

### `vt.leader(trigger_key, mappings)`

注册一组 Leader 键配置。

```python
vt.leader("cmd_r", [
    {"key": "w", "app": "WeChat"},
])
```

### `vt.app.launch(name)`

启动或聚焦应用。支持应用名称或完整路径。

```python
vt.app.launch("Safari")
vt.app.launch("/Applications/Visual Studio Code.app")
```

### `vt.app.frontmost()`

返回当前前台应用的名称。

```python
name = vt.app.frontmost()  # 例如 "Finder"
```

### `vt.alert(text, duration=2.0)`

在屏幕上显示一个浮动提示，`duration` 秒后自动消失。

```python
vt.alert("你好！", duration=3.0)
```

### `vt.notify(title, message="")`

发送 macOS 系统通知。

```python
vt.notify("构建完成", "所有测试已通过")
```

### `vt.pasteboard.get()`

获取当前剪贴板文本，没有内容则返回 `None`。

```python
text = vt.pasteboard.get()
```

### `vt.pasteboard.set(text)`

设置剪贴板文本。

```python
vt.pasteboard.set("Hello, world!")
```

### `vt.keystroke(key, modifiers=None)`

通过 Quartz CGEvent 模拟按键。

```python
vt.keystroke("c", modifiers=["cmd"])       # Cmd+C
vt.keystroke("v", modifiers=["cmd"])       # Cmd+V
vt.keystroke("space")                       # 空格
vt.keystroke("a", modifiers=["cmd", "shift"])  # Cmd+Shift+A
```

### `vt.execute(command, background=True)`

执行 Shell 命令。

```python
vt.execute("open ~/Downloads")             # 后台执行（返回 None）
output = vt.execute("date", background=False)  # 前台执行（返回 stdout）
```

### `vt.timer.after(seconds, callback)`

延迟执行一次。返回 `timer_id`。

```python
tid = vt.timer.after(5.0, lambda: vt.alert("5 秒到了"))
```

### `vt.timer.every(seconds, callback)`

按间隔重复执行。返回 `timer_id`。

```python
tid = vt.timer.every(60.0, lambda: vt.notify("提醒", "该休息了"))
```

### `vt.timer.cancel(timer_id)`

取消定时器。

```python
tid = vt.timer.every(10.0, my_func)
vt.timer.cancel(tid)
```

### `vt.date(format="%Y-%m-%d")`

返回格式化的当前日期/时间字符串。

```python
vt.date()              # "2025-03-15"
vt.date("%H:%M:%S")   # "14:30:00"
vt.date("%Y-%m-%d %H:%M")  # "2025-03-15 14:30"
```

### `vt.reload()`

重新加载所有脚本。停止当前监听器，重新读取 `init.py`，然后重启。

```python
vt.reload()
```

## 使用示例

### 应用启动器

```python
vt.leader("cmd_r", [
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
vt.leader("alt_r", [
    {"key": "d", "desc": "日期 → 剪贴板", "func": lambda: (
        vt.pasteboard.set(vt.date("%Y-%m-%d")),
        vt.notify("日期已复制", vt.date("%Y-%m-%d")),
    )},
    {"key": "t", "desc": "时间戳", "func": lambda: (
        vt.pasteboard.set(vt.date("%Y-%m-%d %H:%M:%S")),
        vt.alert("时间戳已复制"),
    )},
    {"key": "r", "desc": "重载脚本", "func": lambda: vt.reload()},
])
```

### 定时提醒

```python
# 每 30 分钟提醒休息
vt.timer.every(1800, lambda: vt.notify("休息", "站起来活动一下！"))
```

### 全局快捷键

```python
# Ctrl+Cmd+N 打开备忘录
vt.hotkey.bind("ctrl+cmd+n", lambda: vt.execute("open -a Notes"))
```

## 脚本运行环境

- 脚本作为标准 Python 代码运行，可以使用 `import` 导入任何模块
- `vt` 对象作为全局变量直接可用，无需导入
- 脚本中的错误会被捕获并以浮窗提示显示
- 脚本在启动时加载一次，修改后需调用 `vt.reload()` 重新加载
- 脚本路径：`~/.config/VoiceText/scripts/init.py`
- 可通过 `"scripting": {"script_dir": "/path/to/scripts"}` 自定义脚本目录

## 安全说明

脚本以**未沙箱化的 Python** 运行，拥有与 VoiceText 相同的系统权限。这意味着脚本可以：

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
- 启用后需要重启 VoiceText
- 查看日志 `~/Library/Logs/VoiceText/voicetext.log` 排查错误

**Leader 键没有响应？**
- 确保 VoiceText 已获得辅助功能权限（系统设置 → 隐私与安全性 → 辅助功能）
- 检查触发键名称是否正确（如 `cmd_r` 而非 `right_cmd`）

**提示面板不可见？**
- 面板需要辅助功能权限才能显示在其他应用之上

**脚本报错？**
- 语法错误和异常会记录到日志并以浮窗提示
- 查看 `~/Library/Logs/VoiceText/voicetext.log` 获取完整错误信息
