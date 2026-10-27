# Linux sing-box 自动化项目规则

## 项目定位

本项目的目标是在 Linux 云服务器上使用 **sing-box** 完成代理配置、订阅更新、分流和节点测速自动化。

## 工作边界

- Mac 只用于维护脚本、配置模板和测试；Linux 云服务器负责实际部署和运行。
- 不在 Mac 上安装 Linux sing-box，不安装 Karing，不修改 Karing 配置。
- `archive/` 是历史资料，不纳入当前实现；Git 中必须忽略。

## 敏感信息

- 订阅 URL、token、UUID、密码和节点密钥不得写入 Git、日志或用户回复。
- 订阅缓存必须使用 root-only 或服务账号可读的权限。
- GitHub 下载地址统一使用 `https://gh-proxy.org/https://...`。

## 输出与变更

- 所有交流、注释和文档使用中文。
- 修改前保留用户已有内容，只做必要变更。
- 修改脚本后运行 Python 编译检查和现有测试。
