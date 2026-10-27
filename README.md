# Linux sing-box 自动部署与使用

部分机场会拦截 Linux 的请求，当前验证成功的机场 [山海](https://shanhai.me/#/dashboard) 。

## 一、从本地电脑上传文件

推荐使用 termius 等图形化终端连接远程云服务器

### termius

1. 连接远程云服务器
2. 创建目标目录：

  ```bash
  sudo mkdir -p /opt/sing-box-auto && sudo chown "$USER":"$USER" /opt/sing-box-auto
  ```

3. 通过 SFTP 将 'sing_box_auto.py', 'custom_config.yaml' 上传到 '/opt/sing-box-auto' 目录下
4. 进入目标目录：

  ```bash
  cd /opt/sing-box-auto
  ```

5. 验证是否上传成功

  ```bash
  ls -l sing_box_auto.py custom_config.yaml
  ```

`py_compile` 不输出错误且 `test` 返回状态码 0，表示文件可以继续使用。

## 三、首次部署

  ```bash
  sudo install -d -m 755 /etc/apt/sources.list.d && \
  sudo python3 sing_box_auto.py deploy
  ```

`deploy` 会完成：

1. 检查 Debian/Ubuntu、CPU 架构和 systemd；
2. 安装 Python、PyYAML、curl 和 sing-box；
3. 创建 `/etc/sing-box-auto`、`/var/lib/sing-box-auto`；
4. 安装 `sing-box-auto.service` 和可选更新 timer；
5. 将脚本复制到 `/usr/local/libexec/sing-box-auto.py`。

部署过程中会显示 `[阶段 1/7]` 到 `[阶段 7/7]`，每个阶段结束时显示耗时。APT 安装阶段会保留包管理器输出；如果某一阶段长时间没有结束，可以根据最后一个阶段判断卡在环境检查、软件包下载、订阅输入还是 systemd 配置。

如果没有保存过订阅，脚本会交互式询问：

1. `请输入或粘贴订阅链接...`：粘贴订阅链接，输入内容不会回显，按回车提交；
2. `请给你刚才输入的订阅链接起个名字...`：输入 `shanhai`；
3. 再次询问订阅链接时直接按回车结束。

至少需要输入一个订阅链接，也可以连续输入多个订阅。

## 四、生成配置并启动

部署准备完成后必须执行：

  ```bash
  sudo python3 /usr/local/libexec/sing-box-auto.py update
  ```

`update` 会下载订阅、识别格式、解析 AnyTLS/VLESS 节点、下载规则集、生成配置、执行 `sing-box check`，然后重启服务。任何关键步骤失败都会停止更新并保留旧配置。

更新过程中会显示 `[阶段 1/8]` 到 `[阶段 8/8]`，包括订阅下载、节点解析、规则集下载、配置校验和服务重启。

## 五、检查服务和代理

  ```bash
  sudo systemctl enable --now sing-box-auto
  sudo systemctl status sing-box-auto --no-pager
  sudo ss -lntp | grep ':1080'
  ```

入口默认只监听：

  ```text
  127.0.0.1:1080
  ```

服务器本机测试代理：

  ```bash
  curl -x socks5://127.0.0.1:1080 \
    --max-time 20 \
    -I https://www.gstatic.com/generate_204
  ```

返回 HTTP 204 或其他成功响应，表示代理链路正常。

## 六、日常更新订阅

手动更新：

  ```bash
  sudo python3 /usr/local/libexec/sing-box-auto.py update
  ```

查看服务日志：

  ```bash
  sudo journalctl -u sing-box-auto -e --no-pager
  ```

配置更新采用临时文件校验和原子替换。校验或重启失败时会尝试恢复旧配置。

## 七、启用自动更新

自动更新默认关闭。确认手动更新和代理测试稳定后，再启用：

  ```bash
  sudo systemctl enable --now sing-box-auto-update.timer
  sudo systemctl list-timers sing-box-auto-update.timer
  ```

查看自动更新结果：

  ```bash
  sudo systemctl status sing-box-auto-update.timer --no-pager
  sudo journalctl -u sing-box-auto-update.service -e --no-pager
  ```

## 八、服务管理

  ```bash
  sudo systemctl start sing-box-auto
  sudo systemctl stop sing-box-auto
  sudo systemctl restart sing-box-auto
  sudo systemctl enable sing-box-auto
  sudo systemctl disable sing-box-auto
  sudo systemctl status sing-box-auto --no-pager
  ```

## 九、回滚

如果更新失败且目录中保留了本次更新前的配置备份，可以尝试：

  ```bash
  sudo python3 /usr/local/libexec/sing-box-auto.py rollback
  ```

如果没有可用备份，先查看日志，不要删除 `/etc/sing-box-auto` 或 `/var/lib/sing-box-auto`。

## 十、配置和权限

  ```text
  /etc/sing-box-auto/subscriptions.yaml  订阅 URL，0600
  /etc/sing-box-auto/custom_config.yaml   分流规则
  /etc/sing-box-auto/config.json          生成配置，root:sing-box，0640
  /var/lib/sing-box-auto/rulesets/        .srs 规则集缓存
  /var/backups/sing-box-auto/             更新过程中的配置备份
  /usr/local/libexec/sing-box-auto.py     systemd 使用的脚本副本
  ```

服务由 `sing-box` 用户运行。更新脚本会自动设置生成配置和规则集的读取权限，以及运行缓存目录的写入权限。

## 十一、支持范围

支持识别：

- Sing-box JSON；
- Clash/Mihomo YAML；
- 明文分享链接；
- 外层 Base64 分享链接。

首版生成支持 AnyTLS 和 VLESS。未知格式、缺少必要字段或暂不支持协议时会停止生成，不会静默跳过节点。

GitHub 相关规则集下载统一经过 `gh-proxy.org`。默认规则使用 SagerNet 的 `geoip-cn.srs` 和 `geosite-cn.srs`。

默认不会把代理入口暴露到公网。如需远程客户端访问，必须另行设计认证、防火墙和 TLS，不要直接暴露无认证 mixed 入口。

## 十二、为 Claude Code 和命令行工具持久化代理

sing-box 配置和服务确认正常后，可以把本机代理写入当前用户的 `~/.bashrc`。这样重新打开终端后，不需要重复执行 `export`。

只执行一次：

```bash
cat >> ~/.bashrc <<'EOF'

# sing-box 本机代理
export http_proxy='http://127.0.0.1:1080'
export https_proxy='http://127.0.0.1:1080'
export all_proxy='socks5h://127.0.0.1:1080'
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"
export ALL_PROXY="$all_proxy"
EOF

source ~/.bashrc
```

检查变量是否生效：

```bash
env | grep -iE '^(http|https|all)_proxy='
```

之后可以直接执行用户需要的 Claude Code 安装命令：

```bash
# Claude Code 官方文档说明其不支持 SOCKS 代理；安装时清除 all_proxy，保留 HTTP 代理
unset all_proxy ALL_PROXY
export http_proxy='http://127.0.0.1:1080'
export https_proxy='http://127.0.0.1:1080'
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"

curl -fsSL https://claude.ai/install.sh | bash
```

如果安装器已下载并解压二进制，但只在最后的版本检查阶段报错，可按安装器提示使用：

```bash
unset all_proxy ALL_PROXY
curl -fsSL https://claude.ai/install.sh | bash -s -- --force
```

## 十三、使用官方 APT 仓库安装 Claude Code

如果原生 `install.sh` 在版本检查阶段失败，Ubuntu/Debian 可以改用 Claude Code 官方签名 APT 仓库。下面使用 `stable` 频道；官方文档也提供 `latest` 频道。

Claude Code 不支持 SOCKS 代理，安装 APT 包时清除 `all_proxy`，保留 sing-box 的 HTTP 代理：

```bash
unset all_proxy ALL_PROXY
export http_proxy='http://127.0.0.1:1080'
export https_proxy='http://127.0.0.1:1080'
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"

sudo env \
  http_proxy="$http_proxy" \
  https_proxy="$https_proxy" \
  HTTP_PROXY="$HTTP_PROXY" \
  HTTPS_PROXY="$HTTPS_PROXY" \
  apt update

sudo env \
  http_proxy="$http_proxy" \
  https_proxy="$https_proxy" \
  HTTP_PROXY="$HTTP_PROXY" \
  HTTPS_PROXY="$HTTPS_PROXY" \
  apt install -y curl gnupg

sudo install -d -m 0755 /etc/apt/keyrings

sudo env \
  http_proxy="$http_proxy" \
  https_proxy="$https_proxy" \
  HTTP_PROXY="$HTTP_PROXY" \
  HTTPS_PROXY="$HTTPS_PROXY" \
  curl -fsSL https://downloads.claude.ai/keys/claude-code.asc \
  -o /etc/apt/keyrings/claude-code.asc
```

核对密钥指纹，官方指纹应为：

```text
31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE
```

执行：

```bash
gpg --show-keys /etc/apt/keyrings/claude-code.asc
```

确认指纹后注册 stable 仓库并安装：

```bash
echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] https://downloads.claude.ai/claude-code/apt/stable stable main" \
  | sudo tee /etc/apt/sources.list.d/claude-code.list

sudo env \
  http_proxy="$http_proxy" \
  https_proxy="$https_proxy" \
  HTTP_PROXY="$HTTP_PROXY" \
  HTTPS_PROXY="$HTTPS_PROXY" \
  apt update

sudo env \
  http_proxy="$http_proxy" \
  https_proxy="$https_proxy" \
  HTTP_PROXY="$HTTP_PROXY" \
  HTTPS_PROXY="$HTTPS_PROXY" \
  apt install -y claude-code
```

验证安装：

```bash
command -v claude
claude --version
claude doctor
```

以后升级：

```bash
sudo env \
  http_proxy="$http_proxy" \
  https_proxy="$https_proxy" \
  HTTP_PROXY="$HTTP_PROXY" \
  HTTPS_PROXY="$HTTPS_PROXY" \
  apt update

sudo env \
  http_proxy="$http_proxy" \
  https_proxy="$https_proxy" \
  HTTP_PROXY="$HTTP_PROXY" \
  HTTPS_PROXY="$HTTPS_PROXY" \
  apt upgrade claude-code
```

## 十四、使用 NVM 和 npm 安装 Claude Code

如果不想配置 Claude Code APT 仓库，可以使用 NVM 安装 Node.js，再通过 npm 安装 Claude Code。当前官方 npm 包要求 Node.js 22 或更高版本；Node.js 24 可以使用。

安装前让 npm 和 Node.js 使用 sing-box 的 HTTP 代理，并清除 SOCKS 代理变量：

```bash
unset all_proxy ALL_PROXY
export http_proxy='http://127.0.0.1:1080'
export https_proxy='http://127.0.0.1:1080'
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"
```

安装 NVM 和 Node.js：

```bash
curl -o- https://gh-proxy.org/https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

nvm install 24
nvm alias default 24
```

安装 Claude Code：

```bash
npm install -g @anthropic-ai/claude-code
```

如果 npm 提示 Claude Code 的 `postinstall` 脚本未被允许，可以明确授权该包的安装脚本后重试：

```bash
npm install -g @anthropic-ai/claude-code \
  --allow-scripts=@anthropic-ai/claude-code
```

验证：

```bash
node --version
npm --version
command -v claude
claude --version
claude doctor
```

不要使用 `sudo npm install -g`。NVM 会把 Node.js 和 npm 安装到当前用户目录，新终端会从 `~/.bashrc` 加载默认 Node.js 版本。

如果临时需要绕过代理：

```bash
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
```

重新打开终端或执行 `source ~/.bashrc` 后，代理变量会再次生效。
