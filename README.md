[English](README.en.md) | 中文

# osu-map-finder

搜 osu! 谱面、一键下载、自动解压到指定目录的 Windows 小工具。单文件 Python（tkinter），零第三方依赖。

## 功能

- 启动时读取系统正在播放的歌曲（Windows SMTC），把歌名预填进搜索框；也可以手动输入关键词
- 高级筛选：模式、状态、搜索范围、分类、语言，以及 Stars / AR / OD / CS / HP / BPM / Length 区间
- 结果列出曲名、歌手、谱师、模式、Stars、BPM、时长；每行右侧一键下载
- 下载的 osz 自动改名为 zip 并解压到「保存路径」下的 `艺术家 - 曲名\`，随后删除 zip

## 用法

从 Release 下载 zip，解压后双击 `osu-map-finder.exe` 即可；也可以直接跑源码：

    python osu_map_finder.py

保存路径在窗口底部设置，记在同目录的 `config.json` 里，默认是程序旁边的 `beatmaps/`。

## 数据来源与致谢

- 谱面搜索与下载镜像来自 **Sayobot**（osu.sayobot.cn），感谢其长期提供公开、免登录的搜索与镜像服务
- 谱面原始元数据来自 osu! 官方数据库，由 Sayobot 镜像提供
- 本工具只是检索与下载的客户端，不托管、不镜像任何谱面文件
- 本工具与 osu! 官方、Sayobot 均无隶属关系

## 版权声明

- 歌曲、谱面及其中包含的音频、图片等素材，版权归各自的创作者与权利方所有
- 本工具不修改、不再分发这些内容；下载后的使用请遵守 osu! 及相关权利方的规定，请勿用于商业用途

## 许可证

MIT License，详见 LICENSE。
