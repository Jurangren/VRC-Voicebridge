# VRC TTS Text

VRC TTS Text 是一个面向 Windows 和 VRChat 的 Python 托盘后台程序。程序运行后，按下自定义全局热键会弹出一个小输入框；用户输入中文并回车后，程序会自动把原文翻译成日文，调用 OpenAI TTS 生成语音，通过 VRChat OSC 显示聊天气泡并控制开麦，然后把 TTS 音频播放到虚拟麦克风，播放结束后自动关麦。

默认聊天气泡格式：

```text
源文本
翻译文本
```

## 功能概览

- 托盘后台运行，不占用主窗口。
- 默认全局热键为 b，可在 Web 设置面板中修改。
- 热键唤出置顶输入框，回车提交文本，Esc 隐藏输入框。
- 打开输入框时会通过 VRChat OSC 发送正在输入状态，隐藏输入框时自动关闭正在输入状态。
- 提交后会在屏幕左下角显示悬浮状态窗口，展示当前步骤和 x/x 进度。
- 悬浮状态窗口支持透明度设置；处理中、成功、正在播放音频为绿色，失败为红色。
- 默认把中文翻译成日文。
- 支持 Google 翻译、微软翻译、腾讯翻译和百度翻译，可在 Web 设置面板中选择，默认翻译失败重试次数为 2。
- 使用 OpenAI TTS 生成语音，支持自定义 API Key、Base URL、模型、音色、输出格式、超时和失败重试次数。
- 默认 TTS 失败重试次数为 2。
- 通过 VRChat OSC 发送聊天气泡文本。
- 通过 VRChat OSC 控制正在输入、开麦和关麦。
- 使用 PyAudio 把 WAV 音频播放到 VB-Cable 虚拟麦克风入口，也可以同步播放到本机默认扬声器或耳机。
- 提供本地 Web 设置面板，默认地址为 http://127.0.0.1:8765/。
- 任意环节失败都会终止本次处理流程，但不会退出程序；状态悬浮窗口显示简短错误，Web 设置面板显示完整错误日志。

## 运行流程

```text
按下热键
  -> 弹出输入框
  -> VRChat OSC 显示正在输入
  -> 输入中文并回车
  -> VRChat OSC 关闭正在输入
  -> 1/6 使用所选翻译服务翻译为日文
  -> 2/6 OpenAI TTS 生成 WAV
  -> 3/6 VRChat OSC 发送聊天气泡
  -> 4/6 VRChat OSC 开麦
  -> 5/6 PyAudio 播放 WAV 到虚拟麦克风
  -> 6/6 VRChat OSC 关麦
```

如果翻译、TTS、OSC、音频设备查找或音频播放任意一步失败，后续步骤会立即停止，左下角状态悬浮窗口会显示简短错误，完整错误和堆栈可在 Web 设置面板的“最近错误”区域查看。

## 项目结构

```text
vrc-tts-text/
  main.py                         程序入口
  requirements.txt                Python 依赖
  README.md                       项目说明
  config.example.json             无敏感信息的配置示例
  start-vrc-tts.bat               Windows 快速启动脚本
  vrc_tts/
    __init__.py
    core/
      __init__.py
      config.py                   配置模型、配置读写、默认参数
      errors.py                   统一错误类型和最近错误记录
      pipeline.py                 主业务流水线
    services/
      __init__.py
      translator.py               Google、微软、腾讯、百度翻译封装和重试
      tts_client.py               OpenAI TTS 调用和重试
      osc_client.py               VRChat OSC 客户端
      audio_player.py             PyAudio 音频设备扫描和播放
    ui/
      __init__.py
      input_window.py             Tkinter 输入框
      status_overlay.py           左下角悬浮状态和进度窗口
      hotkey.py                   全局热键管理
      tray_app.py                 系统托盘菜单
    web/
      __init__.py
      server.py                   Flask Web 设置服务
      templates/
        settings.html             设置页面模板
      static/
        style.css                 设置页面样式
```

## 环境要求

- Windows 10 或 Windows 11。
- Python 3.10 或更高版本。
- VRChat 已启用 OSC。
- 已安装虚拟音频线，推荐 VB-Cable。
- 可用的 OpenAI API Key，或兼容 OpenAI Audio Speech API 的中转服务。
- 网络环境可以访问所选翻译服务和 OpenAI API 或你的中转地址。
- 如果使用微软翻译，需要可用的 Microsoft Translator Key，必要时需要 Region。
- 如果使用腾讯翻译，需要腾讯云访问管理 CAM API 密钥 SecretId 和 SecretKey。
- 如果使用百度翻译，需要百度翻译开放平台 App ID 和密钥。

## 安装步骤

### 1. 安装 Python 依赖

在项目目录执行：

```bash
pip install -r requirements.txt
```

### 2. 处理 PyAudio 安装问题

如果 Windows 上安装 PyAudio 失败，可以尝试：

```bash
pip install pipwin
pipwin install pyaudio
```

也可以安装与你的 Python 版本对应的 PyAudio wheel。

### 3. 安装和配置 VB-Cable

安装 VB-Cable 后，系统通常会出现两类设备：

- CABLE Input：播放设备，程序会把声音输出到这里。
- CABLE Output：录音设备，VRChat 里应该选择它作为麦克风。

在 VRChat 中，把麦克风设备设置为 CABLE Output。

### 4. 开启 VRChat OSC

在 VRChat 中开启 OSC。默认情况下，本程序会向 127.0.0.1:9000 发送 OSC 消息。

## 启动程序

在项目目录执行：

```bash
python main.py
```

启动后程序会：

1. 打开本地 Web 设置面板。
2. 创建系统托盘图标。
3. 注册默认全局热键 b。

## 第一次使用配置

打开设置面板后，至少需要配置：

- OpenAI API Key：你的 OpenAI API Key。
- Base URL：默认 https://api.openai.com/v1；如果使用中转服务，改成中转地址。
- 模型：默认 tts-1。
- 音色：默认 alloy。
- 输出格式：建议保持 wav，因为当前播放器只支持 WAV。
- OSC Host：默认 127.0.0.1。
- OSC Port：默认 9000。
- 虚拟麦克风设备关键字：默认 CABLE Input。
- 翻译服务：默认 Google 翻译；微软翻译需要填写 Microsoft Translator Key；腾讯翻译需要填写腾讯云 CAM SecretId 和 SecretKey；百度翻译需要填写百度翻译 App ID 和密钥。

敏感配置会保存在本地 `config.json` 中。该文件已被 `.gitignore` 排除，不应提交到 Git。仓库仅保留无密钥的 `config.example.json` 作为配置示例。

保存后，可以先使用设置面板里的测试按钮：

- 测试 OSC：向 VRChat 聊天框发送测试文本。
- 测试 TTS：调用 OpenAI TTS 生成一段测试音频。
- 扫描音频设备：列出当前系统中的输出设备，方便确认 VB-Cable 设备名称。

## 使用方式

1. 运行程序。
2. 确认 VRChat 正在运行且 OSC 已开启。
3. 确认 VRChat 麦克风选择 CABLE Output。
4. 按默认热键 b。
5. 在弹出的输入框中输入中文。
6. 回车提交。
7. 程序会自动翻译、合成语音、发送气泡、开麦播放、播放完成后关麦。

## 配置项说明

配置会保存在运行目录下的 config.json 中。如果文件不存在，程序会自动创建默认配置。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| hotkey | b | 全局热键，可以改成 ctrl+shift+b 等组合键 |
| web_host | 127.0.0.1 | Web 设置面板监听地址 |
| web_port | 8765 | Web 设置面板端口 |
| translation_provider | google | 翻译服务，可选 google、microsoft、tencent、baidu |
| source_language | zh-CN | 翻译源语言 |
| target_language | ja | 翻译目标语言 |
| bubble_format | {original}\n{translated} | VRChat 聊天气泡格式 |
| translation_retry_count | 2 | 翻译失败后的重试次数 |
| microsoft_translator_key | 空 | Microsoft Translator Key |
| microsoft_translator_region | 空 | Microsoft Translator Region，Azure 多服务资源通常需要填写 |
| microsoft_translator_endpoint | https://api.cognitive.microsofttranslator.com | Microsoft Translator Endpoint |
| tencent_translator_secret_id | 空 | 腾讯云 CAM API 密钥 SecretId，不是 AppID |
| tencent_translator_secret_key | 空 | 腾讯云 CAM API 密钥 SecretKey |
| tencent_translator_region | ap-guangzhou | 腾讯翻译 Region |
| tencent_translator_endpoint | tmt.tencentcloudapi.com | 腾讯翻译 Endpoint |
| baidu_translator_app_id | 空 | 百度翻译开放平台 App ID |
| baidu_translator_secret_key | 空 | 百度翻译开放平台密钥 |
| baidu_translator_endpoint | https://fanyi-api.baidu.com/api/trans/vip/translate | 百度翻译 Endpoint |
| overlay_alpha | 0.92 | 左下角状态悬浮窗口透明度，范围 0.1 到 1 |
| openai_api_key | 空 | OpenAI API Key |
| openai_base_url | https://api.openai.com/v1 | OpenAI Base URL，可填中转地址 |
| openai_tts_model | tts-1 | TTS 模型 |
| openai_tts_voice | alloy | TTS 音色 |
| openai_tts_format | wav | TTS 输出格式，建议保持 wav |
| tts_retry_count | 2 | TTS 失败后的重试次数 |
| tts_timeout_seconds | 60 | TTS 请求超时时间 |
| osc_host | 127.0.0.1 | VRChat OSC 地址 |
| osc_port | 9000 | VRChat OSC 端口 |
| osc_chatbox_path | /chatbox/input | VRChat 聊天框 OSC 路径 |
| osc_typing_path | /chatbox/typing | VRChat 正在输入 OSC 路径 |
| osc_voice_path | /input/Voice | VRChat 开麦 OSC 路径 |
| osc_chat_enter | true | 发送聊天框后是否直接提交 |
| osc_chat_notify | false | 是否触发聊天框通知 |
| virtual_audio_device_keyword | CABLE Input | 用于匹配虚拟麦克风播放设备的关键字 |
| play_to_speaker | true | 是否同步播放到默认扬声器或耳机 |
| audio_chunk_size | 1024 | PyAudio 播放块大小 |

## Web 设置面板

默认地址：

```text
http://127.0.0.1:8765/
```

面板功能：

- 修改热键。
- 选择翻译服务，并按当前翻译服务显示对应配置。
- 修改翻译语言、聊天气泡格式和翻译失败重试次数。
- 修改状态悬浮窗口透明度。
- 修改 OpenAI TTS 参数。
- 修改 VRChat OSC 参数。
- 修改虚拟音频设备关键字。
- 打开输入框。
- 测试 OSC。
- 测试 TTS。
- 扫描音频输出设备。
- 查看最近一次错误。
- 处理文本时查看左下角悬浮进度。

## VRChat OSC 默认路径

默认聊天框路径：

```text
/chatbox/input
```

默认正在输入路径：

```text
/chatbox/typing
```

默认开麦路径：

```text
/input/Voice
```

如果你的 VRChat 或 OSC 映射有特殊配置，可以在 Web 设置面板中修改。

## 错误处理策略

每次提交文本都会作为一次独立任务执行。任务中任意一步失败时：

1. 立即停止后续步骤。
2. 如果已经开麦，会尽力发送关麦 OSC。
3. 左下角状态悬浮窗口显示简短错误信息。
4. 完整错误详情和堆栈记录到设置面板的“最近错误”区域。
5. 程序继续托盘后台运行，不会退出。

## 常见问题

### 按热键没有反应

- 尝试把热键改成组合键，例如 ctrl+shift+b。
- 尝试以管理员权限启动终端再运行程序。
- 检查是否有其他软件占用了同一个热键。

### VRChat 没有显示聊天气泡

- 确认 VRChat 已开启 OSC。
- 确认 OSC Host 为 127.0.0.1。
- 确认 OSC Port 为 9000。
- 使用设置面板的测试 OSC 按钮验证。

### 队友听不到声音

- 确认已安装 VB-Cable。
- 在设置面板扫描音频设备，确认存在 CABLE Input。
- 确认 VRChat 麦克风选择 CABLE Output。
- 确认虚拟麦克风设备关键字能匹配到 CABLE Input。

### TTS 失败

- 检查 OpenAI API Key 是否正确。
- 检查 Base URL 是否正确。
- 检查模型和音色是否被当前服务支持。
- 检查网络是否能访问 API。
- 设置面板中可以调高超时时间或重试次数。

### 翻译失败

- 检查当前翻译服务对应的密钥或 App ID 是否正确。
- 检查源语言、目标语言是否被当前翻译服务支持。
- 检查网络是否能访问所选翻译服务。
- 设置面板中可以调高翻译失败重试次数。

### 腾讯翻译提示 SecretId 不存在

- 确认填写的是腾讯云访问管理 CAM 的 API 密钥 SecretId，不是 AppID。
- 确认 SecretId 和 SecretKey 没有前后空格。
- 确认该 API 密钥没有被删除或禁用。
- 确认账号已开通腾讯云机器翻译 TMT 服务。

### 提示播放器只支持 WAV

当前音频播放模块使用 wave 和 PyAudio 直接读取 WAV，所以 openai_tts_format 请保持 wav。

## 开发说明

可以用下面的命令做语法检查：

```bash
python -m py_compile main.py vrc_tts\core\config.py vrc_tts\core\errors.py vrc_tts\core\pipeline.py vrc_tts\services\translator.py vrc_tts\services\tts_client.py vrc_tts\services\osc_client.py vrc_tts\services\audio_player.py vrc_tts\ui\input_window.py vrc_tts\ui\hotkey.py vrc_tts\ui\tray_app.py vrc_tts\web\server.py
```

主要模块职责：

- [main.py](main.py)：启动应用，连接各模块。
- [vrc_tts/core/config.py](vrc_tts/core/config.py)：配置默认值、加载、保存和表单更新。
- [vrc_tts/core/errors.py](vrc_tts/core/errors.py)：统一错误类型、最近错误记录和状态栏短错误文本。
- [vrc_tts/core/pipeline.py](vrc_tts/core/pipeline.py)：单次输入文本的完整处理流水线。
- [vrc_tts/services/translator.py](vrc_tts/services/translator.py)：Google、微软、腾讯、百度翻译封装和重试。
- [vrc_tts/services/tts_client.py](vrc_tts/services/tts_client.py)：OpenAI TTS 封装和重试。
- [vrc_tts/services/osc_client.py](vrc_tts/services/osc_client.py)：VRChat OSC 消息发送。
- [vrc_tts/services/audio_player.py](vrc_tts/services/audio_player.py)：音频设备扫描和播放到虚拟麦克风。
- [vrc_tts/ui/input_window.py](vrc_tts/ui/input_window.py)：输入框 UI。
- [vrc_tts/ui/status_overlay.py](vrc_tts/ui/status_overlay.py)：左下角悬浮状态窗口。
- [vrc_tts/ui/hotkey.py](vrc_tts/ui/hotkey.py)：全局热键。
- [vrc_tts/ui/tray_app.py](vrc_tts/ui/tray_app.py)：系统托盘。
- [vrc_tts/web/server.py](vrc_tts/web/server.py)：Flask 设置面板和测试接口。

## 注意事项

- 这个项目优先支持 Windows 和 VRChat 桌面版使用场景。
- keyboard 全局热键库在某些环境下可能需要管理员权限。
- Google Translate 非官方接口可能受网络环境影响。
- 微软翻译、腾讯翻译和百度翻译需要在设置面板填写对应云服务凭证。
- `config.json`、`.env`、缓存、日志和本地音频文件已被 `.gitignore` 排除，避免提交敏感配置和运行产物。
- 如果使用自定义 OpenAI Base URL，请确认它兼容 OpenAI Audio Speech API。
- 当前音频播放链路要求 TTS 输出为 WAV。
